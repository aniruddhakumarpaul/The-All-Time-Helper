import json
import threading
import time
import unittest
from unittest.mock import patch

from app.contracts.email_draft import draft_marker, normalize_email_draft
from app.logic.workflow_orchestrator import (
    PendingWorkflowStore,
    WorkflowActionState,
    WorkflowActionType,
    WorkflowExecutor,
    WorkflowIntent,
    WorkflowPlanner,
    classify_workflow_intent,
    normalize_image_tool_result,
    resolve_workflow_context,
)
from app.services.email_delivery_service import (
    EmailAuthorizationError,
    EmailDeliveryResult,
)


OWNER = "owner@example.com"


def payload_from_message(message):
    return json.loads(message.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])


def modi_draft(attachments=None):
    return normalize_email_draft({
        "recipient": OWNER,
        "subject": "About Prime Minister Narendra Modi",
        "body": "A concise note about Prime Minister Narendra Modi.",
        "tone": "formal",
        "attachments": attachments or [],
    })


def history_with_draft(draft=None):
    return [
        {"role": "user", "content": "write a mail to me about pm modi"},
        {"role": "assistant", "content": draft_marker(draft or modi_draft())},
    ]


class FakeDeliveryService:
    def __init__(self):
        self.calls = []

    def send_approved_email(self, *, draft, owner, admin_key, request_id):
        if admin_key != "valid-key":
            raise EmailAuthorizationError("invalid")
        self.calls.append({"draft": draft, "owner": owner, "request_id": request_id})
        return EmailDeliveryResult(
            success=True,
            status="SIMULATE SUCCESS",
            request_id=request_id,
            mode="simulated",
        )


class WorkflowPlannerTests(unittest.TestCase):
    def setUp(self):
        self.store = PendingWorkflowStore(ttl_seconds=60)
        self.planner = WorkflowPlanner(pending_store=self.store)

    def test_scenario_a_write_mail_defaults_to_draft_and_resolves_owner(self):
        plan = self.planner.plan("write a mail to me about pm modi", [], OWNER)
        self.assertEqual(plan.intent, WorkflowIntent.DRAFT_EMAIL)
        self.assertFalse(any(action.sensitive for action in plan.actions))
        self.assertEqual(plan.actions[-1].action_type, WorkflowActionType.BUILD_EMAIL_DRAFT)

        result = WorkflowExecutor(pending_store=self.store).execute(plan)
        payload = payload_from_message(result.message)
        self.assertEqual(payload["recipient"], OWNER)
        self.assertIn("Narendra Modi", payload["subject"])
        self.assertNotIn("AUTH_REQUIRED", result.message)

    def test_intent_classifier_separates_draft_delivery_media_and_approval(self):
        self.assertEqual(classify_workflow_intent("write a mail to me"), WorkflowIntent.DRAFT_EMAIL)
        self.assertEqual(classify_workflow_intent("send this email now", has_active_draft=True), WorkflowIntent.DELIVER_EMAIL)
        self.assertEqual(classify_workflow_intent("attach a reference image", has_active_draft=True), WorkflowIntent.ATTACH_TO_DRAFT)
        self.assertEqual(classify_workflow_intent("find a reference image"), WorkflowIntent.SEARCH_IMAGE)
        self.assertEqual(classify_workflow_intent("generate a symbolic image", has_active_draft=True), WorkflowIntent.GENERATE_IMAGE)
        self.assertEqual(classify_workflow_intent("latest facts about PM Modi"), WorkflowIntent.SEARCH_WEB)
        self.assertEqual(classify_workflow_intent("change the subject", has_active_draft=True), WorkflowIntent.UPDATE_EMAIL_DRAFT)
        self.assertEqual(classify_workflow_intent("ignored", is_masked=True), WorkflowIntent.REQUEST_EMAIL_APPROVAL)
        self.assertEqual(classify_workflow_intent("hello"), WorkflowIntent.GENERAL_RESPONSE)

    def test_create_email_with_reference_image_searches_instead_of_generating(self):
        searched = []
        generated = []
        plan = self.planner.plan(
            "Create a factual email about PM Modi and attach a reference image.",
            [],
            OWNER,
        )
        action_types = [action.action_type for action in plan.actions]
        self.assertIn(WorkflowActionType.IMAGE_SEARCH, action_types)
        self.assertNotIn(WorkflowActionType.IMAGE_GENERATE, action_types)
        result = WorkflowExecutor(
            pending_store=self.store,
            image_search=lambda query: searched.append(query) or "![Official](https://images.example/modi.jpg)",
            image_generate=lambda prompt: generated.append(prompt) or "![Generated](https://images.example/generated.png)",
        ).execute(plan)
        payload = payload_from_message(result.message)
        self.assertEqual(len(searched), 1)
        self.assertEqual(generated, [])
        self.assertEqual(payload["attachments"][0]["content"], "https://images.example/modi.jpg")

    def test_scenario_b_reference_typo_uses_search_and_preserves_draft(self):
        searched = []
        generated = []
        executor = WorkflowExecutor(
            pending_store=self.store,
            image_search=lambda query: searched.append(query) or "![Official](https://images.example/modi.jpg)",
            image_generate=lambda prompt: generated.append(prompt) or "![Generated](https://images.example/generated.png)",
        )
        plan = self.planner.plan(
            "how about attaching a image just for refernce",
            history_with_draft(),
            OWNER,
        )
        self.assertEqual(plan.topic, "Prime Minister Narendra Modi")
        result = executor.execute(plan)
        payload = payload_from_message(result.message)

        self.assertEqual(len(searched), 1)
        self.assertIn("Narendra Modi", searched[0])
        self.assertEqual(generated, [])
        self.assertEqual(payload["recipient"], OWNER)
        self.assertEqual(payload["subject"], modi_draft().subject)
        self.assertEqual(payload["body"], modi_draft().body)
        self.assertEqual(payload["tone"], "formal")
        self.assertEqual(payload["attachments"][0]["content"], "https://images.example/modi.jpg")

    def test_scenario_c_generation_finishes_before_attachment(self):
        calls = []

        def generate(description):
            calls.append("generate")
            return "![Digital India](https://images.example/digital-india.png)"

        plan = self.planner.plan(
            "generate a symbolic digital india image and attach it to this draft",
            history_with_draft(),
            OWNER,
        )
        executor = WorkflowExecutor(pending_store=self.store, image_generate=generate)
        result = executor.execute(plan)
        payload = payload_from_message(result.message)
        calls.append("attached" if payload["attachments"] else "missing")

        self.assertEqual(calls, ["generate", "attached"])
        self.assertEqual(payload["attachments"][0]["content"], "https://images.example/digital-india.png")

    def test_send_without_active_draft_asks_for_draft_without_authorization(self):
        plan = self.planner.plan("send this email to me now", [], OWNER)
        self.assertEqual(plan.intent, WorkflowIntent.GENERAL_RESPONSE)
        self.assertFalse(any(action.sensitive for action in plan.actions))
        result = WorkflowExecutor(pending_store=self.store).execute(plan)
        self.assertIn("Which email draft", result.message)
        self.assertNotIn("AUTH_REQUIRED", result.message)

    def test_reference_image_with_blank_active_draft_asks_for_topic(self):
        blank = normalize_email_draft({"recipient": OWNER, "subject": "", "body": ""})
        searched = []
        plan = self.planner.plan("attach a reference image", [{"role": "assistant", "content": draft_marker(blank)}], OWNER)
        result = WorkflowExecutor(
            pending_store=self.store,
            image_search=lambda query: searched.append(query),
        ).execute(plan)
        self.assertIn("What topic or person", result.message)
        self.assertEqual(searched, [])

    def test_scenario_h_without_active_draft_asks_one_focused_question(self):
        searched = []
        plan = self.planner.plan("attach a reference image", [], OWNER)
        result = WorkflowExecutor(
            pending_store=self.store,
            image_search=lambda query: searched.append(query),
        ).execute(plan)

        self.assertIn("Which email draft", result.message)
        self.assertEqual(searched, [])

    def test_latest_structured_draft_wins(self):
        old = modi_draft()
        latest = normalize_email_draft({"recipient": "latest@example.com", "subject": "Latest", "body": "Latest body"})
        context = resolve_workflow_context("attach a reference image", history_with_draft(old) + [
            {"role": "assistant", "content": draft_marker(latest)}
        ])
        self.assertEqual(context.active_draft.subject, "Latest")

    def test_malformed_and_unsupported_latest_drafts_fail_controlled(self):
        malformed = resolve_workflow_context(
            "attach a reference image",
            [{"role": "assistant", "content": "EMAIL_DRAFT_PAYLOAD:{broken"}],
        )
        unsupported = resolve_workflow_context(
            "attach a reference image",
            [{"role": "assistant", "content": 'EMAIL_DRAFT_PAYLOAD:{"schema_version":99}'}],
        )
        self.assertEqual(malformed.error_code, "invalid_email_draft")
        self.assertEqual(unsupported.error_code, "unsupported_email_draft_version")

    def test_image_normalizer_accepts_markdown_url_and_rejects_unsafe_scheme(self):
        result = normalize_image_tool_result(
            "![Reference](https://images.example/photo.webp)", source="search", query="person",
        )
        self.assertEqual(result.url, "https://images.example/photo.webp")
        self.assertEqual(result.mime_type, "image/webp")
        self.assertIsNone(normalize_image_tool_result("javascript:alert(1)", source="search"))


class WorkflowExecutorTests(unittest.TestCase):
    def setUp(self):
        self.store = PendingWorkflowStore(ttl_seconds=60)
        self.planner = WorkflowPlanner(pending_store=self.store)

    def test_scenario_d_independent_searches_overlap_and_update_waits(self):
        barrier = threading.Barrier(2)
        completed = set()
        lock = threading.Lock()

        def web_search(_query):
            barrier.wait(timeout=2)
            with lock:
                completed.add("web")
            return "Title: Current update\nSnippet: A verified current point.\nURL: https://source.example"

        def image_search(_query):
            barrier.wait(timeout=2)
            with lock:
                completed.add("image")
            return "![Reference](https://images.example/modi.jpg)"

        plan = self.planner.plan(
            "add current factual points about PM Modi and attach a reference image",
            history_with_draft(),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            web_search=web_search,
            image_search=image_search,
        ).execute(plan)
        payload = payload_from_message(result.message)

        self.assertEqual(completed, {"web", "image"})
        self.assertEqual(result.actions["web_search"].state, WorkflowActionState.COMPLETED)
        self.assertEqual(result.actions["image"].state, WorkflowActionState.COMPLETED)
        self.assertIn("Current factual reference points", payload["body"])
        self.assertEqual(len(payload["attachments"]), 1)

    def test_partial_parallel_success_updates_body_without_image(self):
        plan = self.planner.plan(
            "add current factual points about PM Modi and attach a reference image",
            history_with_draft(),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            web_search=lambda _q: "Title: Update\nSnippet: Grounded point.",
            image_search=lambda _q: "ERROR: unavailable",
        ).execute(plan)
        payload = payload_from_message(result.message)
        self.assertIn("Grounded point", payload["body"])
        self.assertEqual(payload["attachments"], [])
        self.assertEqual(result.actions["image"].state, WorkflowActionState.FAILED)

    def test_web_search_failure_still_attaches_valid_reference_image(self):
        plan = self.planner.plan(
            "add current factual points about PM Modi and attach a reference image",
            history_with_draft(),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            web_search=lambda _q: "ERROR: unavailable",
            image_search=lambda _q: "![Reference](https://images.example/modi.jpg)",
        ).execute(plan)
        payload = payload_from_message(result.message)
        self.assertEqual(result.actions["web_search"].state, WorkflowActionState.FAILED)
        self.assertEqual(result.actions["image"].state, WorkflowActionState.COMPLETED)
        self.assertEqual(payload["attachments"][0]["content"], "https://images.example/modi.jpg")

    def test_image_generation_failure_blocks_attachment_and_emits_no_marker(self):
        plan = self.planner.plan(
            "generate a symbolic digital india image and attach it to this draft",
            history_with_draft(),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            image_generate=lambda _q: "ERROR: unavailable",
        ).execute(plan)
        self.assertEqual(result.actions["image"].state, WorkflowActionState.FAILED)
        self.assertEqual(result.actions["image"].error_category, "image_generation_unavailable")
        self.assertEqual(result.actions["attach_image"].state, WorkflowActionState.BLOCKED)
        self.assertNotIn("EMAIL_DRAFT_PAYLOAD:", result.message)
        self.assertIn("draft was not changed", result.message)

    def test_invalid_image_generation_output_blocks_attachment(self):
        plan = self.planner.plan(
            "generate a symbolic digital india image and attach it to this draft",
            history_with_draft(),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            image_generate=lambda _q: "Done",
        ).execute(plan)
        self.assertEqual(result.actions["image"].state, WorkflowActionState.FAILED)
        self.assertEqual(result.actions["image"].error_category, "invalid_image_result")
        self.assertEqual(result.actions["attach_image"].state, WorkflowActionState.BLOCKED)
        self.assertNotIn("EMAIL_DRAFT_PAYLOAD:", result.message)

    def test_generated_image_description_uses_draft_context_and_attaches_once(self):
        draft = normalize_email_draft({
            "recipient": OWNER,
            "subject": "Regarding PM Modi",
            "body": "Leadership, infrastructure, digital innovation, and national development are the core themes.",
            "attachment_description": "A respectful public-service illustration.",
            "attachment_content": None,
            "attachment_filename": "attachment.bin",
            "attachment_type": "application/octet-stream",
            "attachments": [],
        })
        prompts = []
        plan = self.planner.plan(
            "generate an image based on the topics content and attach it to the email widget",
            history_with_draft(draft),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            image_generate=lambda description: prompts.append(description) or "![Generated](https://image.pollinations.ai/prompt/pm-modi?model=flux&width=1024&height=1024&seed=1&uid=job-1)",
        ).execute(plan)
        payload = payload_from_message(result.message)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Prime Minister Narendra Modi", prompts[0])
        for term in ("leadership", "infrastructure", "digital innovation", "national development"):
            self.assertIn(term, prompts[0].lower())
        self.assertNotIn(OWNER, prompts[0])
        self.assertNotIn("Admin Key", prompts[0])
        self.assertEqual(len(payload["attachments"]), 1)
        self.assertEqual(payload["attachments"][0]["source"], "generated")
        self.assertNotEqual(payload["attachments"][0]["filename"], "attachment.bin")
        self.assertEqual(payload["attachment_filename"], payload["attachments"][0]["filename"])
        self.assertEqual(payload["attachment_content"], payload["attachments"][0]["content"])

    def test_real_image_generate_tool_output_is_normalized(self):
        from app.logic import tools

        with patch.object(tools.UpscaleManager, "start_upscale", return_value="job-42") as start:
            raw = tools.image_generate_tool.func("A respectful PM Modi infrastructure illustration")
        start.assert_called_once()
        result = normalize_image_tool_result(raw, source="generated", query="A respectful PM Modi infrastructure illustration")
        self.assertIsNotNone(result)
        self.assertTrue(result.url.startswith("https://"))
        self.assertEqual(result.mime_type, "image/png")
        self.assertTrue(result.filename.endswith(".png"))
        self.assertNotIn("?", result.filename)
        self.assertIn("model=flux", result.url)
        self.assertIn("width=1024", result.url)
        self.assertIn("height=1024", result.url)
        self.assertIn("uid=job-42", result.url)

    def test_multiple_attachments_are_preserved_and_duplicate_is_suppressed(self):
        existing = [
            {"id": "upload-1", "filename": "notes.pdf", "mime_type": "application/pdf", "source": "upload"},
            {"content": "https://images.example/modi.jpg", "filename": "modi.jpg", "mime_type": "image/jpeg", "source": "remote"},
        ]
        plan = self.planner.plan(
            "attach a reference image",
            history_with_draft(modi_draft(existing)),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            image_search=lambda _q: "![Same](https://images.example/modi.jpg)",
        ).execute(plan)
        payload = payload_from_message(result.message)
        self.assertEqual(len(payload["attachments"]), 2)
        self.assertEqual(payload["attachments"][0]["id"], "upload-1")
    def test_generated_attachment_preserves_real_attachments_and_uses_generated_primary(self):
        existing = [
            {"id": "upload-1", "filename": "notes.pdf", "mime_type": "application/pdf", "source": "upload"},
            {"content": "https://images.example/modi.jpg", "filename": "modi.jpg", "mime_type": "image/jpeg", "source": "remote"},
        ]
        plan = self.planner.plan(
            "generate an image about this draft and attach it to the email widget",
            history_with_draft(modi_draft(existing)),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            image_generate=lambda _q: "![Generated](https://image.pollinations.ai/prompt/modi?model=flux&width=1024&height=1024&seed=2&uid=job-2)",
        ).execute(plan)
        payload = payload_from_message(result.message)
        self.assertEqual(len(payload["attachments"]), 3)
        self.assertEqual(payload["attachments"][0]["id"], "upload-1")
        generated = next(item for item in payload["attachments"] if item["source"] == "generated")
        self.assertEqual(payload["attachment_content"], generated["content"])
        self.assertEqual(payload["attachment_filename"], generated["filename"])

    def test_workflow_cancellation_stops_before_tools(self):
        abort = threading.Event()
        abort.set()
        calls = []
        plan = self.planner.plan(
            "attach a reference image",
            history_with_draft(),
            OWNER,
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            image_search=lambda _q: calls.append("called"),
        ).execute(plan, abort_event=abort)
        self.assertTrue(result.cancelled)
        self.assertEqual(calls, [])

    def test_send_pauses_then_valid_masked_key_resumes_delivery_once(self):
        delivery = FakeDeliveryService()
        executor = WorkflowExecutor(pending_store=self.store, delivery_service=delivery)
        plan = self.planner.plan("send this email to me now", history_with_draft(), OWNER)
        paused = executor.execute(plan)
        self.assertTrue(paused.paused)
        self.assertIn("AUTH_REQUIRED", paused.message)
        self.assertEqual(delivery.calls, [])

        resume = self.planner.plan("ignored-secret", [], OWNER, is_masked=True)
        result = executor.execute(resume, admin_key="valid-key")
        self.assertEqual(result.message, "Email simulated successfully.")
        self.assertEqual(len(delivery.calls), 1)
        self.assertIsNone(self.store.peek(OWNER))

        self.assertIsNone(self.planner.plan("valid-key", [], OWNER, is_masked=True))
        self.assertEqual(len(delivery.calls), 1)

    def test_invalid_key_keeps_pending_workflow_resumable(self):
        delivery = FakeDeliveryService()
        executor = WorkflowExecutor(pending_store=self.store, delivery_service=delivery)
        paused = executor.execute(self.planner.plan("send this email to me now", history_with_draft(), OWNER))
        resume = self.planner.plan("secret", [], OWNER, is_masked=True)
        invalid = executor.execute(resume, admin_key="wrong-secret")

        self.assertTrue(paused.paused)
        self.assertTrue(invalid.paused)
        self.assertIn("Incorrect Admin Key", invalid.message)
        self.assertNotIn("wrong-secret", invalid.message)
        self.assertIsNotNone(self.store.peek(OWNER))
        self.assertEqual(delivery.calls, [])

    def test_pending_workflow_is_owner_scoped_and_ttl_bounded(self):
        plan = self.planner.plan("send this email to me now", history_with_draft(), OWNER)
        WorkflowExecutor(pending_store=self.store, delivery_service=FakeDeliveryService()).execute(plan)
        self.assertIsNone(self.store.peek("other@example.com"))
        stored = self.store.peek(OWNER)
        stored.expires_at = time.time() - 1
        self.store.put(stored)
        self.assertIsNone(self.store.peek(OWNER))


if __name__ == "__main__":
    unittest.main()
