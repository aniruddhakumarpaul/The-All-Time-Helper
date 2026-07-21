import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.logic import attachment_store
from app.logic.agent_context import ContextRuntime, assemble_context
from app.logic.vision_pipeline import VisionPipeline
from app.routes.chat import (
    _hydrate_current_image_payload,
    _hydrate_history_attachment_references,
)


class DummyVision:
    def __init__(self):
        self.calls = []

    def analyze_chat_images(self, targets, prompt):
        self.calls.append((targets, prompt))
        return {"url": targets[0], "description": "stored image description"}


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 80, 160)).save(output, format="PNG")
    return output.getvalue()


class AttachmentVisionPipelineTests(unittest.TestCase):
    def test_current_and_historical_attachment_ids_are_owner_hydrated(self):
        owner = "owner@example.com"
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes("sample.png", "image/png", png_bytes(), owner)
                current = _hydrate_current_image_payload([saved], owner)
                history = _hydrate_history_attachment_references(
                    [{"role": "user", "content": "look at this", "attachments": [saved]}],
                    owner,
                )

                self.assertEqual(base64.b64decode(current[0]["content"]), png_bytes())
                self.assertEqual(base64.b64decode(history[0]["attachments"][0]["content"]), png_bytes())
                with self.assertRaises(attachment_store.AttachmentStoreError):
                    _hydrate_current_image_payload([saved], "other@example.com")

    def test_visual_followup_uses_history_attachment_without_leaking_base64_into_prompt(self):
        encoded = base64.b64encode(png_bytes()).decode("ascii")
        vision = DummyVision()
        runtime = ContextRuntime(
            clean_prompt=lambda value: value,
            image_items=lambda value: value if isinstance(value, list) else [value],
            image_base64=lambda item: item.get("content") if isinstance(item, dict) else item,
            image_source=lambda item: item.get("content") if isinstance(item, dict) else item,
            save_image=lambda value: None,
            process_cloud=lambda image, key: None,
            process_local=lambda image: "local description",
            next_groq_key=lambda: None,
            vision_system=vision,
            query_memory=lambda *args, **kwargs: [],
            logger=type("Logger", (), {"error": lambda *args, **kwargs: None})(),
        )

        result = assemble_context(
            "describe this image",
            None,
            [{"role": "user", "content": "attached", "attachments": [{"content": encoded}]}],
            {"requires_tools": False, "is_local": True},
            runtime=runtime,
        )

        self.assertEqual(vision.calls[0][0], [encoded])
        self.assertIn("Image: uploaded attachment", result["final_prompt"])
        self.assertNotIn(encoded, result["final_prompt"])

    def test_vision_accepts_base64_but_rejects_arbitrary_local_paths(self):
        pipeline = VisionPipeline()
        raw = png_bytes()
        encoded = pipeline._encode_image(base64.b64encode(raw).decode("ascii"))
        self.assertEqual(base64.b64decode(encoded), raw)

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside.png"
            outside.write_bytes(raw)
            self.assertIsNone(pipeline._encode_image(str(outside)))


class MultimodalExecutionTests(unittest.TestCase):
    def test_native_context_uses_validated_structured_image_input(self):
        encoded = base64.b64encode(png_bytes()).decode("ascii")
        pipeline = VisionPipeline()

        def should_not_run(*args, **kwargs):
            raise AssertionError("native multimodal path should not run a separate perception model")

        runtime = ContextRuntime(
            clean_prompt=lambda value: value,
            image_items=lambda value: value if isinstance(value, list) else [value],
            image_base64=lambda item: item.get("content") if isinstance(item, dict) else item,
            image_source=lambda item: item.get("content") if isinstance(item, dict) else item,
            save_image=should_not_run,
            process_cloud=should_not_run,
            process_local=should_not_run,
            next_groq_key=lambda: None,
            vision_system=pipeline,
            query_memory=lambda *args, **kwargs: [],
            logger=type("Logger", (), {"error": lambda *args, **kwargs: None})(),
        )

        result = assemble_context(
            "Explain this visual composition.",
            [{"content": encoded, "content_type": "image/png"}],
            [],
            {"requires_tools": False, "is_local": False, "native_vision": True},
            runtime=runtime,
        )

        self.assertEqual(len(result["image_inputs"]), 1)
        self.assertTrue(result["image_inputs"][0]["data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(result["final_prompt"], "Explain this visual composition.")
        self.assertNotIn(encoded, result["final_prompt"])

    def test_cloud_and_local_messages_do_not_duplicate_current_turn(self):
        from app.logic.agent_cloud import _conversation_messages as cloud_messages
        from app.logic.agent_local import _conversation_messages as local_messages

        context = {
            "final_prompt": "Describe this image.",
            "request_prompt": "Describe this image.",
            "memory_block": "",
            "image_inputs": [{"data_url": "data:image/png;base64,AAAA", "base64": "AAAA"}],
        }
        history = [
            {"role": "user", "content": "Earlier request"},
            {"role": "assistant", "content": "Earlier response"},
            {"role": "user", "content": "Describe this image."},
        ]

        cloud = cloud_messages(context, history)
        local = local_messages(context, history)

        self.assertEqual(sum(message.get("content") == "Describe this image." for message in cloud), 0)
        self.assertEqual(cloud[-1]["content"][0]["text"], "Describe this image.")
        self.assertEqual(cloud[-1]["content"][1]["type"], "image_url")
        self.assertEqual(sum(message.get("content") == "Describe this image." for message in local), 1)
        self.assertEqual(local[-1]["images"], ["AAAA"])

    def test_openrouter_gemma_and_local_gemma_support_native_vision(self):
        from app.logic.agent_model_registry import supports_native_vision

        self.assertTrue(supports_native_vision("agentic-pro"))
        self.assertTrue(supports_native_vision("gemma4-openrouter"))
        self.assertTrue(supports_native_vision("gemma4:e2b"))
        self.assertFalse(supports_native_vision("gemma2:2b"))
        self.assertFalse(supports_native_vision("openrouter-kimi-code"))

    def test_dominant_color_uses_deterministic_pixels_without_model_call(self):
        pipeline = VisionPipeline()
        encoded = base64.b64encode(png_bytes()).decode("ascii")
        with patch("app.logic.vision_pipeline.requests.post", side_effect=AssertionError("model call is forbidden")):
            result = pipeline.analyze_chat_images([encoded], "What is the dominant color in this image?")

        self.assertEqual(result["model"], "deterministic-vision")
        self.assertEqual(result["description"], "The dominant color in the image is blue.")
    def test_local_perception_cache_avoids_duplicate_model_calls_and_logs_no_payload(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"response": "The dominant color is blue."}

        pipeline = VisionPipeline()
        pipeline.model_name = "moondream"
        pipeline.fallback_model = ""
        encoded = base64.b64encode(png_bytes()).decode("ascii")
        with patch("app.logic.vision_pipeline.requests.post", return_value=Response()) as request:
            with patch("app.logic.vision_pipeline.logger.info") as info:
                first = pipeline.analyze_chat_images([encoded], "Describe this image.")
                second = pipeline.analyze_chat_images([encoded], "Describe this image.")

        self.assertEqual(first["description"], "The dominant color is blue.")
        self.assertTrue(second["cached"])
        request.assert_called_once()
        log_arguments = " ".join(str(value) for call in info.call_args_list for value in call.args)
        self.assertNotIn(encoded, log_arguments)

    def test_empty_lightweight_vision_output_gets_one_bounded_retry(self):
        class Response:
            status_code = 200

            def __init__(self, content):
                self.content = content

            def json(self):
                return {"response": self.content}

        pipeline = VisionPipeline()
        pipeline.model_name = "moondream"
        pipeline.fallback_model = ""
        encoded = base64.b64encode(png_bytes()).decode("ascii")
        responses = [Response(""), Response("A blue square.")]
        with patch("app.logic.vision_pipeline.requests.post", side_effect=responses) as request:
            result = pipeline.analyze_chat_images([encoded], "Describe the central object.", allow_fallback=False)

        self.assertEqual(result["description"], "A blue square.")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(request.call_args_list[0].kwargs["json"]["prompt"].startswith("Describe this image briefly."))

    def test_fast_local_perception_failure_preserves_native_image_fallback(self):
        encoded = base64.b64encode(png_bytes()).decode("ascii")

        class EmptyVision:
            @staticmethod
            def prepare_image(source):
                return {
                    "base64": encoded,
                    "data_url": f"data:image/png;base64,{encoded}",
                    "media_type": "image/png",
                    "sha256": "hash",
                    "byte_size": len(png_bytes()),
                }

            @staticmethod
            def analyze_chat_images(targets, prompt, *, allow_fallback=True):
                return None

        runtime = ContextRuntime(
            clean_prompt=lambda value: value,
            image_items=lambda value: value if isinstance(value, list) else [value],
            image_base64=lambda item: item.get("content") if isinstance(item, dict) else item,
            image_source=lambda item: item.get("content") if isinstance(item, dict) else item,
            save_image=lambda value: None,
            process_cloud=lambda image, key: None,
            process_local=lambda image: None,
            next_groq_key=lambda: None,
            vision_system=EmptyVision(),
            query_memory=lambda *args, **kwargs: [],
            logger=type("Logger", (), {"error": lambda *args, **kwargs: None})(),
        )

        result = assemble_context(
            "What is the dominant color in this image?",
            [{"content": encoded, "content_type": "image/png"}],
            [],
            {
                "requires_tools": False,
                "is_local": True,
                "native_vision": False,
                "fast_local_vision": True,
            },
            runtime=runtime,
        )

        self.assertEqual(len(result["image_inputs"]), 1)
        self.assertTrue(result["visual_input_present"])
        self.assertEqual(result["image_description"], "No image context available.")
    def test_simple_local_visual_question_returns_perception_without_second_model(self):
        from app.logic import agents

        context = {
            "final_prompt": "visual facts",
            "memory_block": "",
            "history_context": "",
            "image_description": "Image 1: The dominant color is blue.",
            "image_inputs": [],
            "resolved_email": None,
        }
        chunks = []
        intent = {"is_sensitive": False, "requires_tools": False, "complexity": "direct", "is_local": True}
        with patch.object(agents, "_try_direct_tool_execution", return_value=None):
            with patch.object(agents, "_assemble_context", return_value=context):
                with patch.object(agents, "_execute_local", side_effect=AssertionError("second model pass is forbidden")):
                    result = agents.run_helper_agent(
                        "What is the dominant color in this image?",
                        img_data="encoded-image",
                        target_model="gemma4:e2b",
                        history=[],
                        intent=intent,
                        chunk_callback=chunks.append,
                    )

        self.assertEqual(result, "The dominant color is blue.")
        self.assertEqual(chunks, ["The dominant color is blue."])

    def test_cloud_visual_request_reaches_one_native_model_call(self):
        from app.logic import agents

        encoded = base64.b64encode(png_bytes()).decode("ascii")
        intent = {"is_sensitive": False, "requires_tools": False, "complexity": "direct", "is_local": False}
        with patch.object(agents, "_try_direct_tool_execution", return_value=None):
            with patch.object(agents.vision_sys, "analyze_chat_images", side_effect=AssertionError("pre-analysis is forbidden")):
                with patch.object(agents, "_execute_cloud", return_value="Native visual answer") as execute_cloud:
                    result = agents.run_helper_agent(
                        "Explain this visual composition for a brand campaign.",
                        img_data={"content": encoded, "content_type": "image/png"},
                        target_model="agentic-pro",
                        history=[],
                        intent=intent,
                    )

        self.assertEqual(result, "Native visual answer")
        context = execute_cloud.call_args.args[1]
        self.assertEqual(len(context["image_inputs"]), 1)
        self.assertEqual(context["image_description"], "No image context available.")

class ToolRoutingRegressionTests(unittest.TestCase):
    def test_only_proven_deterministic_actions_use_tool_lane(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "i want to see an arcilic scenery"},
            {"role": "assistant", "content": "Would you prefer a forest scene or mountains?"},
        ]

        self.assertTrue(agents.is_deterministic_tool_lane_request(
            "genetrate image of your architecture",
            [],
        ))
        self.assertTrue(agents.is_deterministic_tool_lane_request(
            "anything you like which will be pleasing",
            history,
        ))
        self.assertTrue(agents.is_deterministic_tool_lane_request(
            "search the web for current Python release notes",
            [],
        ))
        self.assertTrue(agents.is_deterministic_tool_lane_request(
            "find an image of Saturn",
            [],
        ))
        self.assertFalse(agents.is_deterministic_tool_lane_request(
            "we have an image tool; inspect why it is not working",
            [],
        ))
        self.assertFalse(agents.is_deterministic_tool_lane_request(
            "write a Python image processing function",
            [],
        ))
        self.assertFalse(agents.is_deterministic_tool_lane_request(
            "generate an image and email it to owner@example.com",
            [],
        ))

    def test_exported_chat_creative_latitude_followup_calls_image_tool_once(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "i want to see an arcilic scenery"},
            {"role": "assistant", "content": "Would you prefer a forest scene or mountains?"},
            {"role": "user", "content": "anything you like which will be pleasing"},
        ]
        result_markdown = "![acrylic scenery](https://example.com/generated.png)"

        with patch.object(agents.tools.image_generate_tool, "func", return_value=result_markdown) as image_tool:
            with patch.object(agents, "_execute_local", side_effect=AssertionError("chat model should not run")):
                result = agents.run_helper_agent(
                    "anything you like which will be pleasing",
                    target_model="gemma4:e2b",
                    history=history,
                    user_id="owner@example.com",
                )

        self.assertEqual(result, result_markdown)
        image_tool.assert_called_once()
        self.assertIn("acrylic scenery", image_tool.call_args.kwargs["description"].lower())

    def test_exported_chat_produce_one_followup_recovers_unresolved_visual_task(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "i want to see an arcilic scenery"},
            {"role": "assistant", "content": "Would you prefer a forest scene or mountains?"},
            {"role": "user", "content": "anything you like which will be pleasing"},
            {"role": "assistant", "content": "Here is a picture: [link to image]"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Would you like me to produce the scenic image?"},
        ]

        resolved = agents._resolve_visual_task_continuation("yes produce one", history)

        self.assertIsNotNone(resolved)
        self.assertTrue(agents._is_image_generation_prompt(resolved))
        self.assertIn("acrylic scenery", resolved.lower())

    def test_completed_visual_task_is_not_generated_again(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "create an acrylic scenery"},
            {"role": "assistant", "content": "![scene](https://example.com/generated.png)"},
        ]

        self.assertIsNone(agents._resolve_visual_task_continuation("produce one", history))

    def test_tool_capability_discussion_does_not_trigger_generation(self):
        from app.logic import agents
        from app.logic.agent_intent import is_image_generation_request

        prompt = "we already have an image generation tool, check why tool calling did not work"
        self.assertFalse(is_image_generation_request(prompt))
        with patch.object(agents, "_analyze_prompt_via_llm", side_effect=AssertionError("fast route expected")):
            intent = agents._detect_intent(prompt, "gemma4-openrouter", history=[])
        self.assertFalse(intent["requires_tools"])
        self.assertIsNone(
            agents._try_direct_tool_execution(
                prompt,
                {"requires_tools": True, "is_local": False, "complexity": "single"},
                history=[],
                target_model="gemma4-openrouter",
            )
        )

    def test_initial_acrylic_scenery_request_is_direct_generation(self):
        from app.logic import agents

        result_markdown = "![scene](https://example.com/generated.png)"
        with patch.object(agents.tools.image_generate_tool, "func", return_value=result_markdown) as image_tool:
            with patch.object(agents, "_execute_local", side_effect=AssertionError("chat model should not run")):
                result = agents.run_helper_agent(
                    "i want to see an arcilic scenery",
                    target_model="gemma4:e2b",
                    history=[],
                    user_id="owner@example.com",
                )

        self.assertEqual(result, result_markdown)
        image_tool.assert_called_once()

    def test_ordinary_code_work_bypasses_tool_agents(self):
        from app.logic import agents

        with patch.object(agents, "_analyze_prompt_via_llm", side_effect=AssertionError("code fast route expected")):
            intent = agents._detect_intent(
                "Write a Python function that validates an API response and add unit tests.",
                "agentic-pro",
                history=[],
            )

        self.assertFalse(intent["requires_tools"])
        self.assertEqual(intent["complexity"], "direct")

    def test_live_generate_typo_uses_direct_tool(self):
        from app.logic import agents

        result_markdown = "![architecture](https://example.com/generated.png)"
        with patch.object(agents.tools.image_generate_tool, "func", return_value=result_markdown) as image_tool:
            with patch.object(agents, "_execute_cloud", side_effect=AssertionError("Crew/cloud model should not run")):
                result = agents.run_helper_agent(
                    "genetrate image of your architecture",
                    target_model="agentic-pro",
                    history=[],
                    user_id="owner@example.com",
                )

        self.assertEqual(result, result_markdown)
        image_tool.assert_called_once()
        self.assertEqual(image_tool.call_args.kwargs["description"], "your architecture")

    def test_crewai_execution_is_noninteractive(self):
        root = Path(__file__).resolve().parents[2]
        agents_source = (root / "app" / "logic" / "agents.py").read_text(encoding="utf-8")
        local_source = (root / "app" / "logic" / "agent_local.py").read_text(encoding="utf-8")
        cloud_source = (root / "app" / "logic" / "agent_cloud.py").read_text(encoding="utf-8")

        self.assertIn("CREWAI_TRACING_ENABLED', 'false'", agents_source)
        self.assertIn("tracing=False", local_source)
        self.assertIn("tracing=False", cloud_source)

class FrontendRecoveryPipelineTests(unittest.TestCase):
    def test_expired_sessions_recover_and_extension_loading_is_ordered(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        api = (root / "static" / "js" / "api.js").read_text(encoding="utf-8")
        app = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        chat_route = (root / "app" / "routes" / "chat.py").read_text(encoding="utf-8")

        self.assertIn("window.handleHelperUnauthorized", bootstrap)
        self.assertIn("script.async = false;", bootstrap)
        self.assertNotIn("script.defer = true;", bootstrap)
        self.assertIn("window.handleHelperUnauthorized?.(response);", api)
        self.assertIn("window.handleHelperUnauthorized?.(response)", app)
        self.assertIn("chat.ms.slice(0, -1).map", app)
        self.assertIn("_hydrate_current_image_payload(_normalize_chat_image_payload(req), current_user)", chat_route)
        self.assertIn("_hydrate_history_attachment_references(req.history, current_user)", chat_route)
        self.assertIn("lane=execution_lane", chat_route)
        self.assertIn("intent=direct_tool_intent", chat_route)

class MemoryRecoveryPipelineTests(unittest.TestCase):
    def test_transient_query_failure_retries_and_clears_the_circuit(self):
        from app.logic import memory

        class FlakyCollection:
            def __init__(self):
                self.query_calls = 0

            def query(self, **kwargs):
                self.query_calls += 1
                if self.query_calls == 1:
                    raise RuntimeError("transient query failure")
                return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

            def count(self):
                return 0

        collection = FlakyCollection()
        with patch.object(memory, "collection", collection), patch.object(memory, "_memory_unhealthy_reason", None), patch.object(memory, "_memory_retry_at", 0.0):
            self.assertEqual(memory.query_memory("architecture decision", user_id="owner@example.com"), [])
            self.assertFalse(memory.memory_runtime_status()["healthy"])
            self.assertEqual(memory.query_memory("architecture decision", user_id="owner@example.com"), [])
            self.assertEqual(collection.query_calls, 1)

            memory._memory_retry_at = 0.0
            self.assertEqual(memory.query_memory("architecture decision", user_id="owner@example.com"), [])
            self.assertEqual(collection.query_calls, 3)
            self.assertIsNone(memory._memory_unhealthy_reason)

if __name__ == "__main__":
    unittest.main()