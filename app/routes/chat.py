import asyncio
import json
import queue
import sqlite3
import threading
import time
import traceback
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.database import get_db
from app.inference_queue import inference_queue
from app.logger import logger
from app.logic.agents import ask_the_helper
from app.logic.attachment_store import AttachmentStoreError, MAX_ATTACHMENT_BYTES, save_attachment_bytes
from app.logic.email_draft_image_workflow import build_email_draft_body_update_payload_from_history
from app.logic.memory import admin_auth_context, query_memory, user_context
from app.logic.neural_explainer import explain_neural_context
from app.repository import ChatRepository
from app.security import get_current_user, verify_admin_key
from app.services.email_widget_intercept import (
    _email_widget_message,
    _email_widget_ndjson,
    _is_email_widget_attachment_request,
    _latest_image_email_draft,
)

router = APIRouter()


class Attachment(BaseModel):
    id: Optional[str] = None
    name: str = "attachment.png"
    type: str = "image/png"
    size: Optional[int] = None
    data: Optional[str] = None


class ChatRequest(BaseModel):
    prompt: str
    history: List[dict] = Field(default_factory=list)
    model: str = "gemma4:e2b"
    img: Optional[Any] = None
    attachments: List[Attachment] = Field(default_factory=list)
    name: str = "Human"
    sys: dict = Field(default_factory=dict)
    persona: bool = False
    isMasked: bool = False


class RetrieveRequest(BaseModel):
    text: str
    n: int = 3


def _new_job_id() -> str:
    return str(uuid.uuid4())


def _normalize_chat_image_payload(req: ChatRequest):
    if req.attachments:
        return [item.model_dump(exclude_none=True) for item in req.attachments]
    return req.img


def _message_role(message: dict) -> str:
    return str(message.get("role") or message.get("r") or "").lower()


def _message_content(message: dict) -> str:
    return str(message.get("content") or message.get("c") or "")


def _looks_like_auth_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return "auth_required" in lowered or "admin key" in lowered or "incorrect admin key" in lowered


def _find_pending_sensitive_request(history: list[dict]) -> str:
    for message in reversed(history or []):
        role = _message_role(message)
        content = _message_content(message).strip()
        if not content:
            continue
        if message.get("masked"):
            continue
        if role in {"assistant", "a", "bot", "b"}:
            continue
        if _looks_like_auth_error(content):
            continue
        if len(content) < 25 and (content.isalnum() or "admin" in content.lower()):
            continue
        return content
    return ""


@router.get("/get_chats")
def get_chats(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    chats_array = ChatRepository.get_chats_for_user(db, current_user)
    return {"success": True, "chats": chats_array}


@router.post("/attachments")
async def upload_attachments(
    files: List[UploadFile] = File(...),
    current_user: str = Depends(get_current_user),
):
    saved = []
    try:
        for upload in files[:6]:
            data = await upload.read(MAX_ATTACHMENT_BYTES + 1)
            saved.append(save_attachment_bytes(upload.filename or "attachment", upload.content_type or "", data, current_user))
        return {"success": True, "attachments": saved}
    except AttachmentStoreError as exc:
        return {"success": False, "error": str(exc)}


@router.post("/sync_chats")
def sync_chats(chats: list[dict] | dict, current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        ChatRepository.sync_user_chats(db, current_user, chats)
        return {"success": True}
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "error": str(exc)}


@router.post("/chat/jobs/{job_id}/cancel")
async def cancel_chat_job(job_id: str, current_user: str = Depends(get_current_user)):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return {"success": False, "error": "Job not found"}
    cancelled = inference_queue.cancel(job_id, current_user)
    return {"success": cancelled, **({} if cancelled else {"error": "Job not found"})}


@router.post("/retrieve_context")
def retrieve_context(req: RetrieveRequest, current_user: str = Depends(get_current_user)):
    for marker in ("EMAIL_DRAFT_CONTEXT:", "EMAIL_DRAFT_PAYLOAD:"):
        if marker in req.text:
            try:
                raw = req.text.split(marker, 1)[1].strip()
                draft, _ = json.JSONDecoder().raw_decode(raw)
                return {"success": True, "kind": "email_draft", "draft": draft, "results": [], "explanation": ""}
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "error": "Invalid email draft context"}
    token = user_context.set(current_user)
    try:
        results = query_memory(req.text, n_results=req.n)
        snippet_list = [r['content'] for r in results]
        explanation = explain_neural_context(req.text, snippet_list)
        return {
            "success": True,
            "results": results,
            "explanation": explanation,
        }
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "error": str(exc)}
    finally:
        user_context.reset(token)


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user)):
    target_model = req.model
    prompt = req.prompt
    img = _normalize_chat_image_payload(req)
    has_visual_input = bool(img)
    history = req.history
    sys_config = req.sys

    if not req.isMasked:
        body_update = build_email_draft_body_update_payload_from_history(prompt, history, logger=logger)
        if body_update:
            logger.info("[EmailWidget] Routed targeted body update inside chat endpoint before image shortcut.")
            return Response(content=_email_widget_ndjson(body_update), media_type="application/x-ndjson")

    if not req.isMasked and _is_email_widget_attachment_request(prompt):
        try:
            draft = _latest_image_email_draft(history)
            message = _email_widget_message(draft)
            logger.info("[EmailWidget] Routed latest image attachment request inside chat endpoint.")
            return Response(content=_email_widget_ndjson(message), media_type="application/x-ndjson")
        except Exception as exc:
            logger.warning(f"[EmailWidget] Route shortcut failed, continuing normal chat flow: {exc}")

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
        pending_request = _find_pending_sensitive_request(history)
        if pending_request:
            prompt = "APPROVAL_CONFIRMED. Continue this pending sensitive request:\n\n" + pending_request
        else:
            prompt = "APPROVAL_CONFIRMED, but no pending sensitive request was found. Ask the user to repeat the action request."
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
        job_id = _new_job_id()

        async def agent_stream():
            token = user_context.set(current_user)
            admin_token = None
            if admin_key_value:
                admin_token = admin_auth_context.set(admin_key_value)
            listener_task = asyncio.create_task(listen_for_disconnect())
            try:
                yield json.dumps({"job_id": job_id}).encode() + b'\n'
                yield json.dumps({"status": "Initializing Neural Core..."}).encode() + b'\n'
                await asyncio.sleep(0.5)

                if has_visual_input:
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
                from app.logic.bus import job_id_context
                _job_id_for_thread = job_id

                def thread_target():
                    user_context.set(current_user)
                    job_id_context.set(_job_id_for_thread)
                    if _admin_key_for_thread:
                        admin_auth_context.set(_admin_key_for_thread)
                    return ask_the_helper(
                        prompt, img, target_model, sys_config, history, req.persona, abort_event, current_user,
                        status_callback=status_callback, chunk_callback=chunk_callback,
                    )

                task = asyncio.create_task(
                    inference_queue.submit(
                        job_id,
                        thread_target,
                        abort_event,
                        timeout=1500.0,
                        owner=current_user,
                    )
                )

                while not task.done():
                    if abort_event.is_set():
                        inference_queue.cancel(job_id, current_user)
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
            except asyncio.CancelledError:
                abort_event.set()
                inference_queue.cancel(job_id, current_user)
                raise
            except GeneratorExit:
                abort_event.set()
                inference_queue.cancel(job_id, current_user)
                raise
            except Exception as exc:
                logger.error(f"Agent Error: {str(exc)}")
                yield json.dumps({"message": {"content": f"⚠️ **Agent Error:** {str(exc)}"}, "done": True}).encode() + b'\n'
            finally:
                abort_event.set()
                listener_task.cancel()
                if admin_token is not None:
                    try:
                        admin_auth_context.reset(admin_token)
                    except ValueError:
                        logger.debug("[Chat] Admin context reset skipped after stream context switch.")
                try:
                    user_context.reset(token)
                except ValueError:
                    logger.debug("[Chat] User context reset skipped after stream context switch.")

        return StreamingResponse(agent_stream(), media_type="application/x-ndjson")
    except Exception as exc:
        traceback.print_exc()
        return {"error": str(exc)}
