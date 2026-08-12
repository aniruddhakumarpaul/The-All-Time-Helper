"""Protected email-delivery service shared by HTTP and workflow callers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from typing import Callable

from app.contracts.email_draft import EmailDraft, normalize_email_draft, serialize_delivery
from app.database import DB_FILE
from app.logic.bus import job_id_context
from app.logic.memory import user_context
from app.logic.tools import send_or_simulate_email
from app.observability import start_span
from app.logic.capability_policy import CapabilityContext, CapabilityGateway
from app.logic.capability_policy import CapabilitySource
from app.logic.action_outbox import (
    CANCELLED,
    CLAIMED,
    DISPATCHING,
    FAILED,
    PREPARED,
    SUCCEEDED,
    UNKNOWN_EXTERNAL_RESULT,
    ActionOutboxCapacityError,
    ActionOutboxConflict,
    SQLiteActionOutboxStore,
    idempotency_digest,
)
from app.security import verify_admin_key


MAX_BODY_CHARS = 50_000
MAX_ATTACHMENTS = 10
VALID_TONES = {"formal", "informal", "modern"}
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,120}")
_RECIPIENT_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


class EmailDeliveryError(RuntimeError):
    """Base class for controlled delivery failures."""


class EmailAuthorizationError(EmailDeliveryError):
    """The request-scoped approval key is absent or invalid."""


class EmailValidationError(EmailDeliveryError):
    """The draft cannot safely cross the delivery boundary."""


class EmailIdempotencyConflict(EmailDeliveryError):
    """A request ID was reused for a materially different email draft."""


class EmailDeliveryUnavailable(EmailDeliveryError):
    """Durable dispatch coordination cannot safely admit the request."""


@dataclass(frozen=True)
class EmailDeliveryResult:
    success: bool
    status: str
    request_id: str
    mode: str
    duplicate: bool = False
    outcome: str | None = None
    outbox_id: str | None = None
    dispatch_execution_id: str | None = None
    receipt_reference: str | None = None


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


def email_payload_fingerprint(draft: EmailDraft) -> str:
    payload = serialize_delivery(draft)
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_status(mode: str, success: bool) -> str:
    if not success:
        return "Email delivery failed. The draft remains available to retry."
    return "Email delivery simulated successfully." if mode == "simulated" else "Email sent successfully."


def _uncertain_status() -> str:
    return "Email delivery began, but the final outcome could not be confirmed. It will not be retried automatically."


def _in_progress_status() -> str:
    return "This email delivery is already in progress."

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
    """Deprecated compatibility hook; new delivery receipts live in the outbox."""
    return None


class EmailDeliveryService:
    """Validate authorization and deliver one owner-scoped draft exactly once."""

    def __init__(
        self,
        *,
        key_verifier: Callable[[str | None], bool] = verify_admin_key,
        sender: Callable[..., str] = send_or_simulate_email,
        outbox_store: SQLiteActionOutboxStore | None = None,
    ) -> None:
        self._key_verifier = key_verifier
        self._sender = sender
        self.outbox_store = outbox_store or SQLiteActionOutboxStore()
        self._capability_gateway = CapabilityGateway({"email.deliver": self._send_authorized_email})

    def is_authorized(self, admin_key: str | None) -> bool:
        """Validate a request-scoped approval candidate without retaining it."""
        return bool(self._key_verifier(admin_key))

    def send_approved_email(
        self,
        *,
        draft: EmailDraft | dict,
        owner: str,
        admin_key: str | None,
        request_id: str | None = None,
        capability_context: CapabilityContext,
        prepared_outbox_id: str | None = None,
        dispatch_execution_id: str | None = None,
    ) -> EmailDeliveryResult:
        return self._capability_gateway.invoke(
            "email.deliver",
            context=capability_context,
            arguments={
                "draft": draft,
                "owner": owner,
                "admin_key": admin_key,
                "request_id": request_id,
                "dispatch_context": capability_context,
                "prepared_outbox_id": prepared_outbox_id,
                "dispatch_execution_id": dispatch_execution_id,
            },
        )

    def _send_authorized_email(
        self,
        *,
        draft: EmailDraft | dict,
        owner: str,
        admin_key: str | None,
        request_id: str | None = None,
        dispatch_context: CapabilityContext,
        prepared_outbox_id: str | None = None,
        dispatch_execution_id: str | None = None,
    ) -> EmailDeliveryResult:
        if not self.is_authorized(admin_key):
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
        fingerprint = email_payload_fingerprint(canonical)
        source = "workflow" if dispatch_context.source == CapabilitySource.WORKFLOW else "http"

        try:
            existing = _existing_delivery(delivery_job_id)
            if existing and _mode_from_status(existing) != "error":
                record = self.outbox_store.record_legacy_success(
                    owner=owner,
                    capability_id="email.deliver",
                    source=source,
                    idempotency_key=safe_id,
                    payload_fingerprint=fingerprint,
                    workflow_id=dispatch_context.workflow_id,
                    job_id=dispatch_context.job_id,
                )
                mode = _mode_from_status(existing)
                return EmailDeliveryResult(
                    success=True, status=_public_status(mode, True), request_id=safe_id,
                    mode=mode, duplicate=True, outcome=SUCCEEDED,
                    outbox_id=record["outbox_id"], receipt_reference="legacy",
                )

            if prepared_outbox_id:
                record = self.outbox_store.get(prepared_outbox_id, owner)
                if (
                    not record
                    or record["capability_id"] != "email.deliver"
                    or record["source"] != "workflow"
                    or record["workflow_id"] != dispatch_context.workflow_id
                    or record["idempotency_key"] != idempotency_digest(safe_id)
                    or record["payload_fingerprint"] != fingerprint
                ):
                    raise EmailIdempotencyConflict("The prepared email action does not match this draft.")
            else:
                record = self.outbox_store.prepare(
                    owner=owner,
                    capability_id="email.deliver",
                    source=source,
                    idempotency_key=safe_id,
                    payload_fingerprint=fingerprint,
                    workflow_id=dispatch_context.workflow_id,
                    job_id=dispatch_context.job_id,
                )
        except ActionOutboxConflict as exc:
            raise EmailIdempotencyConflict(str(exc)) from exc
        except ActionOutboxCapacityError as exc:
            raise EmailDeliveryUnavailable("Email delivery coordination is temporarily full.") from exc

        state = str(record["state"])
        if state == SUCCEEDED:
            return EmailDeliveryResult(
                success=True, status="Email was already delivered for this request.",
                request_id=safe_id, mode="confirmed", duplicate=True, outcome=SUCCEEDED,
                outbox_id=record["outbox_id"], receipt_reference=record.get("receipt_reference"),
            )
        if state == UNKNOWN_EXTERNAL_RESULT:
            return EmailDeliveryResult(
                success=False, status=_uncertain_status(), request_id=safe_id, mode="unknown",
                duplicate=True, outcome=UNKNOWN_EXTERNAL_RESULT, outbox_id=record["outbox_id"],
            )
        if state in {FAILED, CANCELLED}:
            return EmailDeliveryResult(
                success=False, status="This email delivery request is already closed.",
                request_id=safe_id, mode="error", duplicate=True, outcome=state,
                outbox_id=record["outbox_id"],
            )

        execution_id = str(dispatch_execution_id or "").strip()
        if state == PREPARED:
            execution_id = execution_id or str(uuid.uuid4())
            if not self.outbox_store.claim(record["outbox_id"], owner, execution_id):
                return EmailDeliveryResult(
                    success=False, status=_in_progress_status(), request_id=safe_id,
                    mode="pending", duplicate=True, outcome=CLAIMED, outbox_id=record["outbox_id"],
                )
        elif state == CLAIMED:
            if not execution_id or record.get("execution_id") != execution_id:
                return EmailDeliveryResult(
                    success=False, status=_in_progress_status(), request_id=safe_id,
                    mode="pending", duplicate=True, outcome=CLAIMED, outbox_id=record["outbox_id"],
                )
        elif state == DISPATCHING:
            return EmailDeliveryResult(
                success=False, status=_in_progress_status(), request_id=safe_id,
                mode="pending", duplicate=True, outcome=DISPATCHING, outbox_id=record["outbox_id"],
            )

        if not self.outbox_store.mark_dispatch_started(record["outbox_id"], owner, execution_id):
            return EmailDeliveryResult(
                success=False, status=_uncertain_status(), request_id=safe_id,
                mode="unknown", duplicate=True, outcome=UNKNOWN_EXTERNAL_RESULT,
                outbox_id=record["outbox_id"], dispatch_execution_id=execution_id,
            )

        stop_heartbeat = threading.Event()

        def renew_dispatch_lease() -> None:
            while not stop_heartbeat.wait(self.outbox_store.lease_renew_seconds):
                if not self.outbox_store.renew_lease(record["outbox_id"], owner, execution_id):
                    return

        heartbeat = threading.Thread(
            target=renew_dispatch_lease, daemon=True, name="email-dispatch-lease",
        )
        heartbeat.start()
        job_token = job_id_context.set(delivery_job_id)
        user_token = user_context.set(owner)
        try:
            payload = serialize_delivery(canonical)
            with start_span("helper.external_action.dispatch", {
                "helper.external_action.capability": "email.deliver",
                "helper.external_action.source": source,
                "helper.external_action.state": DISPATCHING,
            }) as span:
                if span is not None:
                    span.set_attribute("helper.external_action.id", record["outbox_id"])
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
            stop_heartbeat.set()
            heartbeat.join(timeout=max(0.05, self.outbox_store.lease_renew_seconds))
            job_id_context.reset(job_token)
            user_context.reset(user_token)

        success = status.startswith("SIMULATE SUCCESS") or status.startswith("LIVE SUCCESS")
        mode = _mode_from_status(status)
        outcome = SUCCEEDED if success else UNKNOWN_EXTERNAL_RESULT
        receipt = mode if success else None
        if source == "http":
            terminal_saved = (
                self.outbox_store.mark_succeeded(
                    record["outbox_id"], owner, execution_id, receipt_reference=receipt,
                )
                if success
                else self.outbox_store.mark_unknown(
                    record["outbox_id"], owner, execution_id, error_category="transport_ambiguous",
                )
            )
            if not terminal_saved:
                outcome = UNKNOWN_EXTERNAL_RESULT
                success = False
                mode = "unknown"

        return EmailDeliveryResult(
            success=success,
            status=_public_status(mode, True) if success else _uncertain_status(),
            request_id=safe_id,
            mode=mode,
            outcome=outcome,
            outbox_id=record["outbox_id"],
            dispatch_execution_id=execution_id,
            receipt_reference=receipt,
        )


email_delivery_service = EmailDeliveryService()
