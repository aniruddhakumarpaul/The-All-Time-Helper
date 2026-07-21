import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

OPENROUTER_KEY_ENVS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_TOKEN",
)

GEMINI_KEY_ENVS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)

_CLOUD_CIRCUIT_LOCK = threading.Lock()
_CLOUD_CIRCUIT_OPEN_UNTIL: dict[str, float] = {}
_CLOUD_CIRCUIT_REASON: dict[str, str] = {}
_CLOUD_FAILURE_REASONS = {
    "rate_limited",
    "network_unavailable",
    "authentication_failed",
    "timed_out",
    "provider_unavailable",
}


def _cloud_circuit_cooldown_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("HELPER_CLOUD_RETRY_SECONDS", "45")))
    except ValueError:
        return 45.0


FREE_AGENT_PRIMARY = "openrouter/google/gemma-4-26b-a4b-it:free"
FREE_AGENT_CLASSIFIER = FREE_AGENT_PRIMARY
FREE_AGENT_FALLBACKS = (
    "openrouter/google/gemma-4-31b-it:free",
)
FREE_AGENT_CHAIN = (FREE_AGENT_PRIMARY, *FREE_AGENT_FALLBACKS)

CLOUD_MODEL_CONFIG = {
    "agentic-pro": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-free-agent": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-free-code": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-free-general": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-auto": {
        "provider": "openrouter",
        "model": "openrouter/openrouter/auto",
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_CHAIN,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-glm-agentic": {
        "provider": "openrouter",
        "model": "openrouter/z-ai/glm-5.2",
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_CHAIN,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-claude-sonnet-5": {
        "provider": "openrouter",
        "model": "openrouter/anthropic/claude-sonnet-5",
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_CHAIN,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-kimi-code": {
        "provider": "openrouter",
        "model": "openrouter/moonshotai/kimi-k2.7-code",
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_CHAIN,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-laguna-code": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-nemotron-free": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "gemma4-openrouter": {
        "provider": "openrouter",
        "model": FREE_AGENT_PRIMARY,
        "classifier_model": FREE_AGENT_CLASSIFIER,
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
}


def _looks_fake_key(value: str | None) -> bool:
    cleaned = str(value or "").strip().strip('"').strip("'")
    lowered = cleaned.lower()
    return not cleaned or lowered.startswith("your-") or "placeholder" in lowered or "optional-" in lowered


def get_next_groq_key():
    return None


def _dynamic_cloud_config(model_id: str) -> dict | None:
    cleaned = str(model_id or "").strip()
    lowered = cleaned.lower()
    if lowered.startswith("gemini/"):
        provider_model = cleaned
    elif lowered.startswith("gemini-"):
        provider_model = f"gemini/{cleaned}"
    else:
        return None
    return {
        "provider": "gemini",
        "model": provider_model,
        "classifier_model": provider_model,
        "fallback_models": (),
        "key_envs": GEMINI_KEY_ENVS,
    }


def supports_native_vision(model_id: str) -> bool:
    """Return whether the selected route accepts image input in its native chat request."""
    cleaned = str(model_id or "").strip()
    if cleaned == "gemma4:e2b":
        return True
    config = CLOUD_MODEL_CONFIG.get(cleaned) or _dynamic_cloud_config(cleaned)
    if not config:
        return False
    if config.get("provider") == "gemini":
        return True
    model = str(config.get("model") or "").lower()
    return any(marker in model for marker in ("gemma-4", "claude-", "openrouter/auto"))

def is_cloud_model(model_id: str) -> bool:
    return model_id in CLOUD_MODEL_CONFIG or _dynamic_cloud_config(model_id) is not None


def get_cloud_config(model_id: str) -> dict:
    config = CLOUD_MODEL_CONFIG.get(model_id) or _dynamic_cloud_config(model_id)
    if config is None:
        raise ValueError(f"Unknown cloud model '{model_id}'.")
    return config


def get_cloud_api_key(model_id: str, explicit_key: str = None) -> str:
    load_dotenv(ROOT_DIR / ".env", override=False)
    cfg = get_cloud_config(model_id)
    if explicit_key and not _looks_fake_key(explicit_key):
        return str(explicit_key).strip().strip('"').strip("'")
    for env_name in cfg["key_envs"]:
        key = os.getenv(env_name)
        if not _looks_fake_key(key):
            return str(key).strip().strip('"').strip("'")
    raise ValueError(f"{' or '.join(cfg['key_envs'])} missing - required for {model_id}.")


def has_cloud_credentials(model_id: str = "agentic-pro") -> bool:
    try:
        get_cloud_api_key(model_id)
        return True
    except ValueError:
        return False


def mark_cloud_runtime_failure(
    model_id: str = "agentic-pro",
    cooldown_seconds: float | None = None,
    reason: str = "provider_unavailable",
) -> None:
    cooldown = _cloud_circuit_cooldown_seconds() if cooldown_seconds is None else max(0.0, float(cooldown_seconds))
    normalized_reason = reason if reason in _CLOUD_FAILURE_REASONS else "provider_unavailable"
    with _CLOUD_CIRCUIT_LOCK:
        _CLOUD_CIRCUIT_OPEN_UNTIL[model_id] = time.monotonic() + cooldown
        _CLOUD_CIRCUIT_REASON[model_id] = normalized_reason


def mark_cloud_runtime_success(model_id: str = "agentic-pro") -> None:
    with _CLOUD_CIRCUIT_LOCK:
        _CLOUD_CIRCUIT_OPEN_UNTIL.pop(model_id, None)
        _CLOUD_CIRCUIT_REASON.pop(model_id, None)


def reset_cloud_runtime_state() -> None:
    with _CLOUD_CIRCUIT_LOCK:
        _CLOUD_CIRCUIT_OPEN_UNTIL.clear()
        _CLOUD_CIRCUIT_REASON.clear()


def cloud_runtime_status(model_id: str = "agentic-pro") -> dict:
    configured = has_cloud_credentials(model_id)
    now = time.monotonic()
    with _CLOUD_CIRCUIT_LOCK:
        open_until = _CLOUD_CIRCUIT_OPEN_UNTIL.get(model_id, 0.0)
        reason = _CLOUD_CIRCUIT_REASON.get(model_id)
        if open_until and open_until <= now:
            _CLOUD_CIRCUIT_OPEN_UNTIL.pop(model_id, None)
            _CLOUD_CIRCUIT_REASON.pop(model_id, None)
            open_until = 0.0
            reason = None
    retry_after = max(0, int(max(0.0, open_until - now) + 0.999))
    if not configured:
        reason = "not_configured"
    elif retry_after == 0:
        reason = None
    return {
        "configured": configured,
        "available": configured and retry_after == 0,
        "degraded": configured and retry_after > 0,
        "retry_after_seconds": retry_after,
        "reason": reason,
    }

def cloud_runtime_available(model_id: str = "agentic-pro") -> bool:
    return bool(cloud_runtime_status(model_id)["available"])


def cloud_candidate_models(cfg: dict) -> list[str]:
    models = [cfg["model"]]
    for model in cfg.get("fallback_models", ()):
        if model not in models:
            models.append(model)
    return models


def is_rate_limit_error(error: Exception) -> bool:
    message = str(error).lower()
    markers = (
        "rate_limit",
        "rate limit",
        "429",
        "too many requests",
        "402",
        "404",
        "not found",
        "no endpoints available",
        "more credits",
        "fewer max_tokens",
        "insufficient credits",
        "can only afford",
    )
    return any(marker in message for marker in markers)
