from fastapi import APIRouter, Depends, Request, HTTPException, Body
from fastapi.responses import StreamingResponse, HTMLResponse
import sqlite3
import json
import traceback
import time
import re
import cv2
import numpy as np
import os
from pydantic import BaseModel
from typing import List, Optional, Any, Union
from app.database import get_db
from app.repository import ChatRepository
from app.security import get_current_user
from app.logic.memory import query_memory, user_context, admin_auth_context
from app.logic.neural_explainer import explain_neural_context
from app.logic.agents import ask_the_helper
from app.logger import logger
from app.inference_queue import inference_queue
import asyncio
import threading
import queue

router = APIRouter()

class SendEmailRequest(BaseModel):
    recipient: str
    subject: str
    body: str
    tone: str = "modern"
    attachment_content: Optional[str] = None
    attachment_filename: Optional[str] = "report.txt"
    attachments: Optional[List[dict]] = None
    admin_key: Optional[str] = None

class PreviewEmailRequest(BaseModel):
    body: str
    tone: str = "modern"


class ChatRequest(BaseModel):
    prompt: str
    history: List[dict] = []
    model: str = "gemma4:e2b"
    img: Optional[Union[str, List[str]]] = None
    name: str = "Human"
    sys: dict = {}
    persona: bool = False
    isMasked: bool = False

class RetrieveRequest(BaseModel):
    text: str
    n: int = 3


class CancelJobRequest(BaseModel):
    job_id: str

@router.get("/get_chats")
def get_chats(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    chats_array = ChatRepository.get_chats_for_user(db, current_user)
    return {"success": True, "chats": chats_array}

@router.post("/sync_chats")
def sync_chats(payload: Any = Body(...), current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        ChatRepository.sync_user_chats(db, current_user, payload)
        return {"success": True}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

@router.post("/retrieve_context")
def retrieve_context(req: RetrieveRequest, current_user: str = Depends(get_current_user)):
    # Set context for isolation
    token = user_context.set(current_user)
    try:
        results = query_memory(req.text, n_results=req.n)
        
        # New: Generate an AI explanation for the snippets
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

@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user)):
    # Decision Engine: Choose between Pro (Agentic) and Local (Ollama)
    target_model = req.model
    prompt = req.prompt
    img = req.img
    history = req.history
    sys_config = req.sys
    
    # NEW: Handle Admin Key securely via ContextVar, NOT in the LLM prompt
    # FIX #3: Also pass key explicitly to avoid ContextVar thread-propagation issues
    admin_key_value = None
    if req.isMasked:
        admin_key_value = req.prompt.strip()
        expected_admin_key = os.getenv("ADMIN_KEY")
        if expected_admin_key and admin_key_value != expected_admin_key:
            raise HTTPException(status_code=400, detail="Invalid Admin Key. Authorization failed.")
        # Securely store the key for the tool to access natively
        admin_auth_context.set(admin_key_value)
        
        # FIX: Persist admin auth to DB session so it survives retry loops
        try:
            from app.database import DB_FILE
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute("UPDATE users SET admin_authorized = 1 WHERE email = ?", (current_user,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to persist admin auth to DB: {e}")
            
        # The LLM gets a clear instruction to proceed without seeing the key
        prompt = "ADMIN_KEY_PROVIDED: The system has securely received the Admin Key and loaded it into the execution environment. You MUST now execute the send_email_tool immediately using the exact arguments from your previous attempt. DO NOT ask for the key again. DO NOT say you are ready, just execute the tool."
    else:
        # Ensure context is cleared for regular messages
        admin_auth_context.set(None)
    
    # Detect active agent early for UI badge rendering
    from app.logic.agents import _detect_intent, _reconstruct_contextual_prompt, clean_user_prompt, CODE_KEYWORDS, VISUAL_KEYWORDS, EMAIL_KEYWORDS
    resolved_prompt = _reconstruct_contextual_prompt(prompt, history)
    intent = _detect_intent(resolved_prompt, target_model, history)
    
    # Scan the cleaned prompt instead of raw prompt with attached contexts for badge mapping
    clean_p = clean_user_prompt(resolved_prompt)
    p_lower = clean_p.lower()
    needs_code = any(kw in p_lower for kw in CODE_KEYWORDS)
    needs_visual = any(kw in p_lower for kw in VISUAL_KEYWORDS)
    needs_email = any(kw in p_lower for kw in EMAIL_KEYWORDS)

    if needs_email:
        active_agent = "manager" if intent.get("complexity") == "swarm" else "secretary"
    elif needs_code:
        active_agent = "developer"
    elif needs_visual:
        active_agent = "artist"
    else:
        active_agent = "generalist"

    # New Unified Agentic Flow (Supports both Cloud & Local with Tools)
    abort_event = threading.Event()
    job_id = f"{current_user}_{int(time.time())}"

    async def listen_for_disconnect():
        try:
            while not abort_event.is_set():
                if await request.is_disconnected():
                    abort_event.set()
                    logger.warning("[Chat] Client Disconnected. Killing Agent Thread.")
                    break
                await asyncio.sleep(2)
        except Exception: pass

    try:
        async def agent_stream():
            # Set User context for the generator thread
            token = user_context.set(current_user)
            # FIX #3: Propagate admin key into the thread explicitly
            if admin_key_value:
                admin_auth_context.set(admin_key_value)
            # Start disconnect listener
            listener_task = asyncio.create_task(listen_for_disconnect())
            try:
                # Immediate feedback for the user (only if it needs tools, vision, or RAG)
                # For casual direct talk, bypass status loading to keep UI response instantaneous.
                p_lower = prompt.lower()
                rag_triggers = ["architecture", "code", "function", "file", "logic", "decide", "decision", "plan", "why did", "project", "helper", "memory", "database", "implement", "design"]
                needs_rag = not intent.get("requires_tools") and any(tg in p_lower for tg in rag_triggers)
                
                if intent.get("requires_tools") or req.img or needs_rag:
                    yield json.dumps({"status": "Initializing Neural Core...", "active_agent": active_agent, "job_id": job_id}).encode() + b'\n'
                    if req.img:
                        yield json.dumps({"status": "Vision Pipeline Processing Image...", "active_agent": active_agent, "job_id": job_id}).encode() + b'\n'
                    else:
                        yield json.dumps({"status": "Scanning Semantic Memory...", "active_agent": active_agent, "job_id": job_id}).encode() + b'\n'

                # Track if streaming occurred to prevent duplicate final yields
                streaming_occurred = []

                # Status & Chunk Queue for thread communication
                status_queue = queue.Queue()

                def status_callback(msg):
                    logger.debug(f"[Chat] Status Update -> {msg}")
                    status_queue.put({"type": "status", "data": msg})

                def chunk_callback(token):
                    streaming_occurred.append(True)
                    status_queue.put({"type": "chunk", "data": token})

                # FIX #3: Capture admin key for thread-safe propagation
                _admin_key_for_thread = admin_key_value

                # FIX: Propagate job_id for ToolResultBus tracking
                from app.logic.bus import job_id_context
                _job_id_for_thread = job_id

                def thread_target():
                    # Propagate ContextVars into worker thread
                    user_context.set(current_user)
                    job_id_context.set(_job_id_for_thread)
                    if _admin_key_for_thread:
                        admin_auth_context.set(_admin_key_for_thread)
                    return ask_the_helper(
                        prompt, img, target_model, sys_config, history, req.persona, abort_event, current_user,
                        status_callback=status_callback, chunk_callback=chunk_callback, intent=intent
                    )

                task = asyncio.create_task(inference_queue.submit(job_id, thread_target, abort_event, timeout=1500.0, owner=current_user))

                # HEARTBEAT LOOP: Yield status/chunks from queue
                while not task.done():
                    if abort_event.is_set():
                        break
                    
                    # Drain the queue
                    while not status_queue.empty():
                        item = status_queue.get()
                        if item["type"] == "status":
                            yield json.dumps({"status": item["data"], "active_agent": active_agent, "job_id": job_id}).encode() + b'\n'
                        elif item["type"] == "chunk":
                            # Use 'message' key for character streaming
                            logger.debug(f"DEBUG: Yielding chunk to stream: '{item['data']}'")
                            yield json.dumps({"message": {"content": item["data"]}, "done": False}).encode() + b'\n'
                        await asyncio.sleep(0.01)

                    # Heartbeat to keep connection alive and force flush
                    yield json.dumps({"hb": int(time.time())}).encode() + b'\n'
                    await asyncio.sleep(0.1) 
                
                if abort_event.is_set():
                    inference_queue.cancel(job_id, current_user)
                    while not status_queue.empty():
                        status_queue.get()
                    yield json.dumps({"message": {"content": "âš ï¸ *Request Cancelled.*"}, "done": True}).encode() + b'\n'
                    yield json.dumps({"done": True}).encode() + b'\n'
                    return

                result = await task
                # Final check for any last status/chunks
                while not status_queue.empty():
                    item = status_queue.get()
                    if item["type"] == "status":
                        yield json.dumps({"status": item["data"], "active_agent": active_agent, "job_id": job_id}).encode() + b'\n'
                    elif item["type"] == "chunk":
                        yield json.dumps({"message": {"content": item["data"]}, "done": False}).encode() + b'\n'

                if abort_event.is_set():
                    yield json.dumps({"message": {"content": "⚠️ *Request Cancelled.*"}, "done": True}).encode() + b'\n'
                else:
                    # Only yield the final result block if it's a tool output or if NO streaming happened
                    result_str = str(result).strip() if result else ""
                    is_tool_res = (
                        result_str in ["SUCCESS", "ERROR", "AUTH_REQUIRED"] or
                        result_str.startswith("ERROR:") or
                        "EMAIL_DRAFT_PAYLOAD:" in result_str or
                        "SIMULATE SUCCESS:" in result_str or
                        "LIVE SUCCESS:" in result_str or
                        "ALREADY SENT:" in result_str or
                        ("![" in result_str and "](" in result_str)
                    )
                    if is_tool_res or not streaming_occurred:
                        yield json.dumps({"message": {"content": str(result)}, "done": True}).encode() + b'\n'
                
                yield json.dumps({"done": True}).encode() + b'\n'
            except Exception as e:
                logger.error(f"Agent Error: {str(e)}")
                yield json.dumps({"message": {"content": f"⚠️ **Agent Error:** {str(e)}"}, "done": True}).encode() + b'\n'
            finally:
                abort_event.set() # Ensure listener stops
                listener_task.cancel()
        
        return StreamingResponse(agent_stream(), media_type="application/x-ndjson")
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}


@router.post("/cancel_chat_job")
def cancel_chat_job(req: CancelJobRequest, current_user: str = Depends(get_current_user)):
    try:
        cancelled = inference_queue.cancel(req.job_id, current_user)
        return {"success": bool(cancelled), "cancelled": bool(cancelled)}
    except Exception as e:
        logger.error(f"Error cancelling chat job {req.job_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

@router.post("/api/send_email_direct")
def send_email_direct(req: SendEmailRequest, current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    # 1. Admin auth check
    user_row = db.execute("SELECT admin_authorized FROM users WHERE email = ?", (current_user,)).fetchone()
    authorized = user_row and user_row[0]
    
    if not authorized:
        if req.admin_key:
            expected_key = os.getenv("ADMIN_KEY")
            if expected_key and req.admin_key.strip() == expected_key.strip():
                db.execute("UPDATE users SET admin_authorized = 1 WHERE email = ?", (current_user,))
                db.commit()
            else:
                raise HTTPException(status_code=403, detail="Unauthorized: Invalid Admin Key.")
        else:
            raise HTTPException(status_code=403, detail="Unauthorized: Admin key required to send emails.")

    # 2. Invoke send_or_simulate_email
    job_id = f"direct_{current_user}_{int(time.time())}"
    
    try:
        from app.logic.tools import send_or_simulate_email
        result = send_or_simulate_email(
            recipient=req.recipient,
            subject=req.subject,
            body=req.body,
            tone=req.tone,
            attachment_content=req.attachment_content,
            attachment_filename=req.attachment_filename,
            attachments=req.attachments
        )
        
        # Determine status
        status = "SUCCESS" if ("SUCCESS" in result) else "FAILED"
        
        # 3. Persist log to email_send_log
        db.execute(
            "INSERT OR REPLACE INTO email_send_log (job_id, user_email, recipients, status, timestamp) VALUES (?, ?, ?, ?, ?)",
            (job_id, current_user, req.recipient, status, time.time())
        )
        db.commit()
        
        if status == "FAILED" or "ERROR" in result:
            return {"success": False, "error": result}
            
        return {"success": True, "result": result}
        
    except Exception as e:
        logger.error(f"Error in direct email send: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

@router.post("/api/render_email_preview", response_class=HTMLResponse)
def render_email_preview(req: PreviewEmailRequest, current_user: str = Depends(get_current_user)):
    try:
        from app.logic.tools import _build_html_body
        html = _build_html_body(req.body, req.tone)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.error(f"Error rendering email preview: {e}")
        return HTMLResponse(content=f"<h3>Error rendering preview: {str(e)}</h3>", status_code=500)
