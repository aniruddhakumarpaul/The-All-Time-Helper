import unittest


class OpenRouterRuntimeTests(unittest.TestCase):
    def test_routes_initializer_switches_cloud_registry_to_openrouter(self):
        import app.routes  # noqa: F401 - import applies runtime cloud registry setup
        from app.logic.agent_model_registry import CLOUD_MODEL_CONFIG, get_cloud_config

        self.assertEqual(get_cloud_config("agentic-pro")["provider"], "openrouter")
        self.assertEqual(CLOUD_MODEL_CONFIG["agentic-pro"]["model"], "openrouter/z-ai/glm-5.2")
        self.assertIn("openrouter-claude-sonnet-5", CLOUD_MODEL_CONFIG)
        self.assertIn("openrouter-laguna-code", CLOUD_MODEL_CONFIG)
        self.assertNotIn("gemini-1.5-flash-latest", CLOUD_MODEL_CONFIG)

    def test_pending_sensitive_request_is_recovered_before_key_attempts(self):
        from app.routes.chat import _find_pending_sensitive_request

        history = [
            {"role": "user", "content": "send an email to person@example.com saying deployment is ready"},
            {"role": "assistant", "content": "ERROR: AUTH_REQUIRED. Please provide your Admin Key."},
            {"role": "user", "content": "wrongkey", "masked": True},
            {"role": "assistant", "content": "ERROR: AUTH_REQUIRED. Incorrect admin key."},
            {"role": "user", "content": "correctkey", "masked": True},
        ]

        self.assertEqual(
            _find_pending_sensitive_request(history),
            "send an email to person@example.com saying deployment is ready",
        )


if __name__ == "__main__":
    unittest.main()
