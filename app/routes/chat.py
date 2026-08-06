import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.database import classify_sqlite_error, get_db, is_transient_sqlite_error
from app.inference_queue import inference_queue
from app.logic.chat_job_registry import chat_job_registry
from app.logger import logger
from app.logic.agents import ask_the_helper, is_deterministic_tool_lane_request
from app.logic.attachment_store import (
    AttachmentStoreError,
    MAX_ATTACHMENT_BYTES,
    extract_attachment_text,
    resolve_attachment_metadata,
    resolve_attachment_reference,
    save_attachment_bytes,
)
from app.logic.email_draft_image_workflow import build_email_draft_body_update_payload_from_history
from app.logic.memory import query_memory, user_context
from app.logic.agent_intent import is_compound_email_media_request
from app.logic.neural_explainer import explain_neural_context
from app.logic.workflow_orchestrator import execute_workflow_for_chat, plan_known_workflow, resolve_workflow_context
from app.repository import ChatRepository
from app.security import get_current_user
from app.services.email_widget_intercept import (
    _email_widget_message,
    _email_widget_ndjson,
    _is_email_widget_attachment_request,
    _latest_image_email_draft,
)

router = APIRouter()

NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _inline_ndjson_response(body: str) -> JSONResponse:
    return JSONResponse({"success": True, "inline_ndjson": body}, headers=NO_STORE_HEADERS)


def _finalize_event(event: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    event = dict(event)
    if event.get("final"):
        event["content"] = snapshot.get("content", "")
        if isinstance(event.get("message"), dict):
            message = dict(event["message"])
            message["content"] = snapshot.get("content", "")
            event["message"] = message
    return event


async def _stream_job_events(job_id: str, owner: str, after: int = 0):
    last_sequence = max(0, int(after))
    while True:
        snapshot = chat_job_registry.snapshot(job_id, owner, after=last_sequence)
        if not snapshot:
            yield json.dumps({"error": "Task not found", "done": True}).encode() + b"\n"
            return
        saw_final = False
        for item in snapshot["events"]:
            last_sequence = max(last_sequence, int(item["seq"]))
            event = _finalize_event(item["event"], snapshot)
            saw_final = saw_final or bool(event.get("final") or (event.get("done") and snapshot["status"] in {"completed", "failed", "cancelled"}))
            yield json.dumps(event).encode() + b"\n"
        if snapshot["status"] in {"completed", "failed", "cancelled"}:
            if not saw_final and last_sequence < int(snapshot["next_seq"]):
                yield json.dumps({
                    "final": True,
                    "status": snapshot["status"],
                    "content": snapshot.get("content", ""),
                    "done": True,
                }).encode() + b"\n"
            return
        await asyncio.sleep(0.1)

async def _legacy_job_stream(job_id: str, owner: str):
    yield json.dumps({"job_id": job_id}).encode() + b"\n"
    async for chunk in _stream_job_events(job_id, owner, after=0):
        try:
            event = json.loads(chunk)
        except (TypeError, json.JSONDecodeError):
            yield chunk
            continue
        if event.get("final"):
            message = event.get("message") if isinstance(event.get("message"), dict) else {
                "content": event.get("content", "")
            }
            yield json.dumps({"message": message, "done": True}).encode() + b"\n"
            yield json.dumps({"done": True}).encode() + b"\n"
            return
        yield chunk


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




@router.get("/get_chats")
def get_chats(current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        chats_array = ChatRepository.get_chats_for_user(db, current_user)
        return {"success": True, "chats": chats_array}
    except sqlite3.DatabaseError as exc:
        logger.error(
            "[ChatSync] failure_category=%s operation=get_chats",
            classify_sqlite_error(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="Conversations are temporarily unavailable.",
        ) from exc

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

@router.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: str, current_user: str = Depends(get_current_user)):
    try:
        metadata = resolve_attachment_metadata(attachment_id, current_user)
    except AttachmentStoreError as exc:
        raise HTTPException(status_code=404, detail="Attachment unavailable.") from exc
    return FileResponse(
        metadata["path"],
        media_type=metadata.get("content_type") or "application/octet-stream",
        filename=metadata.get("filename") or metadata.get("name") or "attachment",
        headers={"Cache-Control": "private, no-store"},
    )
@router.post("/sync_chats")
def sync_chats(chats: list[dict] | dict, current_user: str = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    for attempt in range(2):
        try:
            ChatRepository.sync_user_chats(db, current_user, chats)
            return {"success": True}
        except ValueError as exc:
            try:
                db.rollback()
            except sqlite3.DatabaseError:
                pass
            raise HTTPException(status_code=400, detail="Invalid conversation sync payload.") from exc
        except sqlite3.DatabaseError as exc:
            category = classify_sqlite_error(exc)
            try:
                db.rollback()
            except sqlite3.DatabaseError:
                pass
            if attempt == 0 and is_transient_sqlite_error(exc):
                time.sleep(0.05)
                continue
            logger.error("[ChatSync] failure_category=%s operation=sync_chats", category)
            raise HTTPException(status_code=500, detail="Conversations could not be synced.") from exc
        except Exception as exc:
            try:
                db.rollback()
            except sqlite3.DatabaseError:
                pass
            logger.error("[ChatSync] failure_category=database_unknown operation=sync_chats")
            raise HTTPException(status_code=500, detail="Conversations could not be synced.") from exc
    raise HTTPException(status_code=500, detail="Conversations could not be synced.")
@router.get("/chat/jobs/{job_id}")
async def get_chat_job(
    job_id: str,
    after: int = 0,
    current_user: str = Depends(get_current_user),
):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    snapshot = chat_job_registry.snapshot(job_id, current_user, after=after)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse({"success": True, **snapshot}, headers=NO_STORE_HEADERS)

@router.post("/chat/jobs/{job_id}/cancel")
async def cancel_chat_job(job_id: str, current_user: str = Depends(get_current_user)):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    queue_cancelled = inference_queue.cancel(job_id, current_user)
    registry_cancelled = chat_job_registry.cancel(job_id, current_user)
    if not queue_cancelled and not registry_cancelled:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse({"success": True}, headers=NO_STORE_HEADERS)

@router.get("/chat/jobs/{job_id}/events")
async def stream_chat_job_events(job_id: str, after: int = 0, current_user: str = Depends(get_current_user)):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    if not chat_job_registry.snapshot(job_id, current_user, after=after):
        raise HTTPException(status_code=404, detail="Task not found")
    return StreamingResponse(_stream_job_events(job_id, current_user, after), media_type="application/x-ndjson", headers=NO_STORE_HEADERS)

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
        logger.error("[Memory] Related context retrieval failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Related context is temporarily unavailable.") from exc
    finally:
        user_context.reset(token)


async def _chat_endpoint_impl(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user), *, create_only: bool = False):
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

    workflow_admin_key = req.prompt.strip() if req.isMasked else None
    workflow_candidate = (not req.isMasked) and is_compound_email_media_request(prompt)
    workflow_context = resolve_workflow_context(prompt, history) if workflow_candidate else None
    has_prompt_draft_marker = any(marker in prompt for marker in ("EMAIL_DRAFT_CONTEXT:", "EMAIL_DRAFT_PAYLOAD:"))
    has_history_draft_marker = any(
        marker in _message_content(message)
        for message in history
        if isinstance(message, dict)
        for marker in ("EMAIL_DRAFT_CONTEXT:", "EMAIL_DRAFT_PAYLOAD:")
    )
    workflow_plan = (
        plan_known_workflow(
            "" if req.isMasked else prompt,
            history,
            current_user,
            is_masked=req.isMasked,
        )
        if req.isMasked or (not document_blocks and not has_visual_input)
        else None
    )
    route_intent = getattr(getattr(workflow_plan, "intent", None), "value", None)
    if not route_intent:
        route_intent = "generate_image" if workflow_candidate else "unknown"
    route_fallback = "workflow" if workflow_plan is not None else "clarification" if workflow_candidate else "inference"
    logger.info(
        "[WorkflowRoute] candidate=%s has_prompt_draft_marker=%s has_history_draft_marker=%s active_draft_resolved=%s intent=%s plan_created=%s fallback=%s prompt_chars=%d history_messages=%d",
        str(workflow_candidate).lower(),
        str(has_prompt_draft_marker).lower(),
        str(has_history_draft_marker).lower(),
        str(bool(workflow_context and workflow_context.active_draft)).lower(),
        route_intent,
        str(workflow_plan is not None).lower(),
        route_fallback,
        min(len(prompt), 100000),
        min(len(history), 200),
    )

    if req.isMasked and workflow_plan is None:
        body = (json.dumps({"message": {"content": "No pending email delivery was found. Reopen the draft and request delivery again."}, "done": True}) + "\n" + json.dumps({"done": True}) + "\n")
        if create_only:
            return _inline_ndjson_response(body)
        async def inline_stream():
            yield body.encode()
        return StreamingResponse(inline_stream(), media_type="application/x-ndjson", headers=NO_STORE_HEADERS)

    if workflow_candidate and workflow_plan is None:
        body = _email_widget_ndjson("I could not identify the email draft to update. Open the draft or attach it to the prompt, then retry.")
        return _inline_ndjson_response(body) if create_only else Response(content=body, media_type="application/x-ndjson", headers=NO_STORE_HEADERS)

    if workflow_plan is None:
        if not req.isMasked:
            body_update = build_email_draft_body_update_payload_from_history(prompt, history, logger=logger)
            if body_update:
                logger.info("[EmailWidget] Routed targeted body update inside chat endpoint before image shortcut.")
                if create_only:
                    return _inline_ndjson_response(_email_widget_ndjson(body_update))
                legacy_response = Response(content=_email_widget_ndjson(body_update), media_type="application/x-ndjson")
                legacy_response.headers.update(NO_STORE_HEADERS)
                return legacy_response

        if not req.isMasked and _is_email_widget_attachment_request(prompt):
            try:
                draft = _latest_image_email_draft(history)
                message = _email_widget_message(draft)
                logger.info("[EmailWidget] Routed latest image attachment request inside chat endpoint.")
                if create_only:
                    return _inline_ndjson_response(_email_widget_ndjson(message))
                legacy_response = Response(content=_email_widget_ndjson(message), media_type="application/x-ndjson")
                legacy_response.headers.update(NO_STORE_HEADERS)
                return legacy_response
            except Exception as exc:
                logger.warning("[EmailWidget] Route shortcut failed, continuing normal chat flow (%s)", type(exc).__name__)

    use_tool_lane = bool(
        workflow_plan is not None
        or (
            not req.isMasked
            and not req.persona
            and not has_visual_input
            and is_deterministic_tool_lane_request(prompt, history)
        )
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
        if use_tool_lane and workflow_plan is None
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

    try:
        job_id = _new_job_id()
        chat_job_registry.create(job_id, current_user, abort_event)

        watch_task = None

        async def cancellation_watcher():
            while True:
                snapshot = chat_job_registry.snapshot(job_id, current_user)
                if not snapshot or snapshot["status"] in {"completed", "failed", "cancelled"}:
                    return
                if snapshot.get("cancel_requested"):
                    abort_event.set()
                    return
                await asyncio.sleep(0.4)

        async def run_job():
            token = user_context.set(current_user)
            streamed_parts: list[str] = []
            try:
                chat_job_registry.publish(job_id, current_user, {"status": "Starting your request..."})
                if has_visual_input:
                    chat_job_registry.publish(job_id, current_user, {"status": "Reading the attached image..."})
                elif img:
                    chat_job_registry.publish(job_id, current_user, {"status": "Reading the attached document..."})
                else:
                    chat_job_registry.publish(job_id, current_user, {"status": "Checking relevant context..."})

                def status_callback(msg):
                    logger.debug("[Chat] Status Update (chars=%d)", len(str(msg)))
                    chat_job_registry.publish(job_id, current_user, {"status": str(msg)[:4000]})

                def chunk_callback(value):
                    chunk = str(value or "")
                    if chunk:
                        streamed_parts.append(chunk)
                        chat_job_registry.publish(job_id, current_user, {"message": {"content": chunk[:12000]}, "done": False})

                from app.logic.bus import job_id_context
                def thread_target():
                    user_context.set(current_user)
                    job_id_context.set(job_id)
                    test_delay = max(0.0, float(os.getenv("CHAT_JOB_TEST_DELAY_SECONDS", "0") or 0))
                    if test_delay and prompt.startswith("__test_delay__"):
                        deadline = time.monotonic() + test_delay
                        while time.monotonic() < deadline and not abort_event.is_set():
                            time.sleep(0.05)
                        return "server-owned delayed response"
                    if workflow_plan is not None:
                        return execute_workflow_for_chat(workflow_plan, admin_key=workflow_admin_key,
                                                         abort_event=abort_event, status_callback=status_callback)
                    return ask_the_helper(prompt, img, target_model, sys_config, history, req.persona, abort_event,
                                          current_user, status_callback=status_callback, chunk_callback=chunk_callback,
                                          intent=direct_tool_intent)

                result = await inference_queue.submit(job_id, thread_target, abort_event, timeout=1500.0,
                                                       owner=current_user, lane=execution_lane)
                cancelled = abort_event.is_set()
                result_text = str(result or "")
                is_tool_result = result_text.strip() in {"SUCCESS", "ERROR", "AUTH_REQUIRED"}
                content = "Request cancelled." if cancelled else (result_text if not streamed_parts or is_tool_result else "".join(streamed_parts))
                if not content.strip():
                    content = "I could not complete that response. Please retry or choose another route."
                chat_job_registry.complete(job_id, current_user, content, streamed=bool(streamed_parts), cancelled=cancelled)
                logger.info("[JobTrace] job=%s lane=%s state=%s", job_id, execution_lane,
                            "cancelled" if cancelled else "completed")
            except asyncio.CancelledError:
                abort_event.set()
                chat_job_registry.fail(job_id, current_user, "The server stopped this request before it completed.")
                raise
            except Exception:
                logger.error("[Chat] Assistant task failed (background job)")
                chat_job_registry.fail(job_id, current_user, "I could not complete that response. Please retry or choose another route.")
            finally:
                try:
                    user_context.reset(token)
                except ValueError:
                    logger.debug("[Chat] User context reset skipped after background job context switch.")

        watch_task = asyncio.create_task(cancellation_watcher())
        job_task = asyncio.create_task(run_job())

        def stop_watcher(_task):
            if not watch_task.done():
                watch_task.cancel()
        job_task.add_done_callback(stop_watcher)

        if create_only:
            return JSONResponse({"success": True, "job_id": job_id}, status_code=202, headers=NO_STORE_HEADERS)
        return StreamingResponse(_legacy_job_stream(job_id, current_user),
                                 media_type="application/x-ndjson", headers=NO_STORE_HEADERS)
    except Exception as exc:
        logger.error("[Chat] Failed to start assistant request (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="The assistant request could not be started.") from exc

@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user)):
    return await _chat_endpoint_impl(req, request, current_user)


@router.post("/chat/jobs")
async def create_chat_job(req: ChatRequest, request: Request, current_user: str = Depends(get_current_user)):
    return await _chat_endpoint_impl(req, request, current_user, create_only=True)
