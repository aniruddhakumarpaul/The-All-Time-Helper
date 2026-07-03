from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import sqlite3
import json
import traceback
import time
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.repository import ChatRepository
from app.security import get_current_user, verify_admin_key
from app.logic.memory import query_memory, user_context, admin_auth_context
from app.logic.neural_explainer import explain_neural_context
from app.logic.agents import ask_the_helper
from app.logger import logger
from app.inference_queue import inference_queue
import asyncio
import threading
import queue
import uuid

router = APIRouter()


class ChatRequest(BaseModel):
    prompt: str
    history: List[dict] = []
    model: str = "gemma4:e2b"
    img: Optional[str] = None
    name: str = "Human"
    sys: dict = {}
    persona: bool = False
    isMasked: bool = False


class RetrieveRequest(BaseModel):
    text: str
    n: int = 3


@router.get("/get_chats")
def get_chats(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    chats_array = ChatRepository.get_chats_for_user(db, current_user)
    return {"success": True, "chats": chats_array}


@router.post("/sync_chats")
def sync_chats(chats: List[dict], current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        ChatRepository.sync_user_chats(db, current_user, chats)
        return {"success": True}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


@router.post("/retrieve_context")
def retrieve_context(req: RetrieveRequest, current_user: str = Depends(get_current_user)):
    token = user_context.set(current_user)
    try:
        results = query_memory(req.text, n_results=req.n)
        snippet_list = [r['content'] for r in results]
        explanation = explain_neural_context(req.text, snippet_list)
        return {
            "success": True,
            "results": results,
            "explanation": explanation
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}
    finally:
        user_context.reset(token)


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user)):
    target_model = req.model
    prompt = req.prompt
    img = req.img
    history = req.history
    sys_config = req.sys

    admin_key_value = None
    if req.isMasked:
        candidate_key = req.prompt.strip()
        if not verify_admin_key(candidate_key):
            async def invalid_key_stream():
                yield json.dumps({"message": {"content": "ERROR: AUTH_REQUIRED. Incorrect admin key."}, "done": True}).encode() + b'\n'
                yield json.dumps({"done": True}).encode() + b'\n'
            admin_auth_context.set(None)
            return StreamingResponse(invalid_key_stream(), media_type="application/x-ndjson")

        admin_key_value = candidate_key
        admin_auth_context.set(admin_key_value)
        prompt = "ADMIN_KEY_PROVIDED: The system has securely received and validated the Admin Key. Execute the pending sensitive tool request from the prior turn without exposing the key."
    else:
        admin_auth_context.set(None)

    abort_event = threading.Event()

    async def listen_for_disconnect():
        try:
            while not abort_event.is_set():
                if await request.is_disconnected():
                    abort_event.set()
                    logger.warning("[Chat] Client disconnected. Cancelling agent job.")
                    break
                await asyncio.sleep(2)
        except Exception:
            pass

    try:
        async def agent_stream():
            token = user_context.set(current_user)
            if admin_key_value:
                admin_auth_context.set(admin_key_value)
            listener_task = asyncio.create_task(listen_for_disconnect())
            try:
                yield json.dumps({"status": "Initializing Neural Core..."}).encode() + b'\n'
                await asyncio.sleep(0.5)

                if req.img:
                    yield json.dumps({"status": "Vision Pipeline Processing Image..."}).encode() + b'\n'
                else:
                    yield json.dumps({"status": "Scanning Semantic Memory..."}).encode() + b'\n'

                streaming_occurred = []
                status_queue = queue.Queue()

                def status_callback(msg):
                    logger.debug(f"[Chat] Status Update -> {msg}")
                    status_queue.put({"type": "status", "data": msg})

                def chunk_callback(token):
                    streaming_occurred.append(True)
                    status_queue.put({"type": "chunk", "data": token})

                _admin_key_for_thread = admin_key_value
                job_id = f"{current_user}_{uuid.uuid4().hex}"

                from app.logic.bus import job_id_context
                _job_id_for_thread = job_id

                def thread_target():
                    user_context.set(current_user)
                    job_id_context.set(_job_id_for_thread)
                    if _admin_key_for_thread:
                        admin_auth_context.set(_admin_key_for_thread)
                    return ask_the_helper(
                        prompt, img, target_model, sys_config, history, req.persona, abort_event, current_user,
                        status_callback=status_callback, chunk_callback=chunk_callback
                    )

                task = asyncio.create_task(inference_queue.submit(job_id, thread_target, abort_event, timeout=1500.0))

                while not task.done():
                    if abort_event.is_set():
                        break

                    while not status_queue.empty():
                        item = status_queue.get()
                        if item["type"] == "status":
                            yield json.dumps({"status": item["data"]}).encode() + b'\n'
                        elif item["type"] == "chunk":
                            yield json.dumps({"message": {"content": item["data"]}, "done": False}).encode() + b'\n'
                        await asyncio.sleep(0.01)

                    yield json.dumps({"hb": int(time.time())}).encode() + b'\n'
                    await asyncio.sleep(0.1)

                result = await task
                while not status_queue.empty():
                    item = status_queue.get()
                    if item["type"] == "status":
                        yield json.dumps({"status": item["data"]}).encode() + b'\n'
                    elif item["type"] == "chunk":
                        yield json.dumps({"message": {"content": item["data"]}, "done": False}).encode() + b'\n'

                if abort_event.is_set():
                    yield json.dumps({"message": {"content": "⚠️ *Request Cancelled.*"}, "done": True}).encode() + b'\n'
                else:
                    is_tool_res = result and result.strip() in ["SUCCESS", "ERROR", "AUTH_REQUIRED"]
                    if is_tool_res or not streaming_occurred:
                        yield json.dumps({"message": {"content": str(result)}, "done": True}).encode() + b'\n'

                yield json.dumps({"done": True}).encode() + b'\n'
            except Exception as e:
                logger.error(f"Agent Error: {str(e)}")
                yield json.dumps({"message": {"content": f"⚠️ **Agent Error:** {str(e)}"}, "done": True}).encode() + b'\n'
            finally:
                abort_event.set()
                listener_task.cancel()
                user_context.reset(token)

        return StreamingResponse(agent_stream(), media_type="application/x-ndjson")
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}
