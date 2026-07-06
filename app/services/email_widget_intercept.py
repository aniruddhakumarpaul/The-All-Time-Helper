import anyio
import json
from typing import Any

import jwt
from fastapi import Request
from fastapi.responses import Response

from app.logger import logger
from app.security import ALGORITHM, SECRET_KEY


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _restore_request_body(request: Request, body: bytes) -> None:
    """Replay the consumed request body once for the downstream FastAPI route.

    A repeated http.request after the response starts breaks Starlette streaming
    disconnect handling, so the replay receive must not return the body more
    than once.
    """
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.sleep(86400)
        return {"type": "http.disconnect"}

    request._receive = receive


def _has_valid_bearer_token(request: Request) -> bool:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("bearer "):
        return False
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return False
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return bool(payload.get("sub"))
    except jwt.PyJWTError:
        return False


def _is_email_widget_attachment_request(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    if not lowered:
        return False
    has_attach_action = any(term in lowered for term in ("attach", "attachment", "include", "add", "put", "use"))
    has_email_surface = any(term in lowered for term in ("email", "mail", "draft", "template", "widget", "wedgit"))
    has_visual_reference = any(term in lowered for term in ("this", "that", "above", "last", "previous", "it", "pic", "picture", "image", "photo"))
    send_now = any(term in lowered for term in ("send now", "send it", "send email", "send the email", "dispatch", "broadcast"))
    return has_attach_action and has_email_surface and has_visual_reference and not send_now


def _safe_image_filename(filename: str | None) -> str:
    name = str(filename or "image.png").strip() or "image.png"
    if not name.lower().endswith(_IMAGE_EXTENSIONS):
        name = f"{name}.png"
    return name[:120]


def _latest_image_email_draft(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    from app.logic.tools import resolve_chat_image

    resolved = resolve_chat_image("latest image", history)
    if not (isinstance(resolved, tuple) and resolved and resolved[0]):
        return None
    return {
        "recipient": "",
        "subject": "Image Attachment",
        "body": "Please find the image attached.",
        "tone": "modern",
        "attachment_content": resolved[0],
        "attachment_filename": _safe_image_filename(resolved[1] if len(resolved) > 1 else "image.png"),
    }


def _email_widget_message(draft: dict[str, Any] | None) -> str:
    if not draft:
        return "I couldn't find a recent image to attach. Generate or upload an image first, then ask me to attach it to the email widget."
    return "Attached the latest image to a new editable email draft.\n\nEMAIL_DRAFT_PAYLOAD:" + json.dumps(draft)


def _email_widget_ndjson(message: str) -> bytes:
    lines = [
        {"status": "Attaching latest image to email draft..."},
        {"message": {"content": message}, "done": True},
        {"done": True},
    ]
    return b"".join(json.dumps(line).encode() + b"\n" for line in lines)


async def email_widget_chat_middleware(request: Request, call_next):
    if request.method != "POST" or request.url.path != "/chat":
        return await call_next(request)

    body = await request.body()

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        _restore_request_body(request, body)
        return await call_next(request)

    prompt = str(payload.get("prompt") or "")
    if payload.get("isMasked") or not _is_email_widget_attachment_request(prompt):
        _restore_request_body(request, body)
        return await call_next(request)

    if not _has_valid_bearer_token(request):
        _restore_request_body(request, body)
        return await call_next(request)

    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    try:
        draft = _latest_image_email_draft(history)
        message = _email_widget_message(draft)
        logger.info("[EmailWidget] Routed latest image attachment request without agent execution.")
        return Response(content=_email_widget_ndjson(message), media_type="application/x-ndjson")
    except Exception as exc:
        logger.warning(f"[EmailWidget] Direct image attachment failed, falling back to chat route: {exc}")
        _restore_request_body(request, body)
        return await call_next(request)
