import hashlib
import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.logic.bus import job_id_context
from app.logic.memory import user_context
from app.logic.tools import send_or_simulate_email
from app.security import get_current_user, verify_admin_key

router = APIRouter(prefix="/email", tags=["email"])

MAX_BODY_CHARS = 50_000
MAX_ATTACHMENTS = 10
VALID_TONES = {"formal", "informal", "modern"}


class EmailDraftPayload(BaseModel):
    recipient: str
    subject: str = ""
    body: str = ""
    tone: str = "modern"
    attachment_content: Optional[Any] = None
    attachment_filename: str = "attachment.png"
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class SendDraftRequest(BaseModel):
    draft: EmailDraftPayload
    admin_key: str
    request_id: Optional[str] = None


def _safe_request_id(value: str | None, draft: EmailDraftPayload, current_user: str) -> str:
    raw = str(value or "").strip()
    if raw and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", raw):
        return raw
    digest = hashlib.sha256(
        json.dumps(draft.model_dump(mode="json"), sort_keys=True, default=str).encode("utf-8")
        + current_user.encode("utf-8")
    ).hexdigest()[:32]
    return f"email-{digest}"


@router.post("/send-draft")
def send_approved_email_draft(
    req: SendDraftRequest,
    current_user: str = Depends(get_current_user),
):
    if not verify_admin_key(req.admin_key):
        raise HTTPException(status_code=403, detail="Invalid admin key")

    draft = req.draft
    if len(draft.body or "") > MAX_BODY_CHARS:
        raise HTTPException(status_code=400, detail="Email body is too large")
    if len(draft.attachments or []) > MAX_ATTACHMENTS:
        raise HTTPException(status_code=400, detail="Too many attachments")

    tone = draft.tone if draft.tone in VALID_TONES else "modern"
    request_id = _safe_request_id(req.request_id, draft, current_user)
    job_id = f"approved-email:{current_user}:{request_id}"
    job_token = job_id_context.set(job_id)
    user_token = user_context.set(current_user)
    try:
        result = send_or_simulate_email(
            recipient=draft.recipient,
            subject=str(draft.subject or "")[:998],
            body=draft.body or "",
            tone=tone,
            attachment_content=draft.attachment_content,
            attachment_filename=draft.attachment_filename or "attachment.png",
            attachments=draft.attachments,
            owner=current_user,
        )
    finally:
        job_id_context.reset(job_token)
        user_context.reset(user_token)

    success = result.startswith("SIMULATE SUCCESS") or result.startswith("LIVE SUCCESS")
    return {
        "success": success,
        "status": result,
        "request_id": request_id,
        "mode": "simulated" if result.startswith("SIMULATE SUCCESS") else "live" if result.startswith("LIVE SUCCESS") else "error",
    }
