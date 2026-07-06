import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

OPENROUTER_KEY_ENVS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_TOKEN",
)

FREE_AGENT_FALLBACKS = (
    "openrouter/cohere/north-mini-code:free",
    "openrouter/nvidia/nemotron-nano-9b-v2:free",
)

CLOUD_MODEL_CONFIG = {
    "agentic-pro": {
        "provider": "openrouter",
        "model": "openrouter/poolside/laguna-xs-2.1:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-free-agent": {
        "provider": "openrouter",
        "model": "openrouter/poolside/laguna-xs-2.1:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": FREE_AGENT_FALLBACKS,
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-free-code": {
        "provider": "openrouter",
        "model": "openrouter/cohere/north-mini-code:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/poolside/laguna-xs-2.1:free",
            "openrouter/nvidia/nemotron-nano-9b-v2:free",
        ),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-free-general": {
        "provider": "openrouter",
        "model": "openrouter/nvidia/nemotron-nano-9b-v2:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/cohere/north-mini-code:free",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-auto": {
        "provider": "openrouter",
        "model": "openrouter/openrouter/auto",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": ("openrouter/poolside/laguna-xs-2.1:free",),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-glm-agentic": {
        "provider": "openrouter",
        "model": "openrouter/z-ai/glm-5.2",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/poolside/laguna-xs-2.1:free",
            "openrouter/cohere/north-mini-code:free",
        ),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-claude-sonnet-5": {
        "provider": "openrouter",
        "model": "openrouter/anthropic/claude-sonnet-5",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/poolside/laguna-xs-2.1:free",
            "openrouter/cohere/north-mini-code:free",
        ),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-kimi-code": {
        "provider": "openrouter",
        "model": "openrouter/moonshotai/kimi-k2.7-code",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/cohere/north-mini-code:free",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-laguna-code": {
        "provider": "openrouter",
        "model": "openrouter/poolside/laguna-xs-2.1:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": ("openrouter/cohere/north-mini-code:free",),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "openrouter-nemotron-free": {
        "provider": "openrouter",
        "model": "openrouter/nvidia/nemotron-nano-9b-v2:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": ("openrouter/cohere/north-mini-code:free",),
        "key_envs": OPENROUTER_KEY_ENVS,
    },
    "gemma4-openrouter": {
        "provider": "openrouter",
        "model": "openrouter/poolside/laguna-xs-2.1:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
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


def is_cloud_model(model_id: str) -> bool:
    return model_id in CLOUD_MODEL_CONFIG


def get_cloud_config(model_id: str) -> dict:
    if model_id not in CLOUD_MODEL_CONFIG:
        raise ValueError(f"Unknown cloud model '{model_id}'.")
    return CLOUD_MODEL_CONFIG[model_id]


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
        "more credits",
        "fewer max_tokens",
        "insufficient credits",
        "can only afford",
    )
    return any(marker in message for marker in markers)
