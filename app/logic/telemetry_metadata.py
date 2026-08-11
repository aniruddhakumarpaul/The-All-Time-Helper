"""Explicit low-cardinality classifications for exported telemetry metadata."""

from __future__ import annotations

from typing import Any

from app.logic.agent_model_registry import trusted_telemetry_model_ids


TELEMETRY_PROVIDERS = frozenset({"openrouter", "ollama", "gemini", "groq", "unknown"})
FALLBACK_REASONS = frozenset({
    "rate_limited",
    "network_unavailable",
    "authentication_failed",
    "timed_out",
    "provider_unavailable",
    "unknown",
})
USAGE_OPERATIONS = frozenset({"chat", "unknown"})
USAGE_SOURCES = frozenset({
    "provider",
    "litellm",
    "ollama",
    "intent_classifier",
    "complexity_classifier",
    "email_draft",
    "vision",
    "unknown",
})
USAGE_STATUSES = frozenset({"completed", "failed", "unknown"})
COST_SOURCES = frozenset({"litellm", "local", "unknown"})
WORKFLOW_STATES = frozenset({
    "pending", "running", "completed", "failed", "blocked", "cancelled", "paused",
    "interrupted", "unknown_external_result", "unknown",
})
QUEUE_LANES = frozenset({"inference", "tool", "unknown"})
OPERATION_OUTCOMES = frozenset({"completed", "failed", "cancelled", "timed_out", "unknown"})
MEMORY_OPERATIONS = frozenset({"write", "query", "delete", "unknown"})
WORKFLOW_INTENTS = frozenset({
    "draft_email", "update_email_draft", "search_web", "search_image", "generate_image",
    "attach_to_draft", "request_email_approval", "deliver_email", "general_response", "unknown",
})
WORKFLOW_ACTIONS = frozenset({
    "web_search", "image_search", "image_generate", "build_email_draft", "update_email_draft",
    "attach_image", "deliver_email", "general_response", "unknown",
})


def _finite_label(value: object, allowed: frozenset[str], default: str = "unknown") -> str:
    try:
        cleaned = str(value or "").strip().lower()
    except Exception:
        return default
    return cleaned if cleaned in allowed else default


def safe_provider_label(value: object) -> str:
    return _finite_label(value, TELEMETRY_PROVIDERS)


def provider_from_model(model: object) -> str:
    try:
        lowered = str(model or "").strip().lower()
    except Exception:
        return "unknown"
    for provider in ("openrouter", "ollama", "gemini", "groq"):
        if lowered.startswith(f"{provider}/"):
            return provider
    return "unknown"


def safe_telemetry_model_label(model: object, *, provider: object = None) -> str:
    """Classify models by trusted registry or a finite provider-specific bucket."""
    try:
        cleaned = str(model or "").strip()
    except Exception:
        return "unknown"
    if cleaned in trusted_telemetry_model_ids():
        return cleaned
    provider_label = safe_provider_label(provider)
    if provider_label == "unknown":
        provider_label = provider_from_model(cleaned)
    if provider_label in {"openrouter", "ollama", "gemini"}:
        return f"{provider_label}/custom"
    return "unknown"


def safe_fallback_reason(value: object) -> str:
    return _finite_label(value, FALLBACK_REASONS)


def safe_operation_label(value: object) -> str:
    return _finite_label(value, USAGE_OPERATIONS)


def safe_source_label(value: object) -> str:
    return _finite_label(value, USAGE_SOURCES)


def safe_status_label(value: object) -> str:
    return _finite_label(value, USAGE_STATUSES)


def safe_cost_source_label(value: object) -> str:
    return _finite_label(value, COST_SOURCES)


def safe_workflow_state(value: object) -> str:
    return _finite_label(value, WORKFLOW_STATES)


def safe_queue_lane(value: object) -> str:
    return _finite_label(value, QUEUE_LANES)


def safe_operation_outcome(value: object) -> str:
    return _finite_label(value, OPERATION_OUTCOMES)


def safe_memory_operation(value: object) -> str:
    return _finite_label(value, MEMORY_OPERATIONS)


def safe_workflow_intent(value: object) -> str:
    return _finite_label(value, WORKFLOW_INTENTS)


def safe_workflow_action(value: object) -> str:
    return _finite_label(value, WORKFLOW_ACTIONS)


def safe_error_category(error: Any) -> str | None:
    if error is None:
        return None
    name = type(error).__name__.lower()
    if "rate" in name and "limit" in name:
        return "rate_limited"
    if any(marker in name for marker in ("auth", "permission", "credential")):
        return "authentication_failed"
    if any(marker in name for marker in ("timeout", "timedout")):
        return "timed_out"
    if any(marker in name for marker in ("connection", "network", "dns", "proxy")):
        return "network_unavailable"
    return "provider_unavailable"


def safe_error_category_label(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _finite_label(value, FALLBACK_REASONS)
