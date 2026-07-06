OR_KEY_ENV = "OPENROUTER_" + "API_KEY"

OPENROUTER_MODEL_CONFIG = {
    "agentic-pro": {
        "provider": "openrouter",
        "model": "openrouter/z-ai/glm-5.2",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/anthropic/claude-sonnet-5",
            "openrouter/moonshotai/kimi-k2.7-code",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
    "openrouter-auto": {
        "provider": "openrouter",
        "model": "openrouter/openrouter/auto",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/z-ai/glm-5.2",
            "openrouter/anthropic/claude-sonnet-5",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
    "openrouter-claude-sonnet-5": {
        "provider": "openrouter",
        "model": "openrouter/anthropic/claude-sonnet-5",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/z-ai/glm-5.2",
            "openrouter/moonshotai/kimi-k2.7-code",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
    "openrouter-kimi-code": {
        "provider": "openrouter",
        "model": "openrouter/moonshotai/kimi-k2.7-code",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/z-ai/glm-5.2",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
    "openrouter-laguna-code": {
        "provider": "openrouter",
        "model": "openrouter/poolside/laguna-xs-2.1:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/cohere/north-mini-code:free",
            "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
            "openrouter/z-ai/glm-5.2",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
    "openrouter-nemotron-free": {
        "provider": "openrouter",
        "model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/poolside/laguna-xs-2.1:free",
            "openrouter/cohere/north-mini-code:free",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
    "gemma4-openrouter": {
        "provider": "openrouter",
        "model": "openrouter/z-ai/glm-5.2",
        "classifier_model": "openrouter/cohere/north-mini-code:free",
        "fallback_models": (
            "openrouter/anthropic/claude-sonnet-5",
            "openrouter/moonshotai/kimi-k2.7-code",
            "openrouter/poolside/laguna-xs-2.1:free",
        ),
        "key_envs": (OR_KEY_ENV,),
    },
}
