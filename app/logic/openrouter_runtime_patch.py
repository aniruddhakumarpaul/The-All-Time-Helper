from app.logic import agent_model_registry as registry
from app.logic.openrouter_model_registry import OPENROUTER_MODEL_CONFIG


def apply_openrouter_cloud_registry() -> None:
    registry.CLOUD_MODEL_CONFIG.clear()
    registry.CLOUD_MODEL_CONFIG.update(OPENROUTER_MODEL_CONFIG)
    registry.get_next_groq_key = lambda: None
