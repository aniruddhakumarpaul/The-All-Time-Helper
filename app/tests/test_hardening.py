import asyncio
import os
import sqlite3
import unittest
from unittest.mock import mock_open, patch


class HardeningTests(unittest.TestCase):
    def test_inference_queue_cancel_marks_job_cancelled(self):
        from app.inference_queue import InferenceJob, InferenceQueue
        import threading

        async def run_test():
            queue = InferenceQueue()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            abort_event = threading.Event()
            job = InferenceJob(
                id="job-123",
                owner="user@example.com",
                fn=lambda: "should not run",
                abort_event=abort_event,
                result_future=future,
            )
            queue._active_jobs["job-123"] = job

            self.assertTrue(queue.cancel("job-123", "user@example.com"))
            self.assertTrue(abort_event.is_set())
            self.assertTrue(future.done())
            self.assertEqual(future.result(), "Operation cancelled.")

        asyncio.run(run_test())

    def test_gemini_models_are_cloud_routed(self):
        from app.logic.agents import _detect_intent

        intent = _detect_intent("hello there", "gemini-1.5-flash-latest", history=[])

        self.assertEqual(intent["complexity"], "direct")
        self.assertFalse(intent["is_local"])

    def test_gemma4_openrouter_is_additive_cloud_route(self):
        from app.logic.agents import CLOUD_MODEL_CONFIG, _detect_intent

        intent = _detect_intent("hello there", "gemma4-openrouter", history=[])

        self.assertEqual(CLOUD_MODEL_CONFIG["gemma4-openrouter"]["provider"], "openrouter")
        self.assertEqual(
            CLOUD_MODEL_CONFIG["gemma4-openrouter"]["model"],
            "openrouter/google/gemma-4-26b-a4b-it:free",
        )
        self.assertEqual(intent["complexity"], "direct")
        self.assertFalse(intent["is_local"])

    def test_gemma4_openrouter_has_cloud_fallbacks(self):
        from app.logic.agents import CLOUD_MODEL_CONFIG, _cloud_candidate_models

        models = _cloud_candidate_models(CLOUD_MODEL_CONFIG["gemma4-openrouter"])

        self.assertEqual(models[0], "openrouter/google/gemma-4-26b-a4b-it:free")
        self.assertIn("openrouter/google/gemma-4-31b-it:free", models)
        self.assertIn("openrouter/google/gemma-3-27b-it", models)
        self.assertEqual(len(models), len(set(models)))

    def test_local_gemma4_route_is_unchanged(self):
        from app.logic.agents import _detect_intent

        intent = _detect_intent("hello there", "gemma4:e2b", history=[])

        self.assertTrue(intent["is_local"])

    def test_agentic_pro_email_intent_stays_cloud_single_agent(self):
        from app.logic.agents import _detect_intent

        intent = _detect_intent(
            "send an email to teammate@example.com saying hello",
            "agentic-pro",
            history=[],
        )

        self.assertTrue(intent["requires_tools"])
        self.assertEqual(intent["complexity"], "single")
        self.assertFalse(intent["is_local"])

    def test_cloud_image_email_workflow_uses_direct_tools(self):
        from app.logic import agents
        from app.logic.exceptions import AgentFastExit

        payload = (
            'EMAIL_DRAFT_PAYLOAD:{"recipient":"email@example.com","subject":"Requested Image and Description",'
            '"body":"Please find the requested image attached.","tone":"modern",'
            '"attachment_content":"base64","attachment_filename":"abstract_image.png"}'
        )

        with patch.object(agents.tools.image_generate_tool, "func", return_value="![abstract](https://example.com/img.png)") as image_tool:
            with patch.object(agents.tools.send_email_tool, "func", side_effect=AgentFastExit(payload)) as email_tool:
                with patch.object(agents, "_execute_cloud", side_effect=AssertionError("cloud should not run")):
                    result = agents.run_helper_agent(
                        "generate an image and attach it to email@example.com",
                        target_model="gemma4-openrouter",
                        history=[],
                        user_id="user@example.com",
                    )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        image_tool.assert_called_once()
        email_tool.assert_called_once()
        self.assertEqual(email_tool.call_args.kwargs["attachment_content"], "https://example.com/img.png")

    def test_direct_image_email_result_starts_with_payload(self):
        from app.logic import agents
        from app.logic.exceptions import AgentFastExit

        payload = (
            'EMAIL_DRAFT_PAYLOAD:{"recipient":"friend@example.com","subject":"Image",'
            '"body":"Attached image description","tone":"modern",'
            '"attachment_content":"base64","attachment_filename":"abstract.png"}'
        )
        intent = {"is_local": False, "requires_tools": True, "complexity": "swarm", "is_sensitive": False}

        with patch.object(agents.tools.image_generate_tool, "func", return_value="![abstract](https://example.com/img.png)"):
            with patch.object(agents.tools.send_email_tool, "func", side_effect=AgentFastExit(payload)):
                result = agents._try_direct_tool_execution(
                    "generate an image and attach it to friend@example.com",
                    intent,
                    history=[],
                    target_model="gemma4-openrouter",
                )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))

    def test_attach_above_image_uses_history_email_and_direct_widget(self):
        from app.logic import agents
        from app.logic.exceptions import AgentFastExit

        payload = (
            'EMAIL_DRAFT_PAYLOAD:{"recipient":"aniruddha@example.com","subject":"Requested Image",'
            '"body":"","tone":"modern","attachment_content":"base64","attachment_filename":"upscaled_test.jpg"}'
        )
        history = [
            {"role": "user", "content": "send it to aniruddha@example.com"},
            {
                "role": "assistant",
                "content": "![elegant sketch](/static/uploads/upscaled_test.jpg)",
            },
            {"role": "user", "content": "attach the above in our email template and dont fill content"},
        ]

        with patch("app.logic.tools.resolve_chat_image", return_value=("base64", "upscaled_test.jpg")):
            with patch.object(agents.requests, "post", side_effect=TimeoutError("skip local drafting")):
                with patch.object(agents.tools.send_email_tool, "func", side_effect=AgentFastExit(payload)) as email_tool:
                    with patch.object(agents, "_execute_local", side_effect=AssertionError("local agent should not run")):
                        result = agents.run_helper_agent(
                            "attach the above in our email template and dont fill content",
                            target_model="gemma4:e2b",
                            history=history,
                            user_id="user@example.com",
                        )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        email_tool.assert_called_once()
        self.assertEqual(email_tool.call_args.kwargs["recipient"], "aniruddha@example.com")
        self.assertEqual(email_tool.call_args.kwargs["body"], "")
        self.assertEqual(email_tool.call_args.kwargs["attachment_content"], "base64")

    def test_attach_above_asks_when_image_and_text_are_both_recent(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "send it to aniruddha@example.com"},
            {"role": "assistant", "content": "Important project notes that should be considered for the email body."},
            {"role": "assistant", "content": "![elegant sketch](/static/uploads/upscaled_test.jpg)"},
        ]
        intent = {"is_local": True, "requires_tools": True, "complexity": "single", "is_sensitive": False}

        with patch.object(agents.tools.send_email_tool, "func", side_effect=AssertionError("should ask first")):
            result = agents._try_direct_tool_execution(
                "attach the above in our email template",
                intent,
                history=history,
                target_model="gemma4:e2b",
            )

        self.assertIn("both an image and text", result)
        self.assertIn("image only", result)
        self.assertIn("summary", result)

    def test_structured_code_prompt_stays_verbatim_and_does_not_trigger_email_attachment_routing(self):
        from app.logic import agents

        code_prompt = (
            "import os\n"
            "import sys\n"
            "\n"
            "def run_indexing():\n"
            "    print('hello world')\n"
        )
        explain_prompt = "explain this in detail and syntax by syntax"
        history = [{"role": "user", "content": code_prompt}]

        with patch.object(agents, "_analyze_prompt_via_llm", return_value=None):
            normalized = agents._normalize_prompt_for_intent(code_prompt)
            self.assertEqual(normalized, code_prompt.rstrip())

            reconstructed = agents._reconstruct_contextual_prompt(code_prompt, history)
            self.assertEqual(reconstructed, code_prompt)

            context = agents._latest_attachable_history_context(history, explain_prompt)
            self.assertIn("\n", context["text"])
            self.assertTrue(context["text"].startswith("import os"))

            self.assertFalse(agents._is_above_attachment_request(explain_prompt))
            self.assertEqual(agents._attachment_choice_from_prompt(explain_prompt, context), "unknown")

            intent = agents._detect_intent(explain_prompt, "gemma4-openrouter", history=history)
            self.assertFalse(intent["requires_tools"])
            self.assertEqual(intent["complexity"], "direct")

    def test_combined_pasted_code_explanation_overrides_bad_prompt_analyzer(self):
        from app.logic import agents

        prompt = (
            "import os\n"
            "import sys\n"
            "\n"
            "def run_indexing():\n"
            "    print('hello world')\n"
            "\n"
            "explain the above in details and syntax by syntax and also tell why this syntax is used."
        )
        bad_analysis = {"requires_tools": True, "complexity": "swarm", "category": "code"}

        with patch.object(agents, "_analyze_prompt_via_llm", return_value=bad_analysis) as analyzer:
            intent = agents._detect_intent(prompt, "gemma4-openrouter", history=[])

        analyzer.assert_not_called()
        self.assertFalse(intent["requires_tools"])
        self.assertEqual(intent["complexity"], "direct")

    def test_direct_pasted_code_explanation_suppresses_raw_tool_call_leak(self):
        from app.logic import agents

        prompt = (
            "import os\n"
            "import sys\n"
            "\n"
            "def run_indexing():\n"
            "    print('hello world')\n"
            "\n"
            "explain the above in details and syntax by syntax and also tell why this syntax is used."
        )
        intent = {"requires_tools": False, "complexity": "direct", "is_local": False}
        raw = (
            "[send_email_tool(recipient='user@example.com', "
            "subject='Detailed Explanation', body='Here is the explanation.')]"
        )

        result = agents._harden_result(raw, None, target_model="gemma4-openrouter", intent=intent, user_prompt=prompt)

        self.assertNotIn("send_email_tool", result)
        self.assertNotIn("EMAIL_DRAFT_PAYLOAD:", result)
        self.assertIn("invalid tool-call plan", result)

    def test_email_template_field_update_returns_widget_not_tool_json(self):
        from app.logic import agents
        import json

        previous_payload = {
            "recipient": "",
            "subject": "Requested Image and Description",
            "body": "",
            "tone": "modern",
            "attachment_content": "base64-image",
            "attachment_filename": "upscaled_99424748-d7ce-468b-8740-1cfd85f2bb24.jpg",
        }
        prompt = (
            '[Attached Context 1]\n"""\nTO SUBJECT EMAIL TONE Modern BODY ATTACHMENT...\n"""\n'
            'fill aniruddha24680kumarpaul@gmail.com for the \'to\' and '
            '"here is a sketch of a elegant woman" for the body'
        )
        history = [
            {"role": "assistant", "content": f"EMAIL_DRAFT_PAYLOAD:{json.dumps(previous_payload)}"},
            {"role": "user", "content": prompt},
        ]

        with patch.object(agents, "_execute_cloud", side_effect=AssertionError("cloud should not run")):
            with patch.object(agents, "_execute_local", side_effect=AssertionError("local agent should not run")):
                result = agents.run_helper_agent(
                    prompt,
                    target_model="gemma4-openrouter",
                    history=history,
                    user_id="user@example.com",
                )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "aniruddha24680kumarpaul@gmail.com")
        self.assertEqual(payload["body"], "here is a sketch of a elegant woman")
        self.assertEqual(payload["subject"], "Requested Image and Description")
        self.assertEqual(payload["attachment_content"], "base64-image")
        self.assertNotIn("send_email_tool", result)

    def test_log_prompt_image_email_regression(self):
        from app.logic import agents
        import base64
        import json

        prompt = (
            "generate a image and attach it to the email and describe the image in the content section "
            "and send the email to aniruddha24680kumarpaul@gmail.com"
        )
        image_bytes = self._png_bytes()
        image_url = "https://example.com/img.png"

        with patch.object(agents.tools.image_generate_tool, "func", return_value=f"![abstract]({image_url})"):
            with patch("app.logic.tools.requests.get", return_value=self._image_response(image_bytes)):
                result = agents.run_helper_agent(
                    prompt,
                    target_model="gemma4-openrouter",
                    history=[],
                    user_id="user@example.com",
                )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:")[1])
        self.assertEqual(payload["recipient"], "aniruddha24680kumarpaul@gmail.com")
        self.assertNotIn(image_url, payload["attachment_content"])
        self.assertEqual(base64.b64decode(payload["attachment_content"]), image_bytes)
        self.assertGreater(len(payload["attachment_content"]), len(image_url))

    def test_openrouter_429_returns_friendly_message(self):
        from app.logic import agents

        intent = {"requires_tools": False, "is_local": False, "complexity": "direct", "is_sensitive": False}
        context = {"final_prompt": "hello", "memory_block": "", "history_context": "", "resolved_email": None}

        with patch.object(agents, "_get_cloud_api_key", return_value="test-key"):
            with patch("litellm.completion", side_effect=Exception("Error code: 429 - rate limit exceeded")):
                result = agents._execute_cloud(intent, context, "gemma4-openrouter", None, history=[])

        self.assertIn("temporarily rate limited", result)
        self.assertIn("OpenRouter", result)

    def test_memory_query_without_user_context_returns_empty(self):
        from app.logic import memory

        called = False

        class FakeCollection:
            def query(self, **kwargs):
                nonlocal called
                called = True
                return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

        memory.user_context.set(None)
        with patch.object(memory, "collection", FakeCollection()):
            self.assertEqual(memory.query_memory("architecture decision", user_id=None), [])

        self.assertFalse(called)

    def test_memory_query_enforces_user_filter(self):
        from app.logic import memory

        calls = []

        class FakeCollection:
            def query(self, **kwargs):
                calls.append(kwargs)
                return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

        with patch.object(memory, "collection", FakeCollection()):
            memory.query_memory("architecture decision", user_id="user@example.com")

        self.assertEqual(calls[0]["where"], {"user_id": "user@example.com"})
        self.assertEqual(
            calls[1]["where"],
            {"$and": [{"user_id": "user@example.com"}, {"type": "tool_rule"}]},
        )

    def test_memory_query_chroma_failure_returns_empty(self):
        from app.logic import memory

        class BrokenCollection:
            def query(self, **kwargs):
                raise RuntimeError("Error executing plan: Internal error: Error finding id")

        with patch.object(memory, "collection", BrokenCollection()):
            with patch.object(memory, "_memory_unhealthy_reason", None):
                self.assertEqual(memory.query_memory("architecture decision", user_id="user@example.com"), [])

    def test_context_assembly_continues_when_memory_fails(self):
        from app.logic import agents

        intent = {"requires_tools": False, "is_local": False}
        with patch.object(agents, "query_memory", side_effect=RuntimeError("memory failed")):
            context = agents._assemble_context(
                "architecture plan",
                img_data=None,
                history=[],
                intent=intent,
                user_id="user@example.com",
            )

        self.assertEqual(context["memory_block"], "")
        self.assertEqual(context["final_prompt"], "architecture plan")

    def test_ngrok_cors_update_uses_middleware_kwargs(self):
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from app import main

        test_app = FastAPI()
        test_app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:9000"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        with patch.object(main, "ALLOWED_ORIGINS", ["http://localhost:9000"]):
            added = main._append_cors_origin(test_app, "https://example.ngrok-free.dev/")

        self.assertEqual(added, ["https://example.ngrok-free.dev"])
        self.assertIn("https://example.ngrok-free.dev", test_app.user_middleware[0].kwargs["allow_origins"])
        self.assertNotIn("example.ngrok-free.dev", test_app.user_middleware[0].kwargs["allow_origins"])

    def test_theme_runtime_updates_html_and_body_theme_attributes(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        ui_js = (root / "static" / "js" / "ui.js").read_text(encoding="utf-8")
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertIn("document.documentElement.setAttribute('data-theme', theme)", ui_js)
        self.assertIn("document.body.setAttribute('data-theme', theme)", ui_js)
        self.assertIn(
            "document.body.setAttribute('data-theme', window.__initialThemeIsDark ? 'dark' : 'light')",
            template,
        )

    def test_upscale_status_returns_registry_ready(self):
        from app import main

        with patch.object(
            main.UpscaleManager,
            "get_status",
            return_value={"status": "ready", "url": "/static/uploads/upscaled_registry.jpg"},
        ):
            result = asyncio.run(main.get_upscale_status("registry"))

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["url"], "/static/uploads/upscaled_registry.jpg")

    def test_upscale_status_recovers_ready_from_disk(self):
        from app import main

        job_id = "test_status_disk"
        upload_dir = os.path.join(main.base_dir, "static", "uploads")
        file_path = os.path.join(upload_dir, f"upscaled_{job_id}.jpg")
        os.makedirs(upload_dir, exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(b"fake image bytes")

        try:
            with patch.object(main.UpscaleManager, "get_status", return_value=None):
                result = asyncio.run(main.get_upscale_status(job_id))
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["url"], f"/static/uploads/upscaled_{job_id}.jpg")

    def test_upscale_status_reports_missing_when_registry_and_disk_miss(self):
        from app import main

        job_id = "test_status_missing"
        file_path = os.path.join(main.base_dir, "static", "uploads", f"upscaled_{job_id}.jpg")
        if os.path.exists(file_path):
            os.remove(file_path)

        with patch.object(main.UpscaleManager, "get_status", return_value=None):
            result = asyncio.run(main.get_upscale_status(job_id))

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["error"], "Job not found")

    def test_email_simulation_writes_log(self):
        from app.logic.tools import send_or_simulate_email

        mocked_open = mock_open()
        with patch.dict(os.environ, {"EMAIL_MODE": "SIMULATE"}):
            with patch("builtins.open", mocked_open):
                result = send_or_simulate_email(
                    recipient="friend@example.com",
                    subject="Test",
                    body="Hello from the test suite.",
                    tone="modern",
                )

        self.assertIn("SIMULATE SUCCESS", result)
        mocked_open.assert_any_call("simulated_emails.log", "a", encoding="utf-8")

    def test_send_email_tool_downloads_url_attachment_bytes(self):
        from app.logic import tools
        from app.logic.exceptions import AgentFastExit
        import base64
        import json

        image_bytes = self._png_bytes()
        image_url = "https://example.com/generated.png?uid=abc"

        with patch("app.logic.tools.requests.get", return_value=self._image_response(image_bytes)):
            with self.assertRaises(AgentFastExit) as ctx:
                tools.send_email_tool.func(
                    recipient="friend@example.com",
                    subject="Image",
                    body="Attached.",
                    attachment_content=image_url,
                    attachment_filename="generated.png",
                )

        payload = json.loads(ctx.exception.result.split("EMAIL_DRAFT_PAYLOAD:")[1])
        self.assertEqual(base64.b64decode(payload["attachment_content"]), image_bytes)
        self.assertNotIn(image_url, payload["attachment_content"])
        self.assertGreater(len(payload["attachment_content"]), len(image_url))

    def test_send_email_tool_url_download_failure_fails_closed(self):
        from app.logic import tools
        from app.logic.exceptions import AgentFastExit

        image_url = "https://example.com/generated.png"

        with patch("app.logic.tools.requests.get", side_effect=TimeoutError("timed out")):
            with self.assertRaises(AgentFastExit) as ctx:
                tools.send_email_tool.func(
                    recipient="friend@example.com",
                    subject="Image",
                    body="Attached.",
                    attachment_content=image_url,
                    attachment_filename="generated.png",
                )

        self.assertTrue(ctx.exception.result.startswith("ERROR:"))
        self.assertNotIn(image_url, ctx.exception.result)

    def test_send_or_simulate_email_downloads_url_before_logging(self):
        from app.logic.tools import send_or_simulate_email

        image_bytes = self._png_bytes()
        mocked_open = mock_open()

        with patch.dict(os.environ, {"EMAIL_MODE": "SIMULATE"}):
            with patch("app.logic.tools.requests.get", return_value=self._image_response(image_bytes)):
                with patch("builtins.open", mocked_open):
                    result = send_or_simulate_email(
                        recipient="friend@example.com",
                        subject="Image",
                        body="Attached.",
                        tone="modern",
                        attachment_content="https://example.com/generated.png",
                        attachment_filename="generated.png",
                    )

        self.assertIn("SIMULATE SUCCESS", result)
        written = "".join(call.args[0] for call in mocked_open().write.call_args_list)
        self.assertIn(f"Attachment: generated.png ({len(image_bytes)} bytes)", written)

    def test_chat_repository_rejects_oversized_sync(self):
        from app.repository import ChatRepository

        db = self._chat_db()

        with self.assertRaisesRegex(ValueError, "Too many chats"):
            ChatRepository.sync_user_chats(
                db,
                "user@example.com",
                [{"id": str(i), "title": "x", "ms": []} for i in range(201)],
            )

    def test_chat_repository_truncates_large_chat_history(self):
        from app.repository import ChatRepository

        db = self._chat_db()

        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            [{"id": "c1", "title": "Long", "ms": [{"c": str(i)} for i in range(501)]}],
        )

        stored = ChatRepository.get_chats_for_user(db, "user@example.com")[0]
        self.assertEqual(len(stored["ms"]), 500)
        self.assertEqual(stored["ms"][0]["c"], "1")
        self.assertIn("updated_at", stored)
        self.assertIn("updatedAt", stored)

    def test_chat_repository_newest_sync_wins(self):
        from app.repository import ChatRepository

        db = self._chat_db()

        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            {"chats": [{"id": "c1", "title": "New", "ms": [{"c": "new"}], "updatedAt": 2000}], "deleted_chat_ids": []},
        )
        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            {"chats": [{"id": "c1", "title": "Old", "ms": [{"c": "old"}], "updatedAt": 1000}], "deleted_chat_ids": []},
        )

        stored = ChatRepository.get_chats_for_user(db, "user@example.com")[0]
        self.assertEqual(stored["title"], "New")
        self.assertEqual(stored["ms"][0]["c"], "new")

    def test_chat_repository_newer_incoming_updates_existing(self):
        from app.repository import ChatRepository

        db = self._chat_db()

        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            {"chats": [{"id": "c1", "title": "Old", "ms": [{"c": "old"}], "updatedAt": 1000}], "deleted_chat_ids": []},
        )
        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            {"chats": [{"id": "c1", "title": "New", "ms": [{"c": "new"}], "updatedAt": 2000}], "deleted_chat_ids": []},
        )

        stored = ChatRepository.get_chats_for_user(db, "user@example.com")[0]
        self.assertEqual(stored["title"], "New")
        self.assertEqual(stored["ms"][0]["c"], "new")

    def test_chat_repository_legacy_array_does_not_delete_missing_chats(self):
        from app.repository import ChatRepository

        db = self._chat_db()

        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            [
                {"id": "c1", "title": "One", "ms": [], "updatedAt": 1000},
                {"id": "c2", "title": "Two", "ms": [], "updatedAt": 1000},
            ],
        )
        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            [{"id": "c1", "title": "One updated", "ms": [], "updatedAt": 2000}],
        )

        stored = ChatRepository.get_chats_for_user(db, "user@example.com")
        self.assertEqual({chat["id"] for chat in stored}, {"c1", "c2"})
        self.assertEqual(next(chat for chat in stored if chat["id"] == "c1")["title"], "One updated")

    def test_chat_repository_tombstone_deletes_only_current_user_chat(self):
        from app.repository import ChatRepository

        db = self._chat_db()

        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            [{"id": "c1", "title": "Delete me", "ms": [], "updatedAt": 1000}],
        )
        ChatRepository.sync_user_chats(
            db,
            "other@example.com",
            [{"id": "c2", "title": "Keep me", "ms": [], "updatedAt": 1000}],
        )
        ChatRepository.sync_user_chats(
            db,
            "user@example.com",
            {"chats": [], "deleted_chat_ids": ["c1", "c2"]},
        )

        self.assertEqual(ChatRepository.get_chats_for_user(db, "user@example.com"), [])
        self.assertEqual(len(ChatRepository.get_chats_for_user(db, "other@example.com")), 1)

    def test_harden_result_parses_email_json_fallback(self):
        from app.logic.agents import _harden_result
        import json

        raw_json_input = """
        Here is the email draft you requested:
        ```json
        {
            "to": "recipient@example.com",
            "subject": "Greeting",
            "body": "Hello world",
            "tone": "modern"
        }
        ```
        """
        result = _harden_result(raw_json_input, None)
        self.assertIn("EMAIL_DRAFT_PAYLOAD:", result)
        
        # Verify JSON content is parsed correctly
        payload_str = result.split("EMAIL_DRAFT_PAYLOAD:")[1].strip()
        data = json.loads(payload_str)
        self.assertEqual(data["recipient"], "recipient@example.com")
        self.assertEqual(data["subject"], "Greeting")
        self.assertEqual(data["body"], "Hello world")
        self.assertEqual(data["tone"], "modern")

    def test_harden_result_parses_nested_email_json_fallback(self):
        from app.logic.agents import _harden_result
        import json

        raw_nested_input = """
        Agent: Senior Executive Secretary
        Final Answer:
        ```json
        [
          {
            "send_email_tool": {
              "recipient": "aniruddha24680kumarpaul@gmail.com",
              "subject": "Regarding your request",
              "body": "I'm happy to help. Please let me know what you'd like me to do next.",
              "attachment_content": null,
              "tone": "modern"
            }
          }
        ]
        ```
        """
        result = _harden_result(raw_nested_input, None)
        self.assertIn("EMAIL_DRAFT_PAYLOAD:", result)
        
        # Verify JSON content is parsed correctly
        payload_str = result.split("EMAIL_DRAFT_PAYLOAD:")[1].strip()
        data = json.loads(payload_str)
        self.assertEqual(data["recipient"], "aniruddha24680kumarpaul@gmail.com")
        self.assertEqual(data["subject"], "Regarding your request")
        self.assertEqual(data["body"], "I'm happy to help. Please let me know what you'd like me to do next.")
        self.assertEqual(data["tone"], "modern")

    def test_visual_typo_intent_detection(self):
        from app.logic.agents import _detect_intent
        
        # Test with typos
        intent = _detect_intent("i want a acrilic scetch of a beautiful woman", "gemma4-openrouter", history=[])
        
        # Because it requires tools (single complexity for visual tasks), it should route correctly
        self.assertTrue(intent["requires_tools"])
        self.assertEqual(intent["complexity"], "single")

    def test_visual_followup_refinement_generates_from_history(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "i want a acrilic scetch of a beautifull woman"},
            {"role": "assistant", "content": "What pose, attire, and style do you want?"},
            {"role": "user", "content": "doo as fit for the most elegance"},
            {"role": "assistant", "content": "Please provide subject, scene, and color palette details."},
        ]

        with patch.object(agents.tools.image_generate_tool, "func", return_value="![image](https://example.com/image.png)") as image_tool:
            with patch.object(agents, "_execute_local", side_effect=AssertionError("local chat should not run")):
                result = agents.run_helper_agent(
                    "long gown, perfect styled hair, proper display of femininity",
                    target_model="gemma4:e2b",
                    history=history,
                    user_id="user@example.com",
                )

        self.assertEqual(result, "![image](https://example.com/image.png)")
        image_tool.assert_called_once()
        description = image_tool.call_args.kwargs["description"].lower()
        self.assertIn("acrylic sketch", description)
        self.assertIn("beautiful woman", description)
        self.assertIn("long gown", description)
        self.assertIn("perfect styled hair", description)
        self.assertIn("femininity", description)

    def test_visual_followup_frustration_generates_from_accumulated_specs(self):
        from app.logic import agents

        history = [
            {"role": "user", "content": "i want a acrilic scetch of a beautifull woman"},
            {"role": "assistant", "content": "What style should I use?"},
            {"role": "user", "content": "long gown, perfect styled hair, proper display of femininity"},
            {"role": "assistant", "content": "Do you prefer a color palette or background?"},
        ]

        with patch.object(agents.tools.image_generate_tool, "func", return_value="![image](https://example.com/image.png)") as image_tool:
            result = agents.run_helper_agent(
                "stop asking me so many questions",
                target_model="gemma4:e2b",
                history=history,
                user_id="user@example.com",
            )

        self.assertEqual(result, "![image](https://example.com/image.png)")
        image_tool.assert_called_once()
        description = image_tool.call_args.kwargs["description"].lower()
        self.assertIn("long gown", description)
        self.assertNotIn("stop asking", description)

    def test_visual_followup_context_switch_does_not_inherit_image_task(self):
        from app.logic.agents import _resolve_visual_task_continuation

        history = [
            {"role": "user", "content": "i want a acrilic scetch of a beautifull woman"},
            {"role": "assistant", "content": "What pose, attire, and style do you want?"},
        ]

        self.assertIsNone(_resolve_visual_task_continuation("what is python?", history))
        self.assertIsNone(_resolve_visual_task_continuation("send an email to friend@example.com", history))
        self.assertIsNone(_resolve_visual_task_continuation("search the web for acrylic painters", history))

    def test_visual_followup_does_not_duplicate_current_message_when_history_includes_it(self):
        from app.logic.agents import _resolve_visual_task_continuation

        current = "long gown, perfect styled hair, proper display of femininity"
        history = [
            {"role": "user", "content": "i want a acrilic scetch of a beautifull woman"},
            {"role": "assistant", "content": "What pose, attire, and style do you want?"},
            {"role": "user", "content": current},
        ]

        resolved = _resolve_visual_task_continuation(current, history)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.lower().count("long gown"), 1)
        self.assertEqual(resolved.lower().count("perfect styled hair"), 1)

    def test_prompt_analyzer_llm_structured_parsing(self):
        from app.logic.agents import _analyze_prompt_via_llm
        from unittest.mock import MagicMock
        
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"requires_tools": true, "complexity": "single", "category": "visual"}'
        
        with patch("litellm.completion", return_value=mock_response):
            # Using a cloud model like gemma4-openrouter to trigger litellm branch
            analysis = _analyze_prompt_via_llm("acrilic scetch", "gemma4-openrouter")
            
            self.assertIsNotNone(analysis)
            self.assertTrue(analysis["requires_tools"])
            self.assertEqual(analysis["complexity"], "single")
            self.assertEqual(analysis["category"], "visual")

    def test_harden_result_skips_cloud(self):
        from app.logic.agents import _harden_result
        
        raw_json_input = """
        {
            "to": "recipient@example.com",
            "subject": "Greeting",
            "body": "Hello world"
        }
        """
        # For a cloud model, _harden_result should NOT execute fallback parsing
        result = _harden_result(raw_json_input, None, target_model="gemma4-openrouter")
        self.assertNotIn("EMAIL_DRAFT_PAYLOAD:", result)
        self.assertEqual(result.strip(), raw_json_input.strip())

    @staticmethod
    def _chat_db():
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE chats (id TEXT PRIMARY KEY, user_email TEXT, title TEXT, messages_json TEXT, updated_at REAL)"
        )
        return db

    @staticmethod
    def _png_bytes():
        return b"\x89PNG\r\n\x1a\n" + (b"0" * 2048)

    @staticmethod
    def _image_response(content):
        class Response:
            status_code = 200
            headers = {"content-type": "image/png"}

            def __init__(self, body):
                self.content = body

        return Response(content)


if __name__ == "__main__":
    unittest.main()
