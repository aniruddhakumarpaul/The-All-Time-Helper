import asyncio
import os
import sqlite3
import unittest
from unittest.mock import mock_open, patch


class HardeningTests(unittest.TestCase):
    def test_admin_key_validation_requires_exact_configured_secret(self):
        from app import security

        with patch.object(security, "ADMIN_KEY", "configured-secret"):
            self.assertTrue(security.verify_admin_key("configured-secret"))
            self.assertFalse(security.verify_admin_key("wrong-secret"))
            self.assertFalse(security.verify_admin_key(None))

    def test_local_email_execution_ignores_persistent_admin_state(self):
        from app.logic import agents
        from app.logic.memory import admin_auth_context

        token = admin_auth_context.set(None)
        try:
            result = agents._execute_local(
                {"requires_tools": True},
                {"final_prompt": "send an email"},
                "gemma2:2b",
                {},
                [],
            )
        finally:
            admin_auth_context.reset(token)

        self.assertIn("AUTH_REQUIRED", result)

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

            self.assertFalse(queue.cancel("job-123", "other@example.com"))
            self.assertTrue(queue.cancel("job-123", "user@example.com"))
            self.assertTrue(abort_event.is_set())
            self.assertTrue(future.done())
            self.assertEqual(future.result(), "Operation cancelled.")

        asyncio.run(run_test())

    def test_chat_job_ids_are_uuid_values(self):
        import uuid
        from app.routes.chat import _new_job_id

        job_id = _new_job_id()

        self.assertEqual(str(uuid.UUID(job_id)), job_id)
        self.assertNotIn("@", job_id)

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

    def test_attach_two_recent_images_uses_multi_attachment_payload(self):
        import base64
        import json
        from app.logic import agents

        png_a = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"a" * 80).decode("utf-8")
        png_b = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"b" * 80).decode("utf-8")
        history = [
            {
                "role": "assistant",
                "content": (
                    'EMAIL_DRAFT_PAYLOAD:{"recipient":"aniruddha@example.com","subject":"Draft",'
                    '"body":"Please see attached.","tone":"modern","attachment_content":null,'
                    '"attachment_filename":"attachment.png"}'
                ),
            },
            {"role": "user", "content": "uploaded two images", "img": [png_a, png_b]},
        ]
        intent = {"is_local": True, "requires_tools": True, "complexity": "single", "is_sensitive": False}

        with patch("app.logic.tools.resolve_chat_images", return_value=[(png_a, "one.png"), (png_b, "two.png")]):
            with patch.object(agents.requests, "post", side_effect=TimeoutError("skip local drafting")):
                result = agents._try_direct_tool_execution(
                    "now attach this two images in the email",
                    intent,
                    history=history,
                    target_model="gemma4:e2b",
                )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "aniruddha@example.com")
        self.assertEqual(payload["attachment_content"], png_a)
        self.assertEqual(payload["attachment_filename"], "one.png")
        self.assertEqual([att["filename"] for att in payload["attachments"]], ["one.png", "two.png"])
        self.assertEqual([att["content"] for att in payload["attachments"]], [png_a, png_b])

    def test_simulated_email_logs_multiple_attachment_names(self):
        import base64
        from app.logic import tools

        png_a = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"a" * 80).decode("utf-8")
        png_b = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"b" * 80).decode("utf-8")
        attachments = [
            {"content": png_a, "filename": "one.png"},
            {"content": png_b, "filename": "two.png"},
        ]

        opened = mock_open()
        with patch.dict(os.environ, {"EMAIL_MODE": "SIMULATE"}, clear=False):
            with patch.object(tools, "load_dotenv", return_value=False):
                with patch("builtins.open", opened):
                    result = tools.send_or_simulate_email(
                        recipient="aniruddha@example.com",
                        subject="Images",
                        body="Please see attached.",
                        attachments=attachments,
                    )

        self.assertIn("SIMULATE SUCCESS", result)
        written = "".join(call.args[0] for call in opened().write.call_args_list)
        self.assertIn("one.png", written)
        self.assertIn("two.png", written)

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

    def test_attachment_choice_followup_both_builds_email_draft_with_all_images_and_text(self):
        from app.logic import agents
        import json

        img_a = {"id": "file-a", "name": "image_proxy.png", "type": "image/png", "size": 266050}
        img_b = {"id": "file-b", "name": "upscaled.jpg", "type": "image/jpeg", "size": 796430}
        history = [
            {
                "r": "u",
                "c": (
                    '[Attached Context 1]\n"""\nTO\nSUBJECT\nEMAIL TONE\nBODY\nLIVE HTML PREVIEW\n"""\n\n'
                    "attach all this thing together and then we will send a mail to some one"
                ),
                "i": [img_a, img_b],
                "attachments": [img_a, img_b],
            },
            {
                "r": "b",
                "c": (
                    "I found both an image and text in the recent chat. "
                    "Should I use the image only, the text only, both, or a summary of the relevant text with the image attached?"
                ),
            },
        ]

        reconstructed = agents._reconstruct_contextual_prompt("both", history)
        self.assertIn("attach all this thing together", reconstructed)
        self.assertIn("using both", reconstructed)

        result = agents._try_direct_tool_execution(
            reconstructed,
            {"requires_tools": False, "is_local": False, "complexity": "direct"},
            history=history,
            target_model="gemma4-openrouter",
        )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertIsNone(payload["attachment_content"])
        self.assertEqual([att["id"] for att in payload["attachments"]], ["file-a", "file-b"])
        self.assertEqual(payload["attachments"][0]["name"], "image_proxy.png")
        self.assertIn("TO", payload["body"])
        self.assertNotIn("I found both an image and text", payload["body"])

    def test_attachment_choice_reply_accepts_natural_summary_phrases(self):
        from app.logic import agents

        self.assertEqual(
            agents._attachment_choice_reply("a summary of the relevant text with the image attached"),
            "summary",
        )
        self.assertEqual(
            agents._attachment_choice_reply("summary of relevant text with image attached"),
            "summary",
        )
        self.assertEqual(
            agents._attachment_choice_reply("use a summary and attach the image"),
            "summary",
        )
        self.assertEqual(
            agents._attachment_choice_reply("summarize the text and attach the image"),
            "summary",
        )
        self.assertEqual(agents._attachment_choice_reply("image only"), "image")
        self.assertEqual(agents._attachment_choice_reply("text only"), "text")
        self.assertEqual(agents._attachment_choice_reply("both"), "both")

    def test_attachment_choice_followup_summary_builds_email_draft_with_existing_images(self):
        from app.logic import agents
        import json

        img_a = {"id": "file-a", "name": "image_proxy.png", "type": "image/png", "size": 266050}
        img_b = {"id": "file-b", "name": "upscaled.jpg", "type": "image/jpeg", "size": 796430}
        history = [
            {
                "r": "u",
                "c": (
                    '[Attached Context 1]\n"""\n'
                    "Detailed technical notes about Vector Database and FAISS Indexing.\n"
                    "This should become summarized email body text.\n"
                    '"""\n\nattach them together we will send an email to some one'
                ),
                "i": [img_a, img_b],
                "attachments": [img_a, img_b],
            },
            {
                "r": "b",
                "c": (
                    "I found both an image and text in the recent chat. "
                    "Should I use the image only, the text only, both, or a summary of the relevant text with the image attached?"
                ),
            },
        ]

        reconstructed = agents._reconstruct_contextual_prompt(
            "a summary of the relevant text with the image attached",
            history,
        )
        self.assertIn("attach them together", reconstructed)
        self.assertIn("summary of the recent text", reconstructed)

        result = agents._try_direct_tool_execution(
            reconstructed,
            {"requires_tools": False, "is_local": False, "complexity": "direct"},
            history=history,
            target_model="gemma4-openrouter",
        )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        self.assertNotIn("image_generate_tool", result)
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["subject"], "Requested Image and Description")
        self.assertIn("Summary of relevant previous text", payload["body"])
        self.assertNotIn("EMAIL_DRAFT_CONTEXT", payload["body"])
        self.assertEqual([att["id"] for att in payload["attachments"]], ["file-a", "file-b"])

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

    def test_direct_pasted_code_explanation_suppresses_inline_raw_tool_call_leak(self):
        from app.logic import agents

        prompt = (
            "import os\n"
            "def run_indexing():\n"
            "    print('hello world')\n"
            "\n"
            "explain the above in details and syntax by syntax."
        )
        intent = {"requires_tools": False, "complexity": "direct", "is_local": False}
        raw = (
            "Okay, I will do that.\n"
            "[image_generate_tool(description='a new image that should not be generated')]"
        )

        result = agents._harden_result(raw, None, target_model="gemma4-openrouter", intent=intent, user_prompt=prompt)

        self.assertNotIn("image_generate_tool", result)
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

    def test_attached_text_to_email_template_returns_widget_without_cloud_json(self):
        from app.logic import agents
        import json

        attached_text = "Detailed Explanation of the Python Code\n\nThis code demonstrates FAISS indexing."
        prompt = f'[Attached Context 1]\n"""\n{attached_text}\n"""\n\nattach this to email template'
        history = [{"role": "user", "content": prompt}]
        intent = {"is_local": False, "requires_tools": True, "complexity": "single", "is_sensitive": False}

        with patch.object(agents, "_execute_cloud", side_effect=AssertionError("cloud should not run")):
            result = agents.run_helper_agent(
                prompt,
                target_model="gemma4-openrouter",
                history=history,
                user_id="user@example.com",
                intent=intent,
            )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "")
        self.assertEqual(payload["body"], attached_text)
        self.assertNotIn("send_email_tool", result)

    def test_attachment_choice_reply_treats_natural_summary_reply_as_summary(self):
        from app.logic.agents import _attachment_choice_reply

        self.assertEqual(
            _attachment_choice_reply("a summary of the relevant text with the image attached"),
            "summary",
        )

    def test_summary_reply_uses_previous_email_draft_body_not_clarification(self):
        from app.logic import agents
        import json

        draft_body = (
            "Detailed Explanation of the Python Code: Vector Database and FAISS Indexing\n\n"
            "This code demonstrates a vector database implementation using FAISS for multilingual indexing."
        )
        draft_context = {
            "recipient": "",
            "subject": "Requested Image and Description",
            "body": draft_body,
            "tone": "modern",
            "attachments": [
                {"id": "file-a", "name": "image_proxy.png", "type": "image/png", "size": 266050},
                {"id": "file-b", "name": "upscaled.jpg", "type": "image/jpeg", "size": 796430},
            ],
        }
        history = [
            {
                "role": "user",
                "content": (
                    '[Attached Context 1]\n"""\n'
                    f'EMAIL_DRAFT_CONTEXT:{json.dumps(draft_context)}\n'
                    '"""\n\nattach them together then we will send a mail to some one'
                ),
                "attachments": draft_context["attachments"],
                "i": draft_context["attachments"],
            }
        ]

        result = agents._try_update_email_draft_from_prompt(
            "a summary of the relevant text with the image attached",
            history,
            "gemma4-openrouter",
        )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertIn("Summary of relevant previous text", payload["body"])
        self.assertIn("Detailed Explanation of the Python Code", payload["body"])
        self.assertNotIn("a summary of the relevant text with the image attached", payload["body"])
        self.assertNotIn("EMAIL_DRAFT_CONTEXT", payload["body"])
        self.assertNotIn('"recipient"', payload["body"])
        self.assertEqual([att["id"] for att in payload["attachments"]], ["file-a", "file-b"])

        intent = {"is_local": False, "requires_tools": True, "complexity": "single", "is_sensitive": False}
        direct_result = agents._try_direct_tool_execution(
            "summarize the text and attach the image",
            intent,
            history,
            target_model="gemma4-openrouter",
        )
        self.assertTrue(direct_result.startswith("EMAIL_DRAFT_PAYLOAD:"))

    def test_dragged_email_widget_context_seeds_next_email_widget(self):
        from app.logic import agents
        import json

        draft_body = (
            "Detailed Explanation of the Python Code: Vector Database and FAISS Indexing\n\n"
            "def generate_product_catalog():\n"
            "    catalog.append({\"id\": i, \"description\": desc, \"category\": cat})\n"
            "    return catalog\n"
        )
        draft_context = {
            "recipient": "",
            "subject": "Vector Database and FAISS Indexing",
            "body": draft_body,
            "tone": "formal",
        }
        img_a = {"id": "file-a", "name": "image_proxy.png", "type": "image/png", "size": 266050}
        img_b = {"id": "file-b", "name": "upscaled.jpg", "type": "image/jpeg", "size": 796430}
        prompt = (
            '[Attached Context 1]\n"""\n'
            f'EMAIL_DRAFT_CONTEXT:{json.dumps(draft_context)}\n'
            '"""\n\nattach them together then we will send a mail to someone'
        )
        history = [{"role": "user", "content": prompt, "attachments": [img_a, img_b], "i": [img_a, img_b]}]
        intent = {"is_local": False, "requires_tools": True, "complexity": "single", "is_sensitive": False}

        with patch.object(agents, "_execute_cloud", side_effect=AssertionError("cloud should not run")):
            result = agents.run_helper_agent(
                prompt,
                target_model="gemma4-openrouter",
                history=history,
                user_id="user@example.com",
                intent=intent,
            )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "")
        self.assertEqual(payload["subject"], "Vector Database and FAISS Indexing")
        self.assertEqual(payload["body"], draft_body)
        self.assertEqual(payload["tone"], "formal")
        self.assertEqual([att["id"] for att in payload["attachments"]], ["file-a", "file-b"])

        context = agents._latest_attachable_history_context(history, prompt)
        self.assertEqual(context["email_draft"]["body"], draft_body)
        self.assertIn("catalog.append", context["text"])
        self.assertNotIn("EMAIL_DRAFT_CONTEXT", context["text"])
        self.assertNotIn("\\n", context["text"])

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
        from app import factory

        test_app = FastAPI()
        test_app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:9000"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        added = factory.append_cors_origin(test_app, "https://example.ngrok-free.dev/")

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

    def test_refresh_clears_pending_prompt_composer_context(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function clearPendingComposerDrafts()", app_js)
        self.assertIn("key.startsWith('helper_pending_prompt_')", app_js)
        self.assertIn("state.attachedContexts = []", app_js)
        self.assertIn("state.currentImages = []", app_js)
        self.assertIn("clearPendingComposerDrafts();", app_js)

    def test_prompt_context_drag_drop_has_model_safe_limits(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const MAX_ATTACHED_CONTEXTS = 6", app_js)
        self.assertIn("const MAX_CONTEXT_CHARS = 6000", app_js)
        self.assertIn("const MAX_TOTAL_CONTEXT_CHARS = 18000", app_js)
        self.assertIn("function addAttachedContext(text", app_js)
        self.assertIn("addAttachedContext(textVal)", app_js)
        self.assertIn("Context truncated to keep this request within the model limit", app_js)

    def test_context_assembly_skips_current_prompt_in_history(self):
        from app.logic import agents

        prompt = (
            '[Attached Context 1]\n"""\nFirst large context\n"""\n\n'
            '[Attached Context 2]\n"""\nSecond large context\n"""\n\n'
            'compare these contexts'
        )
        intent = {"requires_tools": False, "is_local": False}
        history = [
            {"role": "user", "content": "Earlier message"},
            {"role": "user", "content": prompt},
        ]

        with patch.object(agents, "query_memory", return_value=[]):
            context = agents._assemble_context(
                prompt,
                img_data=None,
                history=history,
                intent=intent,
                user_id="user@example.com",
            )

        self.assertIn("Earlier message", context["history_context"])
        self.assertNotIn("First large context", context["history_context"])
        self.assertNotIn("Second large context", context["history_context"])
        self.assertEqual(context["final_prompt"], prompt)

    def test_retrieve_context_short_circuits_email_draft_markers(self):
        from app.routes import chat

        payload = (
            'EMAIL_DRAFT_CONTEXT:{"recipient":"friend@example.com","subject":"Hello",'
            '"body":"Body with { braces } and \\n line breaks","tone":"formal"}'
        )

        with patch.object(chat, "query_memory", side_effect=AssertionError("query_memory should not run")):
            with patch.object(chat, "explain_neural_context", side_effect=AssertionError("explain_neural_context should not run")):
                result = chat.retrieve_context(chat.RetrieveRequest(text=payload, n=3), current_user="user@example.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["kind"], "email_draft")
        self.assertEqual(result["draft"]["recipient"], "friend@example.com")
        self.assertEqual(result["draft"]["subject"], "Hello")
        self.assertEqual(result["draft"]["tone"], "formal")
        self.assertIn("Body with { braces }", result["draft"]["body"])

    def test_upscale_status_returns_registry_ready(self):
        from app.routes import health

        with patch.object(
            health.UpscaleManager,
            "get_status",
            return_value={"status": "ready", "url": "/static/uploads/upscaled_registry.jpg"},
        ):
            result = asyncio.run(health.get_upscale_status("registry"))

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["url"], "/static/uploads/upscaled_registry.jpg")

    def test_upscale_status_recovers_ready_from_disk(self):
        from app.factory import BASE_DIR
        from app.routes import health

        job_id = "test_status_disk"
        upload_dir = os.path.join(BASE_DIR, "static", "uploads")
        file_path = os.path.join(upload_dir, f"upscaled_{job_id}.jpg")
        os.makedirs(upload_dir, exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(b"fake image bytes")

        try:
            with patch.object(health.UpscaleManager, "get_status", return_value=None):
                result = asyncio.run(health.get_upscale_status(job_id))
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["url"], f"/static/uploads/upscaled_{job_id}.jpg")

    def test_upscale_status_reports_missing_when_registry_and_disk_miss(self):
        from app.factory import BASE_DIR
        from app.routes import health

        job_id = "test_status_missing"
        file_path = os.path.join(BASE_DIR, "static", "uploads", f"upscaled_{job_id}.jpg")
        if os.path.exists(file_path):
            os.remove(file_path)

        with patch.object(health.UpscaleManager, "get_status", return_value=None):
            result = asyncio.run(health.get_upscale_status(job_id))

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

    def test_safe_fetch_blocks_localhost_and_private_ips(self):
        from app.logic.safe_fetch import SafeFetchError, safe_fetch_url

        with self.assertRaises(SafeFetchError):
            safe_fetch_url("http://localhost/image.png")

        with self.assertRaises(SafeFetchError):
            safe_fetch_url("http://10.0.0.8/image.png")

    def test_safe_fetch_blocks_redirect_to_private_ip(self):
        from app.logic.safe_fetch import SafeFetchError, safe_fetch_url

        class RedirectResponse:
            status_code = 302
            headers = {"Location": "http://127.0.0.1/private.png"}
            content = b""

            def iter_content(self, chunk_size=65536):
                return iter(())

        with patch("app.logic.safe_fetch.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            with self.assertRaises(SafeFetchError):
                safe_fetch_url("https://example.com/image.png", request_get=lambda *args, **kwargs: RedirectResponse())

    def test_safe_fetch_blocks_oversized_response(self):
        from app.logic.safe_fetch import SafeFetchError, safe_fetch_url

        class LargeResponse:
            status_code = 200
            headers = {"content-type": "image/png", "content-length": str(20 * 1024 * 1024)}
            content = b""

            def iter_content(self, chunk_size=65536):
                return iter(())

        with patch("app.logic.safe_fetch.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            with self.assertRaises(SafeFetchError):
                safe_fetch_url("https://example.com/image.png", max_bytes=1024, request_get=lambda *args, **kwargs: LargeResponse())

    def test_download_image_attachment_allows_mocked_https_image(self):
        from app.logic.tools import _download_image_attachment
        import base64

        image_bytes = self._png_bytes()
        with patch("app.logic.safe_fetch.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            with patch("app.logic.tools.requests.get", return_value=self._image_response(image_bytes)):
                image_b64, ext, error = _download_image_attachment("https://example.com/image.png")

        self.assertIsNone(error)
        self.assertEqual(ext, "png")
        self.assertEqual(base64.b64decode(image_b64), image_bytes)

    def test_email_html_escapes_script_body_content(self):
        from app.logic.tools import _build_html_body

        html = _build_html_body("Hello\n\n<script>alert(1)</script>", "modern")

        self.assertNotIn("<script>", html.lower())
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_email_html_sanitizes_raw_img_event_handler(self):
        from app.logic.tools import _build_html_body

        html = _build_html_body("Look <img src=x onerror=alert(1)>", "modern")

        self.assertNotIn("<img src=x", html.lower())
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_email_html_preserves_code_blocks_as_escaped_code(self):
        from app.logic.tools import _build_html_body

        html = _build_html_body("```html\n<script>alert(1)</script>\n```", "modern")

        self.assertIn("<pre", html)
        self.assertNotIn("<script>", html.lower())
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_email_html_preserves_safe_links_and_blocks_javascript_links(self):
        from app.logic.tools import _build_html_body

        html = _build_html_body(
            "Read [docs](https://example.com/path?a=1&b=2) and [bad](javascript:alert(1)).",
            "modern",
        )

        self.assertIn('<a href="https://example.com/path?a=1&amp;b=2"', html)
        self.assertIn(">docs</a>", html)
        self.assertNotIn('href="javascript:', html.lower())
        self.assertNotIn("javascript:alert", html.lower())
        self.assertIn("bad", html)

    def test_send_or_simulate_email_offloads_long_plain_body_to_txt_attachment(self):
        from app.logic.tools import send_or_simulate_email

        long_body = (
            "This is a detailed project update with a long explanation.\n\n"
            + ("More plain-text detail is provided here. " * 30)
        ).strip()
        mocked_open = mock_open()

        with patch.dict(os.environ, {"EMAIL_MODE": "SIMULATE"}, clear=False):
            with patch("builtins.open", mocked_open):
                result = send_or_simulate_email(
                    recipient="friend@example.com",
                    subject="Update",
                    body=long_body,
                    tone="modern",
                )

        self.assertIn("SIMULATE SUCCESS", result)
        written = "".join(call.args[0] for call in mocked_open().write.call_args_list)
        self.assertIn("Please find the detailed content attached.", written)
        self.assertIn("Attachment: email-body.txt", written)

    def test_send_or_simulate_email_offloads_long_technical_body_to_md_attachment(self):
        import tempfile
        from app.logic import attachment_store, tools

        class FakeSMTP:
            sent_messages = []

            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self):
                pass

            def login(self, *_args):
                pass

            def send_message(self, msg):
                self.sent_messages.append(msg)

        technical_body = (
            "## Vector Database Notes\n\n"
            "```python\n"
            "from faiss import IndexFlatL2\n"
            "def build_index(vectors):\n"
            "    return IndexFlatL2(768)\n"
            "```\n\n"
            + ("- step one\n- step two\n" * 35)
        ).strip()

        FakeSMTP.sent_messages = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes(
                    "image_proxy.png",
                    "image/png",
                    self._png_bytes(),
                    "owner@example.com",
                )
                with patch.dict(
                    os.environ,
                    {
                        "EMAIL_MODE": "LIVE",
                        "SENDER_EMAIL": "sender@example.com",
                        "SENDER_PWD": "secret",
                    },
                    clear=False,
                ):
                    with patch("app.logic.tools.smtplib.SMTP", FakeSMTP):
                        result = tools.send_or_simulate_email(
                            "user@example.com",
                            "Subject",
                            technical_body,
                            attachments=[saved],
                            owner="owner@example.com",
                        )

        self.assertIn("LIVE SUCCESS", result)
        msg = FakeSMTP.sent_messages[0]
        payload = msg.get_payload()
        plain_text = payload[0].get_payload(0).get_payload(decode=True).decode("utf-8")
        self.assertIn("Please find the detailed technical content attached.", plain_text)
        self.assertEqual(payload[1].get_filename(), "email-body.md")
        self.assertEqual(payload[1].get_content_type(), "text/markdown")
        self.assertEqual(payload[2].get_filename(), "image_proxy.png")
        self.assertEqual(payload[2].get_content_type(), "image/png")
        self.assertIn("Vector Database Notes", payload[1].get_payload(decode=True).decode("utf-8"))

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

    def test_harden_result_converts_cloud_send_email_tool_json_plan(self):
        from app.logic.agents import _harden_result
        import json

        raw_nested_input = """
        [
          {
            "send_email_tool": {
              "recipient": "recipient@example.com",
              "subject": "Current Affairs Update",
              "body": "Here is the requested content.",
              "attachment_content": null,
              "tone": "modern"
            }
          }
        ]
        """

        result = _harden_result(raw_nested_input, None, target_model="gemma4-openrouter")

        self.assertIn("EMAIL_DRAFT_PAYLOAD:", result)
        self.assertNotIn("send_email_tool", result)
        payload = json.loads(result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertEqual(payload["recipient"], "recipient@example.com")
        self.assertEqual(payload["subject"], "Current Affairs Update")
        self.assertEqual(payload["body"], "Here is the requested content.")

    def test_harden_result_rewrites_pollinations_turbo_markdown(self):
        from app.logic.agents import _harden_result

        raw = (
            "![generated](https://image.pollinations.ai/prompt/cat?"
            "model=turbo&width=1024&height=1024&nologo=true&seed=7)"
        )

        result = _harden_result(raw, None, target_model="gemma4-openrouter")

        self.assertIn("model=flux", result)
        self.assertNotIn("model=turbo", result)
        self.assertNotIn("nologo=true", result)

    def test_image_proxy_returns_clear_pollinations_402_error(self):
        from app.routes import proxy
        from unittest.mock import Mock

        upstream = Mock()
        upstream.status_code = 402
        upstream.content = b""
        upstream.headers = {}
        upstream.iter_content.return_value = []

        requested_urls = []

        def fake_get(url, **_kwargs):
            requested_urls.append(url)
            return upstream

        async def run_test():
            with patch("requests.get", side_effect=fake_get):
                return await proxy.image_proxy(
                    "https://image.pollinations.ai/prompt/cat?model=turbo&nologo=true&seed=1"
                )

        response = asyncio.run(run_test())

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.body.decode("utf-8"), "Pollinations rejected this model or account/budget.")
        self.assertEqual(len(requested_urls), 1)
        self.assertIn("model=flux", requested_urls[0])
        self.assertNotIn("model=turbo", requested_urls[0])
        self.assertNotIn("nologo=true", requested_urls[0])

    def test_frontend_stops_retries_for_permanent_image_proxy_status(self):
        from pathlib import Path

        utils_js = Path("static/js/utils.js").read_text(encoding="utf-8")

        status_set_idx = utils_js.index("const PERMANENT_IMAGE_ERROR_STATUSES = new Set([401, 402, 403, 404]);")
        probe_idx = utils_js.index("probeImageProxyStatus(safeSource.url).then")
        retry_idx = utils_js.index("img.retryCount = (img.retryCount || 0) + 1;")

        self.assertLess(status_set_idx, probe_idx)
        self.assertLess(probe_idx, retry_idx)
        self.assertIn("PERMANENT_IMAGE_ERROR_STATUSES.has(Number(status))", utils_js)
        self.assertIn("Pollinations rejected this model or account/budget.", utils_js)
        self.assertIn("img.dataset.loaded = 'true';", utils_js)

    def test_image_generate_tool_uses_flux_model_only(self):
        from app.logic import tools

        with patch("app.logic.tools.time.time", return_value=123):
            with patch("app.logic.tools.UpscaleManager.start_upscale", return_value="job-1"):
                result = tools.image_generate_tool.func("a glass city")

        self.assertIn("image.pollinations.ai", result)
        self.assertIn("model=flux", result)
        self.assertNotIn("model=turbo", result)

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

    def test_multiple_attachments_handling(self):
        from app.routes.chat import ChatRequest, Attachment
        from app.logic.tools import _normalize_attachments
        from app.logic import agents
        import json

        # 1. Test ChatRequest parsing of multiple attachments
        req_data = {
            "prompt": "now attach this two images in the email",
            "attachments": [
                {"name": "img1.png", "type": "image/png", "data": "base64img1"},
                {"name": "img2.png", "type": "image/png", "data": "base64img2"}
            ]
        }
        req = ChatRequest(**req_data)
        self.assertEqual(len(req.attachments), 2)
        self.assertEqual(req.attachments[0].name, "img1.png")
        self.assertEqual(req.attachments[1].data, "base64img2")

        # 2. Test backward compatibility: img conversion to attachments
        req_compat = ChatRequest(prompt="test", img="base64img_single")
        attachments = req_compat.attachments
        if not attachments and req_compat.img:
            if isinstance(req_compat.img, str):
                attachments = [Attachment(name="image.png", type="image/png", data=req_compat.img)]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].data, "base64img_single")

        # 3. Test _normalize_attachments combines primary and list attachments
        attachments_list = [
            {"filename": "img1.png", "content": "base64img1"},
            {"filename": "img2.png", "content": "base64img2"}
        ]
        with patch("app.logic.tools._prepare_attachment", side_effect=lambda content, filename, fallback: {"content": content, "filename": filename}):
            normalized = _normalize_attachments("primary_b64", "primary.txt", attachments_list)
            self.assertEqual(len(normalized), 3)
            self.assertEqual(normalized[0]["filename"], "primary.txt")
            self.assertEqual(normalized[1]["filename"], "img1.png")
            self.assertEqual(normalized[2]["filename"], "img2.png")

    def test_attachment_store_saves_and_resolves_user_image(self):
        import base64
        import tempfile
        from app.logic import attachment_store

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes(
                    "one.png",
                    "image/png",
                    self._png_bytes(),
                    "owner@example.com",
                )
                self.assertIn("id", saved)
                self.assertEqual(saved["name"], "one.png")
                self.assertEqual(saved["type"], "image/png")

                metadata = attachment_store.resolve_attachment_metadata(saved["id"], "owner@example.com")
                self.assertEqual(metadata["sha256"], saved["sha256"])

                resolved = attachment_store.resolve_attachment_reference(saved, "owner@example.com")
                self.assertEqual(base64.b64decode(resolved["content"]), self._png_bytes())
                self.assertEqual(resolved["filename"], "one.png")

                with self.assertRaises(attachment_store.AttachmentStoreError):
                    attachment_store.resolve_attachment_metadata(saved["id"], "other@example.com")

    def test_chat_attachment_model_accepts_file_ids_without_data(self):
        from app.routes.chat import ChatRequest

        req = ChatRequest(
            prompt="attach these",
            attachments=[{"id": "abc123", "name": "one.png", "type": "image/png", "size": 123}],
        )

        self.assertEqual(req.attachments[0].id, "abc123")
        self.assertIsNone(req.attachments[0].data)

    def test_direct_tool_preserves_current_attachment_id_metadata(self):
        from app.logic import agents
        from app.logic.exceptions import AgentFastExit
        import json

        payload = (
            "EMAIL_DRAFT_PAYLOAD:"
            + json.dumps({
                "recipient": "user@example.com",
                "subject": "Images",
                "body": "Please find the requested image attached.",
                "tone": "modern",
                "attachment_content": None,
                "attachment_filename": "one.png",
                "attachments": [{"id": "file-1", "filename": "one.png", "type": "image/png", "size": 123}],
            })
        )
        img_data = [
            {"id": "file-1", "name": "one.png", "type": "image/png", "size": 123, "path": "ignored"},
            {"id": "file-2", "name": "two.png", "type": "image/png", "size": 456, "path": "ignored"},
        ]

        with patch.object(agents.tools.send_email_tool, "func", side_effect=AgentFastExit(payload)) as email_tool:
            with patch("app.logic.agents.requests.post", side_effect=RuntimeError("skip local model")):
                result = agents._try_direct_tool_execution(
                    "email user@example.com and attach these two images",
                    {"requires_tools": True, "is_local": True, "force_direct_tool": True},
                    history=[],
                    target_model="gemma4:e2b",
                    img_data=img_data,
                )

        self.assertTrue(result.startswith("EMAIL_DRAFT_PAYLOAD:"))
        sent_attachments = email_tool.call_args.kwargs["attachments"]
        self.assertEqual([att["id"] for att in sent_attachments], ["file-1", "file-2"])
        self.assertEqual([att["filename"] for att in sent_attachments], ["one.png", "two.png"])
        self.assertNotIn("content", sent_attachments[0])

    def test_send_email_tool_keeps_file_id_draft_metadata_only(self):
        import json
        from app.logic import tools
        from app.logic.exceptions import AgentFastExit

        with self.assertRaises(AgentFastExit) as ctx:
            tools.send_email_tool.func(
                recipient="user@example.com",
                subject="Images",
                body="Attached.",
                attachments=[{"id": "file-1", "name": "one.png", "type": "image/png", "size": 123}],
            )

        payload = json.loads(ctx.exception.result.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])
        self.assertIsNone(payload["attachment_content"])
        self.assertEqual(payload["attachments"][0]["id"], "file-1")
        self.assertNotIn("content", payload["attachments"][0])

    def test_send_or_simulate_email_resolves_attachment_id(self):
        import tempfile
        from app.logic import attachment_store, tools

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes(
                    "one.png",
                    "image/png",
                    self._png_bytes(),
                    "owner@example.com",
                )
                cwd = os.getcwd()
                try:
                    os.chdir(tmp)
                    with patch.dict(os.environ, {"EMAIL_MODE": "SIMULATE"}, clear=False):
                        result = tools.send_or_simulate_email(
                            "user@example.com",
                            "Subject",
                            "Body",
                            attachments=[saved],
                            owner="owner@example.com",
                        )
                    with open("simulated_emails.log", "r", encoding="utf-8") as f:
                        written = f.read()
                finally:
                    os.chdir(cwd)

        self.assertIn("SIMULATE SUCCESS", result)
        self.assertIn("one.png", written)
        self.assertIn(str(len(self._png_bytes())), written)

    def test_live_email_uses_mixed_root_and_alternative_body(self):
        import tempfile
        from app.logic import attachment_store, tools

        class FakeSMTP:
            sent_messages = []

            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self):
                pass

            def login(self, *_args):
                pass

            def send_message(self, msg):
                self.sent_messages.append(msg)

        FakeSMTP.sent_messages = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(attachment_store, "ATTACHMENT_ROOT", tmp):
                saved = attachment_store.save_attachment_bytes(
                    "one.png",
                    "image/png",
                    self._png_bytes(),
                    "owner@example.com",
                )
                with patch.dict(
                    os.environ,
                    {
                        "EMAIL_MODE": "LIVE",
                        "SENDER_EMAIL": "sender@example.com",
                        "SENDER_PWD": "secret",
                    },
                    clear=False,
                ):
                    with patch("app.logic.tools.smtplib.SMTP", FakeSMTP):
                        result = tools.send_or_simulate_email(
                            "user@example.com",
                            "Subject",
                            "Body",
                            attachments=[saved],
                            owner="owner@example.com",
                        )

        self.assertIn("LIVE SUCCESS", result)
        msg = FakeSMTP.sent_messages[0]
        self.assertEqual(msg.get_content_subtype(), "mixed")
        payload = msg.get_payload()
        self.assertEqual(payload[0].get_content_subtype(), "alternative")
        self.assertEqual(payload[1].get_filename(), "one.png")
        self.assertEqual(payload[1].get_content_type(), "image/png")

    def test_frontend_markdown_rendering_is_sanitized(self):
        from pathlib import Path

        index_html = Path("templates/index.html").read_text(encoding="utf-8")
        ui_js = Path("static/js/ui.js").read_text(encoding="utf-8")
        utils_js = Path("static/js/utils.js").read_text(encoding="utf-8")

        marked_idx = index_html.index("https://cdn.jsdelivr.net/npm/marked/marked.min.js")
        purify_idx = index_html.index("https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.2.7/purify.min.js")
        utils_idx = index_html.index("/static/js/utils.js")
        self.assertLess(marked_idx, purify_idx)
        self.assertLess(purify_idx, utils_idx)
        self.assertIn('integrity="sha512-78KH17QLT5e55GJqP76vutp1D2iAoy06WcYBXB6iBCsmO6wWzx0Qdg8EDpm8mKXv68BcvHOyeeP4wxAL0twJGQ=="', index_html)
        self.assertIn('crossorigin="anonymous"', index_html)
        self.assertIn('referrerpolicy="no-referrer"', index_html)

        self.assertIn("function sanitizeMarkdownHtml(html)", utils_js)
        self.assertIn("window.DOMPurify.sanitize(dirty, config)", utils_js)
        self.assertIn("const rendered = marked.parse(text, { renderer: renderer });", utils_js)
        self.assertIn("return sanitizeMarkdownHtml(rendered);", utils_js)
        self.assertIn("renderer.html = function()", utils_js)
        self.assertIn("return '';", utils_js)
        self.assertIn("FORBID_TAGS: FORBIDDEN_MARKDOWN_TAGS", utils_js)
        self.assertIn("FORBID_ATTR:", utils_js)
        self.assertIn("'onerror'", utils_js)
        self.assertIn("'onclick'", utils_js)
        self.assertIn("DANGEROUS_URL_PATTERN", utils_js)
        self.assertIn("javascript|data|vbscript|file", utils_js)
        self.assertIn("ALLOWED_MARKDOWN_PROTOCOLS", utils_js)
        self.assertIn("new Set(['http:', 'https:', 'mailto:'])", utils_js)

        self.assertIn("function buildRenderedImageHtml(source, title, altText)", utils_js)
        self.assertIn("document.createElement('img')", utils_js)
        self.assertIn("img.alt = String(altText || 'AI Generated Image')", utils_js)
        self.assertIn("img.title = String(title)", utils_js)
        self.assertIn("img.dataset.retryUrl = safeSource.url", utils_js)
        self.assertIn("return buildRenderedImageHtml(imgHref, title, imgText);", utils_js)
        self.assertIn("addEventListener('click'", utils_js)
        self.assertIn("addEventListener('load'", utils_js)
        self.assertIn("addEventListener('error'", utils_js)
        self.assertNotIn('class="code-btn copy-btn" onclick=', utils_js)
        self.assertNotIn('class="code-btn download-btn" onclick=', utils_js)

        self.assertIn("function normalizePreviewImageSource(value)", ui_js)
        self.assertIn("data-preview-src", ui_js)
        self.assertIn("data-preview-payload", ui_js)
        self.assertNotIn("window.openImageModal('${src}')", ui_js)
        self.assertNotIn("window.handleImageDragStart(event, '${item}')", ui_js)

        render_assignments = ui_js.count("innerHTML = window.renderMarkdown") + ui_js.count("innerHTML = cleanedText ? window.renderMarkdown")
        self.assertGreater(render_assignments, 0)
        self.assertGreaterEqual(ui_js.count("window.hydrateRenderedMarkdown"), render_assignments)
        self.assertIn('sandbox=""', ui_js)
        self.assertNotIn('allow-scripts', ui_js)

    def test_frontend_attachment_pipeline_uses_file_ids_and_hardened_iframe(self):
        from pathlib import Path

        app_js = Path("static/js/app.js").read_text(encoding="utf-8")
        api_js = Path("static/js/api.js").read_text(encoding="utf-8")
        ui_js = Path("static/js/ui.js").read_text(encoding="utf-8")

        self.assertIn("function escapeHTML(value)", app_js)
        self.assertIn("uploadAttachments", api_js)
        self.assertIn("new FormData()", api_js)
        self.assertIn("function parseEmailDraftContext(text)", app_js)
        self.assertIn("function serializeAttachedContext(ctx)", app_js)
        self.assertIn("function stripInternalEmailDraftMarkers(text)", app_js)
        self.assertIn("kind: 'email_draft'", app_js)
        self.assertIn("ctx.kind === 'email_draft'", app_js)
        self.assertIn("buildEmailDraftDragContext", app_js)
        self.assertIn("buildEmailDraftDragContext(message, widgetEl = null)", app_js)
        self.assertIn("EMAIL_DRAFT_CONTEXT:", app_js)
        self.assertIn("application/x-helper-email-draft", app_js)
        self.assertIn("parseEmailDraftContext(dragContext)", app_js)
        self.assertIn('e.dataTransfer.setData("text/plain", `EMAIL_DRAFT_CONTEXT:${JSON.stringify(emailDraft)}`)', app_js)
        self.assertIn("window.getVisibleUserMessageContent = getVisibleUserMessageContent", app_js)
        self.assertIn("card.__emailDraft = draft", ui_js)
        self.assertIn("function collectEmailDraftForDrag(card)", ui_js)
        self.assertIn("window.getVisibleUserMessageContent", ui_js)
        self.assertIn("collectEmailDraftForDrag", ui_js)
        self.assertIn("await waitForPendingImageUploads()", app_js)
        self.assertIn("historyForApi", app_js)
        self.assertIn("img: null", app_js)
        self.assertIn("await requestChatPersist({ immediate: true })", app_js)
        self.assertIn("await send();", app_js)
        self.assertIn('iframe.srcdoc = safeHtml', ui_js)
        self.assertIn('sandbox=""', ui_js)
        self.assertNotIn('sandbox="allow-same-origin"', ui_js)

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
