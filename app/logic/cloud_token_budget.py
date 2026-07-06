import os
from functools import wraps

DEFAULT_CLOUD_MAX_TOKENS = 4096
DEFAULT_FREE_CLOUD_MAX_TOKENS = 2048
MIN_CLOUD_MAX_TOKENS = 256
MAX_CLOUD_MAX_TOKENS = 12000
MAX_FREE_CLOUD_MAX_TOKENS = 4096


def _read_budget(env_name: str, default: int, max_value: int) -> int:
    try:
        configured = int(os.getenv(env_name, str(default)))
    except ValueError:
        configured = default
    return max(MIN_CLOUD_MAX_TOKENS, min(configured, max_value))


def cloud_output_token_budget(model: object = None) -> int:
    model_name = str(model or "")
    if model_name.endswith(":free"):
        return _read_budget("OPENROUTER_FREE_MAX_TOKENS", DEFAULT_FREE_CLOUD_MAX_TOKENS, MAX_FREE_CLOUD_MAX_TOKENS)
    return _read_budget("OPENROUTER_MAX_TOKENS", DEFAULT_CLOUD_MAX_TOKENS, MAX_CLOUD_MAX_TOKENS)


def _is_cloud_model_name(model: object) -> bool:
    return str(model or "").startswith("openrouter/")


def _cap_kwargs(kwargs: dict) -> dict:
    model = kwargs.get("model")
    if not _is_cloud_model_name(model):
        return kwargs
    budget = cloud_output_token_budget(model)
    current = kwargs.get("max_tokens")
    try:
        current_int = int(current) if current is not None else None
    except (TypeError, ValueError):
        current_int = None
    if current_int is None or current_int > budget:
        kwargs["max_tokens"] = budget
    return kwargs


def apply_cloud_token_budget() -> None:
    """Patch LiteLLM and CrewAI calls so OpenRouter requests do not ask for huge output ceilings."""
    try:
        import litellm

        if not getattr(litellm.completion, "__helper_token_budget_patched__", False):
            original_completion = litellm.completion

            @wraps(original_completion)
            def completion_with_budget(*args, **kwargs):
                if args and "model" not in kwargs:
                    kwargs["model"] = args[0]
                    args = args[1:]
                return original_completion(*args, **_cap_kwargs(kwargs))

            completion_with_budget.__helper_token_budget_patched__ = True
            litellm.completion = completion_with_budget
    except Exception:
        pass

    try:
        import crewai

        current_llm = getattr(crewai, "LLM", None)
        if current_llm and not getattr(current_llm, "__helper_token_budget_patched__", False):
            original_llm = current_llm

            @wraps(original_llm)
            def llm_with_budget(*args, **kwargs):
                if args and "model" not in kwargs:
                    kwargs["model"] = args[0]
                    args = args[1:]
                return original_llm(*args, **_cap_kwargs(kwargs))

            llm_with_budget.__helper_token_budget_patched__ = True
            crewai.LLM = llm_with_budget
    except Exception:
        pass
