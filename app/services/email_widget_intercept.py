import json
from typing import Any

from fastapi import Request


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_DRAFT_MARKERS = ("EMAIL_DRAFT_CONTEXT:", "EMAIL_DRAFT_PAYLOAD:", "[Attached Context")


def _has_targeted_email_draft_context(prompt: str) -> bool:
    raw = str(prompt or "")
    return any(marker in raw for marker in _DRAFT_MARKERS)


def _looks_like_email_body_edit(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    has_body_target = any(term in lowered for term in ("body", "message", "email text", "email copy"))
    has_write_action = any(term in lowered for term in ("write", "compose", "draft", "fill", "make relevant", "i am lazy", "i'm lazy"))
    return has_body_target and has_write_action


def _is_email_widget_attachment_request(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    if not lowered:
        return False
    if _has_targeted_email_draft_context(prompt) or _looks_like_email_body_edit(prompt):
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
    """Compatibility shim.

    This middleware must not read or replay the request body. Normal /chat
    responses are streaming NDJSON, and body replay from BaseHTTPMiddleware can
    trigger Starlette's `Unexpected message received: http.request` failure.
    Email-widget routing is handled inside app.routes.chat.chat_endpoint.
    """
    return await call_next(request)
