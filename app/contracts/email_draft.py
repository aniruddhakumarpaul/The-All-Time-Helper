"""Versioned email-draft contract and boundary-specific serializers.

The UI, chat route, agent tools, persistence layer, and delivery route do not
share the same trust boundary.  Keep the canonical model small and make each
serializer explicit about whether transient attachment content is allowed.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EMAIL_DRAFT_SCHEMA_VERSION = 1
EMAIL_DRAFT_MARKER = "EMAIL_DRAFT_PAYLOAD:"
EMAIL_DRAFT_CONTEXT_MARKER = "EMAIL_DRAFT_CONTEXT:"


class EmailDraftVersionError(ValueError):
    """Base error for controlled email-draft version failures."""


class InvalidEmailDraftVersion(EmailDraftVersionError):
    """The payload contains a malformed or invalid schema version."""

    code = "invalid_email_draft_version"


class UnsupportedEmailDraftVersion(EmailDraftVersionError):
    """The payload uses a future schema version this runtime cannot process."""

    code = "unsupported_email_draft_version"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$", re.I)


def detect_email_draft_version(raw: Mapping[str, Any]) -> int:
    """Return 0 for legacy payloads and reject malformed/future versions."""
    if "schema_version" not in raw:
        return 0
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise InvalidEmailDraftVersion("Email draft schema_version must be an integer.")
    if version < 0:
        raise InvalidEmailDraftVersion("Email draft schema_version cannot be negative.")
    if version > EMAIL_DRAFT_SCHEMA_VERSION:
        raise UnsupportedEmailDraftVersion("Email draft schema version is newer than supported.")
    return version


def migrate_legacy_email_draft(raw: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(raw)
    migrated["schema_version"] = EMAIL_DRAFT_SCHEMA_VERSION
    return migrated


def migrate_email_draft(raw: Mapping[str, Any]) -> dict[str, Any]:
    version = detect_email_draft_version(raw)
    if version == 0:
        return migrate_legacy_email_draft(raw)
    if version == 1:
        return dict(raw)
    raise UnsupportedEmailDraftVersion("Email draft schema version is unsupported.")


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _safe_filename(value: Any, fallback: str = "attachment.bin") -> str:
    name = _text(value, fallback).replace("\\", "/")
    name = os.path.basename(name).strip(" .") or fallback
    return name[:160]


def _safe_mime(value: Any, fallback: str = "application/octet-stream") -> str:
    candidate = _text(value).lower()
    return candidate if _MIME_RE.fullmatch(candidate) else fallback


def _safe_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not numeric.is_integer() or numeric < 0:
        return None
    return int(numeric)


def _safe_sha256(value: Any) -> str | None:
    candidate = _text(value).lower()
    return candidate if _SHA256_RE.fullmatch(candidate) else None


class EmailAttachment(BaseModel):
    """One attachment reference, with bytes allowed only in transient memory."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    filename: str = "attachment.bin"
    mime_type: str = "application/octet-stream"
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    available: bool | None = None
    source: Literal["upload", "generated", "legacy", "remote", "unknown"] = "unknown"
    content: str | None = None


def _canonical_attachment(raw: Any, index: int = 0, legacy_content: Any = None) -> dict[str, Any]:
    item = raw if isinstance(raw, Mapping) else {"content": raw}
    content = item.get("content") or item.get("data")
    if content is None and index == 0:
        content = legacy_content
    attachment_id = _text(item.get("id")) or None
    filename = _safe_filename(
        item.get("filename") or item.get("name"),
        f"attachment-{index + 1}.bin",
    )
    mime_type = _safe_mime(
        item.get("mime_type") or item.get("content_type") or item.get("type"),
        "application/octet-stream",
    )
    source = _text(item.get("source"), "generated" if content and not attachment_id else "upload" if attachment_id else "unknown")
    if source not in {"upload", "generated", "legacy", "remote", "unknown"}:
        source = "unknown"
    available = item.get("available")
    if available is None and (attachment_id or content):
        available = True
    result: dict[str, Any] = {
        "id": attachment_id,
        "filename": filename,
        "mime_type": mime_type,
        "size": _safe_size(item.get("size")),
        "sha256": _safe_sha256(item.get("sha256") or item.get("sha_256")),
        "available": bool(available) if available is not None else None,
        "source": source,
    }
    if content is not None:
        result["content"] = str(content)
    return result


def _is_meaningful_attachment(item: Mapping[str, Any]) -> bool:
    """Reject the empty legacy placeholder while preserving real metadata."""
    if item.get("id") or item.get("content"):
        return True
    if item.get("available") is not None or item.get("size") is not None or item.get("sha256"):
        return True
    if item.get("mime_type") != "application/octet-stream":
        return True
    filename = str(item.get("filename") or "").strip().lower()
    return bool(filename and filename not in {"attachment.bin", "attachment-1.bin"})

def _canonical_draft(raw: Mapping[str, Any]) -> dict[str, Any]:
    attachments_raw = raw.get("attachments")
    if not isinstance(attachments_raw, list):
        attachments_raw = []
    legacy_content = raw.get("attachment_content")
    attachments = [
        _canonical_attachment(item, index, legacy_content if index == 0 and not raw.get("attachment_filename") else None)
        for index, item in enumerate(attachments_raw)
    ]
    attachments = [item for item in attachments if _is_meaningful_attachment(item)]
    if not attachments and (legacy_content is not None or raw.get("attachment_filename")):
        legacy_attachment = _canonical_attachment({
            "content": legacy_content,
            "filename": raw.get("attachment_filename"),
            "type": raw.get("attachment_type"),
            "source": "legacy",
        })
        if _is_meaningful_attachment(legacy_attachment):
            attachments.append(legacy_attachment)
    if attachments and legacy_content is not None:
        legacy_target = next(
            (item for item in attachments if raw.get("attachment_filename") and item.get("filename") == _safe_filename(raw.get("attachment_filename"), "")),
            attachments[0],
        )
        if not legacy_target.get("content"):
            legacy_target["content"] = str(legacy_content)

    primary = attachments[0] if attachments else {}
    if attachments and (legacy_content is not None or raw.get("attachment_filename")):
        primary = next(
            (item for item in attachments if (
                (legacy_content is not None and item.get("content") == str(legacy_content))
                or (raw.get("attachment_filename") and item.get("filename") == _safe_filename(raw.get("attachment_filename"), ""))
            )),
            primary,
        )
    return {
        "schema_version": EMAIL_DRAFT_SCHEMA_VERSION,
        "recipient": _text(raw.get("recipient") or raw.get("to")),
        "subject": _text(raw.get("subject")),
        "body": str(raw.get("body") or "").replace("\r\n", "\n").replace("\r", "\n"),
        "tone": _text(raw.get("tone"), "modern") if _text(raw.get("tone"), "modern") in {"formal", "informal", "modern"} else "modern",
        "attachment_description": _text(raw.get("attachment_description")) or None,
        "attachment_content": primary.get("content"),
        "attachment_filename": primary.get("filename", ""),
        "attachment_type": primary.get("mime_type") or None,
        "attachments": attachments,
    }


class EmailDraft(BaseModel):
    """Canonical versioned draft model.

    Legacy ``to``, ``name``, ``type``, ``content_type``, and ``data`` inputs are
    normalized at ingress. Legacy first-attachment output fields remain in the
    transient and delivery serializers for older clients.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = EMAIL_DRAFT_SCHEMA_VERSION
    recipient: str = ""
    subject: str = ""
    body: str = ""
    tone: Literal["formal", "informal", "modern"] = "modern"
    attachment_description: str | None = None
    attachment_content: str | None = None
    attachment_filename: str = ""
    attachment_type: str | None = None
    attachments: list[EmailAttachment] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_input(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return _canonical_draft(migrate_email_draft(value))
        return value


def normalize_email_draft(raw: EmailDraft | Mapping[str, Any]) -> EmailDraft:
    """Normalize a legacy or versioned mapping through the compatibility gate."""

    if isinstance(raw, EmailDraft):
        return raw
    return EmailDraft.model_validate(migrate_email_draft(raw))


def _attachment_output(item: EmailAttachment, *, include_content: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": item.id,
        "filename": item.filename,
        "name": item.filename,
        "mime_type": item.mime_type,
        "type": item.mime_type,
        "content_type": item.mime_type,
        "size": item.size,
        "sha256": item.sha256,
        "available": item.available,
        "source": item.source,
    }
    if include_content and item.content is not None:
        result["content"] = item.content
    return {key: value for key, value in result.items() if value is not None}


def _base_output(draft: EmailDraft) -> dict[str, Any]:
    return {
        "schema_version": EMAIL_DRAFT_SCHEMA_VERSION,
        "recipient": draft.recipient,
        "subject": draft.subject,
        "body": draft.body,
        "tone": draft.tone,
        "attachment_description": draft.attachment_description,
    }


def serialize_full_transient(raw: EmailDraft | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize the in-memory/widget payload; content must not be persisted."""

    draft = normalize_email_draft(raw)
    result = _base_output(draft)
    result.update({
        "attachment_content": draft.attachment_content,
        "has_attachment_content": bool(draft.attachment_content or any(item.content for item in draft.attachments)),
        "attachment_filename": draft.attachment_filename,
        "attachment_type": draft.attachment_type,
        "attachments": [_attachment_output(item, include_content=True) for item in draft.attachments],
    })
    return result


def serialize_prompt_context(raw: EmailDraft | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize a bounded context payload with metadata but never attachment bytes."""

    draft = normalize_email_draft(raw)
    result = _base_output(draft)
    result.update({
        "attachment_filename": draft.attachment_filename,
        "attachment_type": draft.attachment_type,
        "attachments": [_attachment_output(item, include_content=False) for item in draft.attachments],
    })
    return result


def serialize_persistable(raw: EmailDraft | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize chat/local-storage-safe metadata only."""

    draft = normalize_email_draft(raw)
    result = _base_output(draft)
    result["attachments"] = [_attachment_output(item, include_content=False) for item in draft.attachments]
    return result


def serialize_delivery(raw: EmailDraft | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize the request-scoped delivery payload, including transient bytes."""

    return serialize_full_transient(raw)


def draft_marker(raw: EmailDraft | Mapping[str, Any], *, context: bool = False) -> str:
    marker = EMAIL_DRAFT_CONTEXT_MARKER if context else EMAIL_DRAFT_MARKER
    serializer = serialize_prompt_context if context else serialize_full_transient
    return marker + json.dumps(serializer(raw), separators=(",", ":"))

