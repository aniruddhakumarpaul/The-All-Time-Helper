import asyncio
import json
import queue
import sqlite3
import threading
import time
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.database import get_db
from app.inference_queue import inference_queue
from app.logger import logger
from app.logic.agents import ask_the_helper, is_deterministic_tool_lane_request
from app.logic.attachment_store import (
    AttachmentStoreError,
    MAX_ATTACHMENT_BYTES,
    extract_attachment_text,
    resolve_attachment_reference,
    save_attachment_bytes,
)
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
    prompt: str = Field(max_length=100_000)
    history: List[dict] = Field(default_factory=list, max_length=200)
    model: str = "helper-auto"
    img: Optional[Any] = None
    attachments: List[Attachment] = Field(default_factory=list, max_length=6)
    name: str = "Human"
    sys: dict = Field(default_factory=dict)
    persona: bool = False
    isMasked: bool = False


class RetrieveRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    n: int = Field(default=3, ge=1, le=10)


def _new_job_id() -> str:
    return str(uuid.uuid4())


def _normalize_chat_image_payload(req: ChatRequest):
    if req.attachments:
        return [item.model_dump(exclude_none=True) for item in req.attachments]
    return req.img


def _is_visual_attachment(item: Any) -> bool:
    """Keep document uploads out of the vision lane while preserving legacy images."""
    if not isinstance(item, dict):
        return bool(item)
    content_type = str(item.get("content_type") or item.get("type") or "").lower()
    if content_type:
        return content_type.startswith("image/")
    filename = str(item.get("filename") or item.get("name") or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) or bool(
        item.get("content") or item.get("data") or item.get("attachment_content") or item.get("path")
    )

def _hydrate_current_image_payload(payload: Any, owner: str):
    if not payload:
        return payload
    was_list = isinstance(payload, list)
    items = payload if was_list else [payload]
    hydrated = [
        resolve_attachment_reference(item, owner)
        if isinstance(item, dict) and item.get("id")
        else item
        for item in items
    ]
    return hydrated if was_list else hydrated[0]


def _hydrate_history_attachment_references(history: List[dict], owner: str, max_lookups: int = 6) -> List[dict]:
    hydrated = [dict(message) if isinstance(message, dict) else message for message in history or []]
    remaining = max_lookups
    for index in range(len(hydrated) - 1, -1, -1):
        if remaining <= 0 or not isinstance(hydrated[index], dict):
            continue
        attachments = hydrated[index].get("attachments")
        if not attachments:
            continue
        items = attachments if isinstance(attachments, list) else [attachments]
        resolved_items = []
        for item in items:
            if isinstance(item, dict) and item.get("id") and remaining > 0:
                remaining -= 1
                try:
                    resolved_items.append(resolve_attachment_reference(item, owner))
                except AttachmentStoreError:
                    logger.info("[Attachments] Historical attachment is unavailable or expired.")
                continue
            resolved_items.append(item)
        hydrated[index]["attachments"] = resolved_items
    return hydrated


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
    if len(files) > 6:
        raise HTTPException(status_code=400, detail="Attach no more than 6 files at once.")
    saved = []
    try:
        for upload in files:
            data = await upload.read(MAX_ATTACHMENT_BYTES + 1)
            saved.append(save_attachment_bytes(upload.filename or "attachment", upload.content_type or "", data, current_user))
        return {"success": True, "attachments": saved}
    except AttachmentStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/sync_chats")
def sync_chats(chats: list[dict] | dict, current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        ChatRepository.sync_user_chats(db, current_user, chats)
        return {"success": True}
    except Exception as exc:
        logger.exception("[ChatSync] Failed to persist conversations for %s", current_user)
        raise HTTPException(status_code=500, detail="Conversations could not be synced.") from exc

@router.post("/chat/jobs/{job_id}/cancel")
async def cancel_chat_job(job_id: str, current_user: str = Depends(get_current_user)):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    if not inference_queue.cancel(job_id, current_user):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}

@router.post("/retrieve_context")
def retrieve_context(req: RetrieveRequest, current_user: str = Depends(get_current_user)):
    for marker in ("EMAIL_DRAFT_CONTEXT:", "EMAIL_DRAFT_PAYLOAD:"):
        if marker in req.text:
            try:
                raw = req.text.split(marker, 1)[1].strip()
                draft, _ = json.JSONDecoder().raw_decode(raw)
                return {"success": True, "kind": "email_draft", "draft": draft, "results": [], "explanation": ""}
            except (json.JSONDecodeError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid email draft context")
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
        logger.exception("[Memory] Related context retrieval failed for %s", current_user)
        raise HTTPException(status_code=503, detail="Related context is temporarily unavailable.") from exc
    finally:
        user_context.reset(token)


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user)):
    target_model = req.model
    prompt = req.prompt
    try:
        img = _hydrate_current_image_payload(_normalize_chat_image_payload(req), current_user)
        history = _hydrate_history_attachment_references(req.history, current_user)
    except AttachmentStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    document_blocks = []
    for item in (img if isinstance(img, list) else ([img] if img else [])):
        if isinstance(item, dict):
            extracted = extract_attachment_text(item)
            if extracted:
                filename = item.get("filename") or item.get("name") or "document"
                document_blocks.append(f"--- ATTACHED DOCUMENT: {filename} ---\n{extracted}\n--- END ATTACHED DOCUMENT ---")
    if document_blocks:
        prompt = prompt + "\n\n" + "\n\n".join(document_blocks)
    image_items = img if isinstance(img, list) else ([img] if img else [])
    has_visual_input = any(_is_visual_attachment(item) for item in image_items)
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

    use_tool_lane = bool(
        not req.isMasked
        and not req.persona
        and not has_visual_input
        and is_deterministic_tool_lane_request(prompt, history)
    )
    execution_lane = "tool" if use_tool_lane else "inference"
    direct_tool_intent = (
        {
            "is_sensitive": False,
            "requires_tools": True,
            "complexity": "single",
            "is_local": False,
            "force_direct_tool": True,
        }
        if use_tool_lane
        else None
    )
    attachment_items = image_items if isinstance(image_items, list) else []
    document_count = len(document_blocks)
    visual_count = sum(1 for item in attachment_items if _is_visual_attachment(item))
    bounded_attachment_bytes = sum(
        min(int(item.get("size") or 0), MAX_ATTACHMENT_BYTES)
        for item in attachment_items if isinstance(item, dict)
    )
    logger.info(
        "[ChatTrace] lane=%s attachments=%d documents=%d visuals=%d bounded_bytes=%d",
        execution_lane, len(attachment_items), document_count, visual_count, bounded_attachment_bytes,
    )

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
                yield json.dumps({"status": "Starting your request..."}).encode() + b'\n'
                await asyncio.sleep(0.5)

                if has_visual_input:
                    yield json.dumps({"status": "Reading the attached image..."}).encode() + b'\n'
                elif img:
                    yield json.dumps({"status": "Reading the attached document..."}).encode() + b'\n'
                else:
                    yield json.dumps({"status": "Checking relevant context..."}).encode() + b'\n'
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
                        status_callback=status_callback,
                        chunk_callback=chunk_callback,
                        intent=direct_tool_intent,
                    )

                task = asyncio.create_task(
                    inference_queue.submit(
                        job_id,
                        thread_target,
                        abort_event,
                        timeout=1500.0,
                        owner=current_user,
                        lane=execution_lane,
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
                    logger.info("[ChatTrace] job=%s lane=%s state=cancelled reason=client_disconnect_or_stop", job_id, execution_lane)
                    yield json.dumps({"message": {"content": "Request cancelled."}, "done": True}).encode() + b'\n'
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
            except Exception:
                logger.exception("[Chat] Assistant task failed for job %s", job_id)
                yield json.dumps({
                    "message": {
                        "content": "I could not complete that response. Please retry or choose another route."
                    },
                    "done": True,
                }).encode() + b'\n'
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
        logger.exception("[Chat] Failed to start assistant request")
        raise HTTPException(status_code=500, detail="The assistant request could not be started.") from exc
