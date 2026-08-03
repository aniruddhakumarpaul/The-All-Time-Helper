"""Protected email-delivery service shared by HTTP and workflow callers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from app.contracts.email_draft import EmailDraft, normalize_email_draft, serialize_delivery
from app.database import DB_FILE
from app.logic.bus import job_id_context
from app.logic.memory import user_context
from app.logic.tools import send_or_simulate_email
from app.security import verify_admin_key


MAX_BODY_CHARS = 50_000
MAX_ATTACHMENTS = 10
VALID_TONES = {"formal", "informal", "modern"}
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,120}")
_RECIPIENT_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_DELIVERY_LOCK = threading.RLock()
_DELIVERY_CACHE_LIMIT = 512


class EmailDeliveryError(RuntimeError):
    """Base class for controlled delivery failures."""


class EmailAuthorizationError(EmailDeliveryError):
    """The request-scoped approval key is absent or invalid."""


class EmailValidationError(EmailDeliveryError):
    """The draft cannot safely cross the delivery boundary."""


@dataclass(frozen=True)
class EmailDeliveryResult:
    success: bool
    status: str
    request_id: str
    mode: str
    duplicate: bool = False


def safe_request_id(value: str | None, draft: EmailDraft, owner: str) -> str:
    raw = str(value or "").strip()
    if raw and _REQUEST_ID_RE.fullmatch(raw):
        return raw
    digest = hashlib.sha256(
        json.dumps(draft.model_dump(mode="json"), sort_keys=True, default=str).encode("utf-8")
        + owner.encode("utf-8")
    ).hexdigest()[:32]
    return f"email-{digest}"


def _job_id(owner: str, request_id: str) -> str:
    owner_scope = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
    return f"approved-email:{owner_scope}:{request_id}"


def _public_status(mode: str, success: bool) -> str:
    if not success:
        return "Email delivery failed. The draft remains available to retry."
    return "Email delivery simulated successfully." if mode == "simulated" else "Email sent successfully."

def _mode_from_status(status: str) -> str:
    if status.startswith("SIMULATE SUCCESS"):
        return "simulated"
    if status.startswith("LIVE SUCCESS"):
        return "live"
    return "error"


def _existing_delivery(job_id: str) -> str | None:
    try:
        with sqlite3.connect(DB_FILE) as connection:
            row = connection.execute(
                "SELECT status FROM email_send_log WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None


def _record_delivery(job_id: str, owner: str, recipient: str, status: str) -> None:
    try:
        with sqlite3.connect(DB_FILE) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO email_send_log "
                "(job_id, user_email, recipients, status, timestamp) VALUES (?, ?, ?, ?, unixepoch())",
                (job_id, owner, recipient, status),
            )
            connection.commit()
    except sqlite3.Error:
        # Delivery has already completed; a receipt-write failure must not cause a retry send.
        return


class EmailDeliveryService:
    """Validate authorization and deliver one owner-scoped draft exactly once."""

    def __init__(
        self,
        *,
        key_verifier: Callable[[str | None], bool] = verify_admin_key,
        sender: Callable[..., str] = send_or_simulate_email,
    ) -> None:
        self._key_verifier = key_verifier
        self._sender = sender
        self._completed: OrderedDict[str, str] = OrderedDict()

    def send_approved_email(
        self,
        *,
        draft: EmailDraft | dict,
        owner: str,
        admin_key: str | None,
        request_id: str | None = None,
    ) -> EmailDeliveryResult:
        if not self._key_verifier(admin_key):
            raise EmailAuthorizationError("Authorization is required before email delivery.")

        canonical = normalize_email_draft(draft)
        recipients = [item.strip() for item in canonical.recipient.split(",")]
        if not any(_RECIPIENT_RE.fullmatch(item) for item in recipients):
            raise EmailValidationError("A valid recipient is required before delivery.")
        if len(canonical.body or "") > MAX_BODY_CHARS:
            raise EmailValidationError("Email body is too large.")
        if len(canonical.attachments or []) > MAX_ATTACHMENTS:
            raise EmailValidationError("Too many attachments.")

        safe_id = safe_request_id(request_id, canonical, owner)
        delivery_job_id = _job_id(owner, safe_id)

        # The lock closes the in-process check/send race. The database key preserves
        # idempotency across later retries and process restarts.
        with _DELIVERY_LOCK:
            existing = self._completed.get(delivery_job_id) or _existing_delivery(delivery_job_id)
            if existing:
                self._completed[delivery_job_id] = existing
                self._completed.move_to_end(delivery_job_id)
                return EmailDeliveryResult(
                    success=True,
                    status=_public_status(_mode_from_status(existing), True),
                    request_id=safe_id,
                    mode=_mode_from_status(existing),
                    duplicate=True,
                )

            job_token = job_id_context.set(delivery_job_id)
            user_token = user_context.set(owner)
            try:
                payload = serialize_delivery(canonical)
                status = str(self._sender(
                    recipient=canonical.recipient,
                    subject=str(canonical.subject or "")[:998],
                    body=canonical.body or "",
                    tone=canonical.tone if canonical.tone in VALID_TONES else "modern",
                    attachment_content=payload.get("attachment_content"),
                    attachment_filename=payload.get("attachment_filename") or "attachment.png",
                    attachments=payload.get("attachments"),
                    owner=owner,
                ) or "")
            except Exception:
                status = "ERROR: Email delivery failed."
            finally:
                job_id_context.reset(job_token)
                user_context.reset(user_token)

            success = status.startswith("SIMULATE SUCCESS") or status.startswith("LIVE SUCCESS")
            if success:
                self._completed[delivery_job_id] = status
                self._completed.move_to_end(delivery_job_id)
                while len(self._completed) > _DELIVERY_CACHE_LIMIT:
                    self._completed.popitem(last=False)
                _record_delivery(delivery_job_id, owner, canonical.recipient, status)
            mode = _mode_from_status(status)
            return EmailDeliveryResult(
                success=success,
                status=_public_status(mode, success),
                request_id=safe_id,
                mode=mode,
            )


email_delivery_service = EmailDeliveryService()
