import unittest

from app.logic.agent_context import ContextRuntime, assemble_context
from app.logic.agent_cloud import _image_description, _is_direct_image_generation


class DummyVision:
    def __init__(self):
        self.calls = []

    def analyze_chat_images(self, targets, prompt):
        self.calls.append((targets, prompt))
        return {"url": targets[0], "description": "dummy image description"}


class VisualFocusRoutingTests(unittest.TestCase):
    def _runtime(self, vision):
        return ContextRuntime(
            clean_prompt=lambda value: value,
            image_items=lambda value: value if isinstance(value, list) else [value],
            image_base64=lambda value: value,
            image_source=lambda value: value,
            save_image=lambda value: None,
            process_cloud=lambda image, key: None,
            process_local=lambda image: "local description",
            next_groq_key=lambda: None,
            vision_system=vision,
            query_memory=lambda *args, **kwargs: [],
            logger=type("Logger", (), {"error": lambda *args, **kwargs: None})(),
        )

    def test_new_image_prompt_does_not_analyze_prior_chat_image(self):
        vision = DummyVision()
        history = [{"role": "assistant", "content": "![apple](https://image.pollinations.ai/prompt/an%20apple?uid=old)"}]
        result = assemble_context(
            "content will be an image of an annabelle doll with dim aesthetic and realistic horror effect",
            None,
            history,
            {"requires_tools": True, "is_local": True},
            runtime=self._runtime(vision),
        )
        self.assertEqual(vision.calls, [])
        self.assertNotIn("CURRENT VISUAL FOCUS", result["final_prompt"])

    def test_explicit_prior_image_reference_still_analyzes_history_image(self):
        vision = DummyVision()
        history = [{"role": "assistant", "content": "![apple](https://image.pollinations.ai/prompt/an%20apple?uid=old)"}]
        result = assemble_context(
            "describe this image",
            None,
            history,
            {"requires_tools": False, "is_local": True},
            runtime=self._runtime(vision),
        )
        self.assertEqual(len(vision.calls), 1)
        self.assertIn("CURRENT VISUAL FOCUS", result["final_prompt"])

    def test_content_will_be_image_routes_to_cloud_direct_generator(self):
        prompt = "content will be an image of an annabelle doll with dim aesthetic and realistic horror effect"
        self.assertTrue(_is_direct_image_generation(prompt))
        self.assertIn("annabelle doll", _image_description(prompt).lower())
        self.assertNotIn("content will be", _image_description(prompt).lower())


if __name__ == "__main__":
    unittest.main()
