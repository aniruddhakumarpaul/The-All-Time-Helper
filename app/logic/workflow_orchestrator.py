"""Deterministic planning and execution for compound email workflows.

Known email, research, and image combinations are coordinated above the
cloud/local model split. Ordinary chat and open-ended agent work remain on the
existing router.
"""

from __future__ import annotations

import copy
import mimetypes
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.email_draft import (
    EMAIL_DRAFT_CONTEXT_MARKER,
    EMAIL_DRAFT_MARKER,
    EmailDraft,
    EmailDraftVersionError,
    draft_marker,
    normalize_email_draft,
    serialize_full_transient,
)
from app.logger import logger
from app.services.email_delivery_service import (
    EmailAuthorizationError,
    EmailDeliveryService,
    EmailValidationError,
    email_delivery_service,
)


class WorkflowIntent(str, Enum):
    DRAFT_EMAIL = "draft_email"
    UPDATE_EMAIL_DRAFT = "update_email_draft"
    SEARCH_WEB = "search_web"
    SEARCH_IMAGE = "search_image"
    GENERATE_IMAGE = "generate_image"
    ATTACH_TO_DRAFT = "attach_to_draft"
    REQUEST_EMAIL_APPROVAL = "request_email_approval"
    DELIVER_EMAIL = "deliver_email"
    GENERAL_RESPONSE = "general_response"


class WorkflowActionType(str, Enum):
    WEB_SEARCH = "web_search"
    IMAGE_SEARCH = "image_search"
    IMAGE_GENERATE = "image_generate"
    BUILD_EMAIL_DRAFT = "build_email_draft"
    UPDATE_EMAIL_DRAFT = "update_email_draft"
    ATTACH_IMAGE = "attach_image"
    DELIVER_EMAIL = "deliver_email"
    GENERAL_RESPONSE = "general_response"


class WorkflowApprovalState(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    APPROVED = "approved"


class WorkflowActionState(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class WorkflowAction(BaseModel):
    id: str
    action_type: WorkflowActionType
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    can_run_parallel: bool = False
    sensitive: bool = False
    terminal: bool = False


class WorkflowPlan(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner: str
    intent: WorkflowIntent
    actions: list[WorkflowAction]
    approval_state: WorkflowApprovalState = WorkflowApprovalState.NOT_REQUIRED
    active_draft: EmailDraft | None = None
    topic: str = ""
    completed_action_ids: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    expires_at: float


class ImageToolResult(BaseModel):
    source: str
    url: str | None = None
    attachment_id: str | None = None
    filename: str
    mime_type: str
    title: str = ""
    query: str = ""


class WorkflowActionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    action_id: str
    action_type: WorkflowActionType
    state: WorkflowActionState
    output: Any = None
    error_category: str | None = None
    duration_ms: int = 0


class WorkflowExecutionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    message: str
    plan: WorkflowPlan
    actions: dict[str, WorkflowActionResult] = Field(default_factory=dict)
    paused: bool = False
    cancelled: bool = False


class WorkflowContext(BaseModel):
    active_draft: EmailDraft | None = None
    latest_user_request: str = ""
    topic: str = ""
    error_code: str | None = None
    error_message: str | None = None


class WorkflowCancelled(RuntimeError):
    pass


class _PendingEntry:
    def __init__(self, plan: WorkflowPlan) -> None:
        self.plan = plan.model_copy(deep=True)
        self.claimed = False


class PendingWorkflowStore:
    """Short-lived owner-scoped workflow state; credentials are never stored."""

    def __init__(self, ttl_seconds: int = 600, max_entries: int = 256) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._entries: dict[str, _PendingEntry] = {}
        self._lock = threading.RLock()

    def _cleanup_locked(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        expired = [owner for owner, entry in self._entries.items() if entry.plan.expires_at <= current]
        for owner in expired:
            self._entries.pop(owner, None)

    def put(self, plan: WorkflowPlan) -> WorkflowPlan:
        with self._lock:
            self._cleanup_locked()
            if len(self._entries) >= self.max_entries and plan.owner not in self._entries:
                oldest_owner = min(self._entries, key=lambda owner: self._entries[owner].plan.created_at)
                self._entries.pop(oldest_owner, None)
            safe_plan = plan.model_copy(deep=True)
            safe_plan.expires_at = min(safe_plan.expires_at, time.time() + self.ttl_seconds)
            safe_plan.active_draft = _safe_pending_draft(safe_plan.active_draft)
            self._entries[plan.owner] = _PendingEntry(safe_plan)
            return safe_plan.model_copy(deep=True)

    def peek(self, owner: str) -> WorkflowPlan | None:
        with self._lock:
            self._cleanup_locked()
            entry = self._entries.get(owner)
            if not entry or entry.claimed:
                return None
            return entry.plan.model_copy(deep=True)

    def claim(self, owner: str, workflow_id: str) -> WorkflowPlan | None:
        with self._lock:
            self._cleanup_locked()
            entry = self._entries.get(owner)
            if not entry or entry.claimed or entry.plan.workflow_id != workflow_id:
                return None
            entry.claimed = True
            return entry.plan.model_copy(deep=True)

    def release(self, owner: str, workflow_id: str) -> None:
        with self._lock:
            entry = self._entries.get(owner)
            if entry and entry.plan.workflow_id == workflow_id:
                entry.claimed = False

    def complete(self, owner: str, workflow_id: str) -> None:
        with self._lock:
            entry = self._entries.get(owner)
            if entry and entry.plan.workflow_id == workflow_id:
                self._entries.pop(owner, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _safe_pending_draft(draft: EmailDraft | None) -> EmailDraft | None:
    if draft is None:
        return None
    raw = serialize_full_transient(draft)
    for attachment in raw.get("attachments", []):
        content = str(attachment.get("content") or "").strip()
        if content and not content.startswith(("http://", "https://")):
            attachment.pop("content", None)
    primary = raw.get("attachments", [{}])[0] if raw.get("attachments") else {}
    raw["attachment_content"] = primary.get("content")
    return normalize_email_draft(raw)


def _message_role(message: dict) -> str:
    return str(message.get("role") or message.get("r") or "").lower()


def _message_content(message: dict) -> str:
    return str(message.get("content") or message.get("c") or "")


def _extract_marker_payload(text: str) -> tuple[dict[str, Any] | None, str | None]:
    source = str(text or "")
    candidates = []
    for marker in (EMAIL_DRAFT_CONTEXT_MARKER, EMAIL_DRAFT_MARKER):
        index = source.rfind(marker)
        if index >= 0:
            candidates.append((index, marker))
    if not candidates:
        return None, None
    index, marker = max(candidates)
    raw = source[index + len(marker):].lstrip()
    try:
        import json

        value, _ = json.JSONDecoder().raw_decode(raw)
        if not isinstance(value, dict):
            return None, "invalid_email_draft"
        return value, None
    except (TypeError, ValueError):
        return None, "invalid_email_draft"


def _latest_history_draft(history: list[dict]) -> tuple[EmailDraft | None, str | None]:
    for message in reversed(history or []):
        raw, parse_error = _extract_marker_payload(_message_content(message))
        if raw is None and parse_error is None:
            continue
        if parse_error:
            return None, parse_error
        try:
            return normalize_email_draft(raw), None
        except EmailDraftVersionError as exc:
            return None, getattr(exc, "code", "invalid_email_draft")
        except (TypeError, ValueError):
            return None, "invalid_email_draft"
    return None, None


def _merge_transient_attachments(current: EmailDraft, historical: EmailDraft | None) -> EmailDraft:
    if historical is None or not current.attachments:
        return current
    historical_items = list(historical.attachments)
    raw = serialize_full_transient(current)
    for item in raw.get("attachments", []):
        if item.get("content"):
            continue
        match = next(
            (
                candidate for candidate in historical_items
                if (item.get("id") and candidate.id == item.get("id"))
                or (candidate.filename and candidate.filename == item.get("filename"))
            ),
            None,
        )
        if match and match.content:
            item["content"] = match.content
    first = raw.get("attachments", [{}])[0] if raw.get("attachments") else {}
    raw["attachment_content"] = first.get("content")
    return normalize_email_draft(raw)


def _strip_draft_context(text: str) -> str:
    source = str(text or "")
    for marker in (EMAIL_DRAFT_CONTEXT_MARKER, EMAIL_DRAFT_MARKER):
        while marker in source:
            index = source.find(marker)
            raw = source[index + len(marker):].lstrip()
            try:
                import json

                _, end = json.JSONDecoder().raw_decode(raw)
                leading = len(source[index + len(marker):]) - len(raw)
                source = source[:index] + source[index + len(marker) + leading + end:]
            except (TypeError, ValueError):
                source = source[:index]
                break
    source = re.sub(r'\[Attached Context \d+\]\s*"""\s*', " ", source, flags=re.I)
    return re.sub(r'"""', " ", source).strip()


def _latest_user_request(history: list[dict]) -> str:
    for message in reversed(history or []):
        if _message_role(message) in {"user", "u", "human"} and not message.get("masked"):
            content = _strip_draft_context(_message_content(message))
            if content:
                return content[:1000]
    return ""


def _normalize_topic(value: str) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;-_")
    clean = re.sub(r"(?i)\b(?:email|mail|draft|message|reference image|image)\b", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" .,:;-_")
    if re.search(r"(?i)\b(?:pm\s+modi|narendra\s+modi|prime minister modi)\b", clean):
        return "Prime Minister Narendra Modi"
    if re.search(r"(?i)\bdigital\s+india\b", clean):
        return "Digital India"
    return clean[:180]


def _topic_from_prompt(prompt: str) -> str:
    clean = _strip_draft_context(prompt)
    clean = re.sub(r"(?i)\bhow\s+about\b", " ", clean)
    match = re.search(
        r"(?i)\babout\s+(.+?)(?=\s+(?:and|then)\s+(?:attach|include|find|search|generate|add)\b|$)",
        clean,
    )
    if match:
        return _normalize_topic(match.group(1))
    return ""


def _topic_from_draft(draft: EmailDraft | None, latest_request: str = "") -> str:
    if draft:
        combined = " ".join(
            value for value in (draft.attachment_description, draft.subject, draft.body[:800]) if value
        )
        topic = _normalize_topic(combined)
        if topic:
            return topic
    return _topic_from_prompt(latest_request) or _normalize_topic(latest_request)


def resolve_workflow_context(prompt: str, history: list[dict]) -> WorkflowContext:
    historical, history_error = _latest_history_draft(history)
    raw_current, current_error = _extract_marker_payload(prompt)
    if current_error:
        return WorkflowContext(error_code=current_error, error_message="The active email draft is malformed.")
    if raw_current is not None:
        try:
            active = _merge_transient_attachments(normalize_email_draft(raw_current), historical)
        except EmailDraftVersionError as exc:
            return WorkflowContext(
                error_code=getattr(exc, "code", "invalid_email_draft"),
                error_message="The active email draft version is not supported.",
            )
        except (TypeError, ValueError):
            return WorkflowContext(error_code="invalid_email_draft", error_message="The active email draft is malformed.")
    else:
        active = historical
        if history_error:
            return WorkflowContext(
                error_code=history_error,
                error_message=(
                    "The latest email draft uses an unsupported version."
                    if history_error == "unsupported_email_draft_version"
                    else "The latest email draft is malformed."
                ),
            )
    latest_request = _latest_user_request(history)
    topic = _topic_from_prompt(prompt) or _topic_from_draft(active, latest_request)
    return WorkflowContext(active_draft=active, latest_user_request=latest_request, topic=topic)


def _is_delivery_request(text: str) -> bool:
    return bool(
        re.search(r"(?i)\b(?:send|dispatch|deliver)\b.{0,45}\b(?:email|mail|draft|message|this|it|now|me)\b", text)
        or re.search(r"(?i)\bemail\s+(?:it|this|the\s+draft)\b", text)
        or re.search(r"(?i)\bapprove\s+and\s+send\b", text)
    )


def _is_draft_request(text: str) -> bool:
    return bool(
        re.search(r"(?i)\b(?:write|draft|compose|prepare|create)\b.{0,40}\b(?:email|mail|message|template)\b", text)
        or re.search(r"(?i)\b(?:email|mail)\s+(?:draft|template)\b", text)
    )


def _wants_image_attachment(text: str) -> bool:
    image = re.search(r"(?i)\b(?:image|photo|picture|pic|artwork|illustration)\b", text)
    attach = re.search(r"(?i)\b(?:attach|attachment|include|add|with)\b", text)
    reference = re.search(r"(?i)\b(?:refer\w*|actual|real|existing)\b", text)
    return bool(image and (attach or reference))


def _wants_generated_image(text: str) -> bool:
    if re.search(r"(?i)\b(?:refer\w*|actual|real|existing)\b", text):
        return False
    boundary = r"(?:(?!\b(?:email|mail|draft|message|template)\b).){0,100}"
    return bool(
        re.search(
            rf"(?i)\b(?:generate|create|draw|paint|render)\b{boundary}\b(?:image|photo|picture|artwork|illustration)\b",
            text,
        )
        or re.search(
            rf"(?i)\b(?:image|photo|picture|artwork|illustration)\b{boundary}\b(?:generate|create|draw|paint|render)\b",
            text,
        )
    )


def _wants_research(text: str) -> bool:
    return bool(re.search(r"(?i)\b(?:current|latest|recent|factual|facts?|research|up[- ]to[- ]date)\b", text))


def _wants_draft_update(text: str) -> bool:
    return bool(
        re.search(r"(?i)\b(?:update|change|set|fill|rewrite|edit|improve|add)\b", text)
        and re.search(r"(?i)\b(?:body|subject|recipient|tone|draft|email|mail|facts?|points?)\b", text)
    )


def classify_workflow_intent(
    prompt: str,
    *,
    has_active_draft: bool = False,
    is_masked: bool = False,
) -> WorkflowIntent:
    """Classify workflow-sensitive intent without exposing it to a model."""
    if is_masked:
        return WorkflowIntent.REQUEST_EMAIL_APPROVAL
    clean = _strip_draft_context(prompt)
    if _is_delivery_request(clean):
        return WorkflowIntent.DELIVER_EMAIL
    if _wants_generated_image(clean):
        return WorkflowIntent.GENERATE_IMAGE
    if _wants_image_attachment(clean):
        return WorkflowIntent.ATTACH_TO_DRAFT if has_active_draft else WorkflowIntent.SEARCH_IMAGE
    if has_active_draft and _wants_draft_update(clean):
        return WorkflowIntent.UPDATE_EMAIL_DRAFT
    if _is_draft_request(clean):
        return WorkflowIntent.DRAFT_EMAIL
    if _wants_research(clean):
        return WorkflowIntent.UPDATE_EMAIL_DRAFT if has_active_draft else WorkflowIntent.SEARCH_WEB
    if re.search(r"(?i)\b(?:find|search|look\s+up)\b.{0,50}\b(?:image|photo|picture)\b", clean):
        return WorkflowIntent.SEARCH_IMAGE
    return WorkflowIntent.GENERAL_RESPONSE


def _recipient_from_prompt(prompt: str, owner: str, existing: EmailDraft | None) -> str:
    email = re.search(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", _strip_draft_context(prompt))
    if email:
        return email.group(0).rstrip(".,;)")
    if re.search(r"(?i)\b(?:to|for)\s+me\b", prompt):
        return owner
    return existing.recipient if existing else ""


def _image_query(topic: str) -> str:
    normalized = _normalize_topic(topic)
    return f"{normalized} official reference image" if normalized else ""


def _image_description(prompt: str, topic: str) -> str:
    clean = _strip_draft_context(prompt)
    clean = re.sub(r"(?i)\b(?:and\s+)?attach\b.*$", "", clean).strip()
    return clean[:800] or f"A polished symbolic illustration about {topic or 'the email topic'}"


class WorkflowPlanner:
    def __init__(self, *, ttl_seconds: int = 600, pending_store: PendingWorkflowStore | None = None) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.pending_store = pending_store or PendingWorkflowStore(ttl_seconds=self.ttl_seconds)

    def plan(self, prompt: str, history: list[dict], owner: str, *, is_masked: bool = False) -> WorkflowPlan | None:
        if is_masked:
            return self.pending_store.peek(owner)

        context = resolve_workflow_context(prompt, history)
        clean = _strip_draft_context(prompt)
        now = time.time()

        if context.error_code:
            return WorkflowPlan(
                owner=owner,
                intent=WorkflowIntent.GENERAL_RESPONSE,
                actions=[WorkflowAction(
                    id="draft_error",
                    action_type=WorkflowActionType.GENERAL_RESPONSE,
                    arguments={"message": context.error_message or "The active email draft could not be read."},
                    terminal=True,
                )],
                expires_at=now + self.ttl_seconds,
            )

        active = context.active_draft
        classified_intent = classify_workflow_intent(clean, has_active_draft=active is not None)
        delivery = classified_intent == WorkflowIntent.DELIVER_EMAIL
        draft_request = classified_intent == WorkflowIntent.DRAFT_EMAIL or _is_draft_request(clean)
        image_attachment = _wants_image_attachment(clean)
        generated_image = image_attachment and _wants_generated_image(clean)
        research = _wants_research(clean)
        update = bool(active and _wants_draft_update(clean))

        if image_attachment and not active and not draft_request:
            if re.search(r"(?i)\b(?:email|mail)\s+(?:widget|template)\b", clean) and not re.search(r"(?i)\b(?:refer\w*|actual|real|existing|generate)\b", clean):
                return None
            return WorkflowPlan(
                owner=owner,
                intent=WorkflowIntent.GENERAL_RESPONSE,
                actions=[WorkflowAction(
                    id="clarify_draft",
                    action_type=WorkflowActionType.GENERAL_RESPONSE,
                    arguments={"message": "Which email draft should I attach the image to, and what topic should the image show?"},
                    terminal=True,
                )],
                expires_at=now + self.ttl_seconds,
            )

        if delivery and not active and not draft_request:
            return WorkflowPlan(
                owner=owner,
                intent=WorkflowIntent.GENERAL_RESPONSE,
                actions=[WorkflowAction(
                    id="clarify_delivery_draft",
                    action_type=WorkflowActionType.GENERAL_RESPONSE,
                    arguments={"message": "Which email draft should I send? Open or create the draft first."},
                    terminal=True,
                )],
                expires_at=now + self.ttl_seconds,
            )

        if not any((delivery, draft_request, image_attachment and active, research and active, update)):
            return None

        topic = context.topic or _topic_from_prompt(clean)
        recipient = _recipient_from_prompt(clean, owner, active)
        if image_attachment and not generated_image and not topic:
            return WorkflowPlan(
                owner=owner,
                intent=WorkflowIntent.GENERAL_RESPONSE,
                actions=[WorkflowAction(
                    id="clarify_image_topic",
                    action_type=WorkflowActionType.GENERAL_RESPONSE,
                    arguments={"message": "What topic or person should the reference image show?"},
                    terminal=True,
                )],
                active_draft=active,
                expires_at=now + self.ttl_seconds,
            )
        actions: list[WorkflowAction] = []
        external_ids: list[str] = []

        if research:
            actions.append(WorkflowAction(
                id="web_search",
                action_type=WorkflowActionType.WEB_SEARCH,
                arguments={"query": f"{topic or 'email topic'} current factual information"},
                can_run_parallel=True,
            ))
            external_ids.append("web_search")

        if image_attachment:
            action_type = WorkflowActionType.IMAGE_GENERATE if generated_image else WorkflowActionType.IMAGE_SEARCH
            image_arguments = (
                {"description": _image_description(clean, topic)}
                if generated_image
                else {"query": _image_query(topic)}
            )
            actions.append(WorkflowAction(
                id="image",
                action_type=action_type,
                arguments=image_arguments,
                can_run_parallel=bool(research),
            ))
            external_ids.append("image")

        draft_node = ""
        if not active and (draft_request or delivery):
            draft_node = "build_draft"
            actions.append(WorkflowAction(
                id=draft_node,
                action_type=WorkflowActionType.BUILD_EMAIL_DRAFT,
                arguments={"recipient": recipient, "topic": topic, "request": clean[:2000]},
                depends_on=list(external_ids),
            ))
        elif active and (update or research):
            draft_node = "update_draft"
            dependencies = list(external_ids) if research and image_attachment else (["web_search"] if research else [])
            actions.append(WorkflowAction(
                id=draft_node,
                action_type=WorkflowActionType.UPDATE_EMAIL_DRAFT,
                arguments={"request": clean[:2000], "recipient": recipient},
                depends_on=dependencies,
            ))

        attachment_node = ""
        if image_attachment:
            attachment_node = "attach_image"
            dependencies = ["image"]
            if draft_node:
                dependencies.append(draft_node)
            actions.append(WorkflowAction(
                id=attachment_node,
                action_type=WorkflowActionType.ATTACH_IMAGE,
                arguments={},
                depends_on=dependencies,
                terminal=not delivery,
            ))

        if delivery:
            dependencies = [node for node in (attachment_node, draft_node) if node]
            actions.append(WorkflowAction(
                id="deliver",
                action_type=WorkflowActionType.DELIVER_EMAIL,
                arguments={},
                depends_on=dependencies,
                sensitive=True,
                terminal=True,
            ))

        if actions and not any(action.terminal for action in actions):
            actions[-1].terminal = True

        intent = (
            WorkflowIntent.DELIVER_EMAIL if delivery
            else WorkflowIntent.GENERATE_IMAGE if generated_image
            else WorkflowIntent.ATTACH_TO_DRAFT if image_attachment
            else WorkflowIntent.UPDATE_EMAIL_DRAFT if active
            else WorkflowIntent.DRAFT_EMAIL
        )
        return WorkflowPlan(
            owner=owner,
            intent=intent,
            actions=actions,
            active_draft=active,
            topic=topic,
            expires_at=now + self.ttl_seconds,
        )


def _safe_filename(value: str, fallback: str) -> str:
    decoded = unquote(str(value or "")).replace("\\", "/")
    name = os.path.basename(decoded).split("?", 1)[0].strip(" .")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name)[:120].strip(" .-")
    return name or fallback


def normalize_image_tool_result(raw: Any, *, source: str, query: str = "") -> ImageToolResult | None:
    if isinstance(raw, ImageToolResult):
        return raw
    attachment_id = None
    url = None
    title = ""
    filename = ""
    mime_type = ""
    if isinstance(raw, dict):
        attachment_id = str(raw.get("id") or "").strip() or None
        url = str(raw.get("url") or raw.get("content") or raw.get("image") or "").strip() or None
        title = str(raw.get("title") or "")[:160]
        filename = str(raw.get("filename") or raw.get("name") or "")
        mime_type = str(raw.get("mime_type") or raw.get("content_type") or raw.get("type") or "")
    else:
        text = str(raw or "").strip()
        markdown = re.search(r"!\[([^\]]*)\]\((https?://[^)]+)\)", text, flags=re.I)
        if markdown:
            title, url = markdown.group(1)[:160], markdown.group(2).strip()
        else:
            direct = re.search(r"https?://[^\s<>'\"]+", text, flags=re.I)
            if direct:
                url = direct.group(0).rstrip("),.;")
    if url:
        if len(url) > 4096:
            return None
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        guessed_name = os.path.basename(parsed.path) or ("generated-image.png" if source == "generated" else "reference-image.jpg")
        filename = _safe_filename(filename or guessed_name, "image.png")
        guessed_mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp",
        }.get(os.path.splitext(filename)[1].lower()) or mimetypes.guess_type(filename)[0]
        mime_type = mime_type if mime_type.startswith("image/") else guessed_mime or ("image/png" if source == "generated" else "image/jpeg")
    elif attachment_id:
        filename = _safe_filename(filename, "attached-image.png")
        mime_type = mime_type if mime_type.startswith("image/") else "image/png"
    else:
        return None
    return ImageToolResult(
        source=source[:32],
        url=url,
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type[:100],
        title=title,
        query=str(query or "")[:300],
    )


def _research_notes(raw: Any) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(raw or ""))
    if not text or text.lower().startswith(("error", "no reliable results")):
        return ""
    snippets = []
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean or clean.lower().startswith(("ignore previous", "system:", "assistant:")):
            continue
        if clean.lower().startswith(("snippet:", "title:")):
            snippets.append(clean.split(":", 1)[1].strip())
        if sum(len(item) for item in snippets) >= 1000:
            break
    if not snippets:
        snippets = [re.sub(r"\s+", " ", text).strip()[:1000]]
    return "\n".join(f"- {item[:350]}" for item in snippets[:4] if item)


def _build_draft(plan: WorkflowPlan, arguments: dict[str, Any], results: dict[str, WorkflowActionResult]) -> EmailDraft:
    topic = _normalize_topic(arguments.get("topic") or plan.topic) or "the requested topic"
    recipient = str(arguments.get("recipient") or "").strip()
    subject = f"About {topic}" if topic != "the requested topic" else "New email"
    body = "" if topic == "the requested topic" else (
        f"Hello,\n\nI wanted to share a concise note about {topic}. "
        "Please review the details below and let me know if you would like any changes.\n\nBest regards"
    )
    notes = _research_notes(results.get("web_search").output) if results.get("web_search") else ""
    if notes:
        body = f"Hello,\n\nHere are current reference points about {topic}:\n{notes}\n\nBest regards"
    return normalize_email_draft({
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "tone": "modern",
        "attachments": [],
    })


def _update_draft(draft: EmailDraft, arguments: dict[str, Any], results: dict[str, WorkflowActionResult]) -> EmailDraft:
    raw = serialize_full_transient(draft)
    request = str(arguments.get("request") or "")
    recipient = str(arguments.get("recipient") or "").strip()
    if recipient:
        raw["recipient"] = recipient
    subject_match = re.search(r"(?i)\bsubject\s*(?:to|as|:|=)\s*[\"']?([^\n\"']+)", request)
    body_match = re.search(r"(?is)\b(?:body|content)\s*(?:to|as|:|=)\s*[\"']?(.+?)[\"']?$", request)
    tone_match = re.search(r"(?i)\b(formal|informal|modern)\b", request)
    if subject_match:
        raw["subject"] = subject_match.group(1).strip()[:998]
    if body_match:
        raw["body"] = body_match.group(1).strip()[:50_000]
    if tone_match:
        raw["tone"] = tone_match.group(1).lower()
    notes = _research_notes(results.get("web_search").output) if results.get("web_search") else ""
    if notes:
        existing = str(raw.get("body") or "").rstrip()
        raw["body"] = f"{existing}\n\nCurrent factual reference points:\n{notes}".strip()
    return normalize_email_draft(raw)


def _attach_image(draft: EmailDraft, image: ImageToolResult | None) -> EmailDraft:
    if image is None:
        return draft
    raw = serialize_full_transient(draft)
    candidate = {
        "id": image.attachment_id,
        "filename": image.filename,
        "mime_type": image.mime_type,
        "source": "generated" if image.source == "generated" else "remote" if image.url else "upload",
        "content": image.url,
        "available": True,
    }
    attachments = list(raw.get("attachments") or [])
    duplicate = any(
        (candidate.get("id") and item.get("id") == candidate.get("id"))
        or (candidate.get("content") and item.get("content") == candidate.get("content"))
        or (
            item.get("filename") == candidate.get("filename")
            and item.get("sha256")
            and item.get("sha256") == candidate.get("sha256")
        )
        for item in attachments
    )
    if not duplicate:
        attachments.append({key: value for key, value in candidate.items() if value is not None})
    raw["attachments"] = attachments
    primary = attachments[0] if attachments else {}
    raw["attachment_content"] = primary.get("content")
    raw["attachment_filename"] = primary.get("filename", "")
    raw["attachment_type"] = primary.get("mime_type") or primary.get("type")
    return normalize_email_draft(raw)


class WorkflowExecutor:
    def __init__(
        self,
        *,
        pending_store: PendingWorkflowStore,
        delivery_service: EmailDeliveryService = email_delivery_service,
        web_search: Callable[[str], Any] | None = None,
        image_search: Callable[[str], Any] | None = None,
        image_generate: Callable[[str], Any] | None = None,
        max_parallel_actions: int = 2,
    ) -> None:
        self.pending_store = pending_store
        self.delivery_service = delivery_service
        self._web_search = web_search
        self._image_search = image_search
        self._image_generate = image_generate
        self.max_parallel_actions = max(1, min(int(max_parallel_actions), 4))

    @staticmethod
    def _tools() -> tuple[Callable, Callable, Callable]:
        from app.logic import tools

        return tools.search_tool.func, tools.image_search_tool.func, tools.image_generate_tool.func

    def _status(self, action_type: WorkflowActionType, callback: Callable[[str], None] | None) -> None:
        if not callback:
            return
        messages = {
            WorkflowActionType.WEB_SEARCH: "Researching current information...",
            WorkflowActionType.IMAGE_SEARCH: "Finding a relevant reference image...",
            WorkflowActionType.IMAGE_GENERATE: "Generating the requested image...",
            WorkflowActionType.BUILD_EMAIL_DRAFT: "Preparing your email draft...",
            WorkflowActionType.UPDATE_EMAIL_DRAFT: "Updating the draft...",
            WorkflowActionType.ATTACH_IMAGE: "Adding the image to the draft...",
            WorkflowActionType.DELIVER_EMAIL: "Sending the approved email...",
        }
        if action_type in messages:
            callback(messages[action_type])

    def _run_action(
        self,
        action: WorkflowAction,
        plan: WorkflowPlan,
        results: dict[str, WorkflowActionResult],
        draft: EmailDraft | None,
        abort_event: threading.Event | None,
        status_callback: Callable[[str], None] | None,
        admin_key: str | None,
    ) -> WorkflowActionResult:
        if abort_event and abort_event.is_set():
            raise WorkflowCancelled()
        started = time.monotonic()
        self._status(action.action_type, status_callback)
        logger.info("[Workflow] action=%s state=started", action.action_type.value)
        output: Any = None
        state = WorkflowActionState.COMPLETED
        error_category = None
        try:
            web_search, image_search, image_generate = self._tools()
            if self._web_search:
                web_search = self._web_search
            if self._image_search:
                image_search = self._image_search
            if self._image_generate:
                image_generate = self._image_generate

            if action.action_type == WorkflowActionType.WEB_SEARCH:
                raw = web_search(action.arguments.get("query", ""))
                if str(raw).lower().startswith(("error", "no reliable results")):
                    raise RuntimeError("tool_unavailable")
                output = raw
            elif action.action_type == WorkflowActionType.IMAGE_SEARCH:
                raw = image_search(action.arguments.get("query", ""))
                output = normalize_image_tool_result(raw, source="search", query=action.arguments.get("query", ""))
                if output is None:
                    raise RuntimeError("invalid_tool_result")
            elif action.action_type == WorkflowActionType.IMAGE_GENERATE:
                raw = image_generate(action.arguments.get("description", ""))
                output = normalize_image_tool_result(raw, source="generated", query=action.arguments.get("description", ""))
                if output is None:
                    raise RuntimeError("invalid_tool_result")
            elif action.action_type == WorkflowActionType.BUILD_EMAIL_DRAFT:
                output = _build_draft(plan, action.arguments, results)
            elif action.action_type == WorkflowActionType.UPDATE_EMAIL_DRAFT:
                if draft is None:
                    raise RuntimeError("missing_draft")
                output = _update_draft(draft, action.arguments, results)
            elif action.action_type == WorkflowActionType.ATTACH_IMAGE:
                if draft is None:
                    raise RuntimeError("missing_draft")
                image_result = results.get("image")
                normalized = image_result.output if image_result and image_result.state == WorkflowActionState.COMPLETED else None
                output = _attach_image(draft, normalized)
            elif action.action_type == WorkflowActionType.DELIVER_EMAIL:
                if draft is None:
                    raise RuntimeError("missing_draft")
                output = self.delivery_service.send_approved_email(
                    draft=draft,
                    owner=plan.owner,
                    admin_key=admin_key,
                    request_id=plan.workflow_id,
                )
            elif action.action_type == WorkflowActionType.GENERAL_RESPONSE:
                output = str(action.arguments.get("message") or "I need a little more context to continue.")
            if abort_event and abort_event.is_set():
                raise WorkflowCancelled()
        except WorkflowCancelled:
            state = WorkflowActionState.CANCELLED
            error_category = "cancelled"
        except EmailAuthorizationError:
            state = WorkflowActionState.PAUSED
            error_category = "authorization_required"
        except EmailValidationError:
            state = WorkflowActionState.FAILED
            error_category = "validation_error"
        except Exception as exc:
            state = WorkflowActionState.FAILED
            category = str(exc) if str(exc) in {"tool_unavailable", "invalid_tool_result", "missing_draft"} else type(exc).__name__
            error_category = category[:80]
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "[Workflow] action=%s state=%s duration_ms=%d failure=%s",
            action.action_type.value,
            state.value,
            duration_ms,
            error_category or "none",
        )
        return WorkflowActionResult(
            action_id=action.id,
            action_type=action.action_type,
            state=state,
            output=output,
            error_category=error_category,
            duration_ms=duration_ms,
        )

    def execute(
        self,
        plan: WorkflowPlan,
        *,
        admin_key: str | None = None,
        abort_event: threading.Event | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> WorkflowExecutionResult:
        current = plan.model_copy(deep=True)
        results: dict[str, WorkflowActionResult] = {}
        completed: set[str] = set(current.completed_action_ids)
        pending = {action.id: action for action in current.actions if action.id not in completed}
        draft = current.active_draft
        claimed = False

        while pending:
            if abort_event and abort_event.is_set():
                if claimed:
                    self.pending_store.release(current.owner, current.workflow_id)
                return WorkflowExecutionResult(
                    message="Request cancelled.", plan=current, actions=results, cancelled=True,
                )
            ready = [action for action in pending.values() if set(action.depends_on).issubset(completed)]
            if not ready:
                return WorkflowExecutionResult(
                    message="I could not complete the workflow because its dependencies are invalid.",
                    plan=current,
                    actions=results,
                )

            sensitive = next((action for action in ready if action.sensitive), None)
            if sensitive and not admin_key:
                current.active_draft = draft
                current.completed_action_ids = sorted(completed)
                current.approval_state = WorkflowApprovalState.REQUIRED
                self.pending_store.put(current)
                if status_callback:
                    status_callback("Approval is required before delivery.")
                results[sensitive.id] = WorkflowActionResult(
                    action_id=sensitive.id,
                    action_type=sensitive.action_type,
                    state=WorkflowActionState.PAUSED,
                    error_category="authorization_required",
                )
                return WorkflowExecutionResult(
                    message="ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message using the Masked input to authorize this delivery.",
                    plan=current,
                    actions=results,
                    paused=True,
                )
            if sensitive and admin_key and current.approval_state == WorkflowApprovalState.REQUIRED:
                claimed_plan = self.pending_store.claim(current.owner, current.workflow_id)
                if claimed_plan is None:
                    return WorkflowExecutionResult(
                        message="This pending email delivery is unavailable or already being processed.",
                        plan=current,
                        actions=results,
                    )
                current = claimed_plan
                draft = current.active_draft
                completed = set(current.completed_action_ids)
                pending = {action.id: action for action in current.actions if action.id not in completed}
                ready = [action for action in pending.values() if set(action.depends_on).issubset(completed)]
                sensitive = next((action for action in ready if action.sensitive), sensitive)
                claimed = True

            parallel = [action for action in ready if action.can_run_parallel and not action.sensitive]
            serial = [action for action in ready if action not in parallel]
            action_results: list[WorkflowActionResult] = []
            if len(parallel) > 1:
                with ThreadPoolExecutor(max_workers=min(self.max_parallel_actions, len(parallel))) as pool:
                    futures = {
                        pool.submit(
                            self._run_action, action, current, results, draft, abort_event,
                            status_callback, admin_key,
                        ): action
                        for action in parallel
                    }
                    for future in as_completed(futures):
                        action_results.append(future.result())
            else:
                serial = parallel + serial
            for action in serial:
                result = self._run_action(
                    action, current, results, draft, abort_event, status_callback, admin_key,
                )
                action_results.append(result)
                if result.state == WorkflowActionState.COMPLETED and isinstance(result.output, EmailDraft):
                    draft = result.output

            for result in action_results:
                results[result.action_id] = result
                completed.add(result.action_id)
                current.completed_action_ids = sorted(completed)
                pending.pop(result.action_id, None)
                if result.state == WorkflowActionState.COMPLETED and isinstance(result.output, EmailDraft):
                    draft = result.output
                if result.state == WorkflowActionState.CANCELLED:
                    if claimed:
                        self.pending_store.release(current.owner, current.workflow_id)
                    return WorkflowExecutionResult(
                        message="Request cancelled.", plan=current, actions=results, cancelled=True,
                    )
                if result.action_type == WorkflowActionType.DELIVER_EMAIL:
                    if result.state == WorkflowActionState.PAUSED:
                        if claimed:
                            self.pending_store.release(current.owner, current.workflow_id)
                        return WorkflowExecutionResult(
                            message="ERROR: AUTH_REQUIRED. Incorrect Admin Key. The pending email is unchanged.",
                            plan=current,
                            actions=results,
                            paused=True,
                        )
                    if result.state == WorkflowActionState.COMPLETED and result.output.success:
                        current.approval_state = WorkflowApprovalState.APPROVED
                        self.pending_store.complete(current.owner, current.workflow_id)
                        mode = "simulated" if result.output.mode == "simulated" else "sent"
                        return WorkflowExecutionResult(
                            message=f"Email {mode} successfully.", plan=current, actions=results,
                        )
                    if claimed:
                        self.pending_store.release(current.owner, current.workflow_id)
                    return WorkflowExecutionResult(
                        message="The approved email could not be delivered. The draft remains available to retry.",
                        plan=current,
                        actions=results,
                    )

        current.active_draft = draft
        general = next(
            (item.output for item in results.values() if item.action_type == WorkflowActionType.GENERAL_RESPONSE),
            None,
        )
        if general:
            return WorkflowExecutionResult(message=str(general), plan=current, actions=results)
        if draft:
            failed_media = any(
                item.state == WorkflowActionState.FAILED
                and item.action_type in {WorkflowActionType.IMAGE_SEARCH, WorkflowActionType.IMAGE_GENERATE}
                for item in results.values()
            )
            prefix = "I could not add an image, so the draft is unchanged.\n\n" if failed_media else ""
            return WorkflowExecutionResult(message=prefix + draft_marker(draft), plan=current, actions=results)
        return WorkflowExecutionResult(
            message="I could not complete that email workflow safely.", plan=current, actions=results,
        )


pending_workflow_store = PendingWorkflowStore()
workflow_planner = WorkflowPlanner(pending_store=pending_workflow_store)
workflow_executor = WorkflowExecutor(pending_store=pending_workflow_store)


def plan_known_workflow(
    prompt: str,
    history: list[dict],
    owner: str,
    *,
    is_masked: bool = False,
) -> WorkflowPlan | None:
    return workflow_planner.plan(prompt, history, owner, is_masked=is_masked)


def execute_workflow_for_chat(
    plan: WorkflowPlan,
    *,
    admin_key: str | None = None,
    abort_event: threading.Event | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> str:
    return workflow_executor.execute(
        plan,
        admin_key=admin_key,
        abort_event=abort_event,
        status_callback=status_callback,
    ).message
