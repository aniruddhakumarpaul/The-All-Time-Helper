import json
import unittest
from unittest.mock import patch

from app.contracts.email_draft import draft_marker, normalize_email_draft
from app.logic import workflow_orchestrator as workflow


OWNER = "owner@example.com"


def payload(message):
    return json.loads(message.split("EMAIL_DRAFT_PAYLOAD:", 1)[1])


class CompoundWorkflowExecutorTests(unittest.TestCase):
    def setUp(self):
        self.store = workflow.PendingWorkflowStore(ttl_seconds=60)
        self.planner = workflow.WorkflowPlanner(pending_store=self.store)

    def test_new_draft_build_and_generation_are_independent_before_attachment(self):
        plan = self.planner.plan(
            "create an email about PM Modi and generate an image and attach it to this email draft",
            [],
            OWNER,
        )
        build = next(action for action in plan.actions if action.action_type == workflow.WorkflowActionType.BUILD_EMAIL_DRAFT)
        image = next(action for action in plan.actions if action.action_type == workflow.WorkflowActionType.IMAGE_GENERATE)
        attach = next(action for action in plan.actions if action.action_type == workflow.WorkflowActionType.ATTACH_IMAGE)
        self.assertEqual(build.depends_on, [])
        self.assertEqual(image.depends_on, [])
        self.assertCountEqual(attach.depends_on, [build.id, image.id])

        result = workflow.WorkflowExecutor(
            pending_store=self.store,
            image_generate=lambda _description: "![Generated](https://image.example/generated.png)",
        ).execute(plan)
        self.assertEqual(result.actions[build.id].state, workflow.WorkflowActionState.COMPLETED)
        self.assertEqual(result.actions[image.id].state, workflow.WorkflowActionState.COMPLETED)
        self.assertEqual(result.actions[attach.id].state, workflow.WorkflowActionState.COMPLETED)
        self.assertEqual(payload(result.message)["attachments"][0]["source"], "generated")

    def test_new_draft_generation_failure_returns_draft_without_marker_loss(self):
        plan = self.planner.plan(
            "create an email about PM Modi and generate an image and attach it to this email draft",
            [],
            OWNER,
        )
        result = workflow.WorkflowExecutor(
            pending_store=self.store,
            image_generate=lambda _description: "ERROR: unavailable",
        ).execute(plan)
        self.assertEqual(result.actions["image"].state, workflow.WorkflowActionState.FAILED)
        self.assertEqual(result.actions["attach_image"].state, workflow.WorkflowActionState.BLOCKED)
        self.assertIn("created without an image", result.message)
        self.assertIn("EMAIL_DRAFT_PAYLOAD:", result.message)
        self.assertEqual(payload(result.message)["attachments"], [])

    def test_existing_draft_attachment_failure_has_no_replacement_marker(self):
        existing = normalize_email_draft({
            "recipient": OWNER,
            "subject": "Existing",
            "body": "Keep this draft unchanged.",
        })
        history = [{"role": "assistant", "content": draft_marker(existing)}]
        plan = self.planner.plan(
            "generate an image about this draft and attach it to the email widget",
            history,
            OWNER,
        )
        with patch.object(workflow, "_attach_image", side_effect=RuntimeError("storage unavailable")):
            result = workflow.WorkflowExecutor(
                pending_store=self.store,
                image_generate=lambda _description: "![Generated](https://image.example/generated.png)",
            ).execute(plan)
        self.assertEqual(result.actions["image"].state, workflow.WorkflowActionState.COMPLETED)
        self.assertEqual(result.actions["attach_image"].state, workflow.WorkflowActionState.FAILED)
        self.assertEqual(result.actions["attach_image"].error_category, "image_attachment_failed")
        self.assertNotIn("EMAIL_DRAFT_PAYLOAD:", result.message)
        self.assertIn("existing email draft was not changed", result.message)


if __name__ == "__main__":
    unittest.main()