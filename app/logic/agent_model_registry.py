import os
import threading


CLOUD_MODEL_CONFIG = {
    "agentic-pro": {
        "provider": "groq",
        "model": "groq/llama-3.3-70b-versatile",
        "classifier_model": "groq/llama-3.1-8b-instant",
        "key_envs": ("GROQ_API_KEY",),
    },
    "gemma4-cloud": {
        "provider": "groq",
        "model": "groq/llama-3.1-8b-instant",
        "classifier_model": "groq/llama-3.1-8b-instant",
        "key_envs": ("GROQ_API_KEY",),
    },
    "gemma4-openrouter": {
        "provider": "openrouter",
        "model": "openrouter/google/gemma-4-26b-a4b-it:free",
        "classifier_model": "openrouter/google/gemma-4-26b-a4b-it:free",
        "fallback_models": (
            "openrouter/google/gemma-4-31b-it:free",
            "openrouter/google/gemma-3-27b-it",
            "openrouter/google/gemma-3-12b-it",
        ),
        "key_envs": ("OPENROUTER_API_KEY",),
    },
    "gemini-1.5-flash-latest": {
        "provider": "gemini",
        "model": "gemini/gemini-1.5-flash-latest",
        "classifier_model": "gemini/gemini-1.5-flash-latest",
        "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
    "gemini-1.5-pro-latest": {
        "provider": "gemini",
        "model": "gemini/gemini-1.5-pro-latest",
        "classifier_model": "gemini/gemini-1.5-flash-latest",
        "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
}

_key_index = 0
_key_lock = threading.Lock()


def _groq_keys() -> list[str]:
    return [key for key in (os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_KEY_BACKUP")) if key]


def get_next_groq_key():
    global _key_index
    keys = _groq_keys()
    if not keys:
        return None
    with _key_lock:
        key = keys[_key_index % len(keys)]
        _key_index += 1
    return key


def is_cloud_model(model_id: str) -> bool:
    return model_id in CLOUD_MODEL_CONFIG


def get_cloud_config(model_id: str) -> dict:
    if model_id not in CLOUD_MODEL_CONFIG:
        raise ValueError(f"Unknown cloud model '{model_id}'.")
    return CLOUD_MODEL_CONFIG[model_id]


def get_cloud_api_key(model_id: str, explicit_key: str = None) -> str:
    cfg = get_cloud_config(model_id)
    if explicit_key:
        return explicit_key
    if cfg["provider"] == "groq":
        key = get_next_groq_key()
        if key:
            return key
    for env_name in cfg["key_envs"]:
        key = os.getenv(env_name)
        if key:
            return key
    raise ValueError(f"{' or '.join(cfg['key_envs'])} missing - required for {model_id}.")


def cloud_candidate_models(cfg: dict) -> list[str]:
    models = [cfg["model"]]
    for model in cfg.get("fallback_models", ()):
        if model not in models:
            models.append(model)
    return models


def is_rate_limit_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in ("rate_limit", "rate limit", "429", "too many requests"))
