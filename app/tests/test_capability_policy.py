import tempfile
import threading
import time
import unittest
import inspect
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from app.contracts.email_draft import normalize_email_draft
from app.logic import attachment_store, tools
from app.logic.capability_policy import (
    ACTIVE_TOOL_CAPABILITY_IDS,
    CAPABILITY_POLICY,
    CAPABILITY_POLICY_VERSION,
    CAPABILITY_REGISTRY,
    INTERNAL_PURE_TOOL_IDS,
    WORKFLOW_ACTION_CAPABILITY_IDS,
    ApprovalRequirement,
    CapabilityCategory,
    CapabilityContext,
    CapabilityDeniedError,
    CapabilityEffect,
    CapabilityGateway,
    CapabilityPolicy,
    CapabilityRegistry,
    CapabilityRisk,
    CapabilitySource,
    CapabilitySpec,
    PolicyDecisionType,
    capability_diagnostics,
    capability_scope,
)
from app.logic.workflow_orchestrator import (
    PendingWorkflowStore,
    WorkflowAction,
    WorkflowActionType,
    WorkflowExecutor,
    WorkflowIntent,
    WorkflowPlan,
    serialize_workflow_for_persistence,
)
from app.services.email_delivery_service import EmailDeliveryResult


OWNER = "owner@example.com"


def context(source, *, owner=OWNER, **overrides):
    values = {"owner": owner, "source": source}
    values.update(overrides)
    return CapabilityContext(**values)


def delivery_plan(*, sensitive=False, arguments=None):
    return WorkflowPlan(
        owner=OWNER,
        intent=WorkflowIntent.DELIVER_EMAIL,
        actions=[
            WorkflowAction(
                id="deliver",
                action_type=WorkflowActionType.DELIVER_EMAIL,
                sensitive=sensitive,
                terminal=True,
                arguments=arguments or {},
            )
        ],
        active_draft=normalize_email_draft({
            "recipient": OWNER,
            "subject": "Policy",
            "body": "Policy boundary test.",
        }),
        expires_at=time.time() + 300,
    )


class FakeDeliveryService:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    @staticmethod
    def is_authorized(admin_key):
        return admin_key == "valid-key"

    def send_approved_email(
        self,
        *,
        draft,
        owner,
        admin_key,
        request_id,
        capability_context,
    ):
        with self.lock:
            self.calls.append(request_id)
        return EmailDeliveryResult(
            success=True,
            status="SIMULATE SUCCESS",
            request_id=request_id,
            mode="simulated",
        )


class CapabilityRegistryTests(unittest.TestCase):
    def test_policy_version_diagnostics_and_registry_are_frozen(self):
        self.assertEqual(CAPABILITY_POLICY_VERSION, 1)
        self.assertEqual(capability_diagnostics(), {
            "policy_version": 1,
            "registered_capabilities": len(CAPABILITY_REGISTRY),
        })
        self.assertTrue(CAPABILITY_REGISTRY.frozen)
        email = CAPABILITY_REGISTRY.get("email.deliver")
        self.assertEqual(email.effect, CapabilityEffect.EXTERNAL_MUTATION)
        self.assertEqual(email.risk, CapabilityRisk.HIGH)
        self.assertEqual(email.approval, ApprovalRequirement.REQUEST_SCOPED_AUTHORIZATION)
        self.assertTrue(email.requires_owner)
        self.assertTrue(email.idempotent)
        with self.assertRaises(FrozenInstanceError):
            email.approval = ApprovalRequirement.NONE
        with self.assertRaises(TypeError):
            CAPABILITY_REGISTRY.specs["email.deliver"] = email

    def test_duplicate_registration_and_runtime_registration_are_rejected(self):
        registry = CapabilityRegistry()
        spec = CapabilitySpec(
            "test.read",
            CapabilityCategory.INTERNAL,
            CapabilityEffect.READ_ONLY,
            CapabilityRisk.LOW,
            ApprovalRequirement.NONE,
            False,
            frozenset({CapabilitySource.SYSTEM}),
        )
        registry.register(spec)
        with self.assertRaisesRegex(ValueError, "duplicate_capability"):
            registry.register(spec)
        registry.freeze()
        with self.assertRaisesRegex(RuntimeError, "capability_registry_frozen"):
            registry.register(spec)

    def test_unknown_capability_fails_closed_and_handler_cannot_be_rebound(self):
        calls = []
        gateway = CapabilityGateway({"web.search": lambda query: calls.append(query)})
        with self.assertRaises(CapabilityDeniedError) as raised:
            gateway.invoke(
                "some_new_tool",
                context=context(CapabilitySource.DIRECT_TOOL),
                arguments={"approved": True},
            )
        self.assertEqual(raised.exception.reason, "unknown_capability")
        self.assertEqual(calls, [])
        with self.assertRaises(TypeError):
            gateway.bindings["web.search"] = lambda query: None

    def test_workflow_and_active_tool_inventories_are_exhaustive(self):
        self.assertEqual(
            set(WORKFLOW_ACTION_CAPABILITY_IDS),
            {item.value for item in WorkflowActionType},
        )
        for capability_id in WORKFLOW_ACTION_CAPABILITY_IDS.values():
            self.assertIsNotNone(CAPABILITY_REGISTRY.get(capability_id))
        active = {
            "web_search_text": tools.search_tool,
            "build_email_draft_tool": tools.build_email_draft_tool,
            "image_generate_tool": tools.image_generate_tool,
            "image_search_tool": tools.image_search_tool,
            "recall_memory": tools.recall_memory,
            "archive_insight": tools.archive_insight,
        }
        self.assertEqual(set(active), set(ACTIVE_TOOL_CAPABILITY_IDS))
        for tool_name, crew_tool in active.items():
            self.assertEqual(crew_tool.func.capability_id, ACTIVE_TOOL_CAPABILITY_IDS[tool_name])
        self.assertEqual(INTERNAL_PURE_TOOL_IDS, {"calculate_horoscope", "analyze_palm_lines"})
        decorated_names = set(re.findall(r'@tool\("([^"]+)"\)', inspect.getsource(tools)))
        self.assertEqual(
            decorated_names,
            set(ACTIVE_TOOL_CAPABILITY_IDS) | set(INTERNAL_PURE_TOOL_IDS),
        )


class CapabilityDecisionTests(unittest.TestCase):
    def test_owner_and_source_restrictions(self):
        missing_owner = CAPABILITY_POLICY.evaluate(
            "memory.read",
            context(CapabilitySource.AGENT, owner=None),
        )
        self.assertEqual(missing_owner.decision, PolicyDecisionType.DENY)
        self.assertEqual(missing_owner.reason, "owner_required")

        expected = {
            CapabilitySource.DIRECT_TOOL: PolicyDecisionType.ALLOW,
            CapabilitySource.AGENT: PolicyDecisionType.ALLOW,
            CapabilitySource.WORKFLOW: PolicyDecisionType.ALLOW,
            CapabilitySource.HTTP: PolicyDecisionType.DENY,
        }
        for source, decision_type in expected.items():
            with self.subTest(source=source):
                decision = CAPABILITY_POLICY.evaluate("web.search", context(source))
                self.assertEqual(decision.decision, decision_type)

    def test_delivery_approval_cannot_come_from_arguments(self):
        gateway_calls = []
        gateway = CapabilityGateway({"email.deliver": lambda **kwargs: gateway_calls.append(kwargs)})
        workflow_context = context(
            CapabilitySource.WORKFLOW,
            workflow_id="workflow-1",
            workflow_status="running",
            workflow_approval_state="required",
            workflow_lease_valid=True,
        )
        with self.assertRaises(CapabilityDeniedError) as raised:
            gateway.invoke(
                "email.deliver",
                context=workflow_context,
                arguments={
                    "approved": True,
                    "authorized": True,
                    "admin_verified": True,
                },
            )
        self.assertEqual(raised.exception.decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(gateway_calls, [])

    def test_external_mutation_requires_owner_and_cancellation_wins(self):
        no_owner = CAPABILITY_POLICY.evaluate(
            "email.deliver",
            context(
                CapabilitySource.WORKFLOW,
                owner=None,
                workflow_status="running",
                workflow_approval_state="approved",
                workflow_lease_valid=True,
                request_authorization_verified=True,
            ),
        )
        self.assertEqual(no_owner.reason, "owner_required")
        cancelled = CAPABILITY_POLICY.evaluate(
            "email.deliver",
            context(
                CapabilitySource.WORKFLOW,
                workflow_status="running",
                workflow_approval_state="approved",
                workflow_lease_valid=True,
                request_authorization_verified=True,
                cancel_requested=True,
            ),
        )
        self.assertEqual(cancelled.decision, PolicyDecisionType.DENY)
        self.assertEqual(cancelled.reason, "workflow_cancelled")

    def test_gateway_rejects_owner_argument_mismatch(self):
        calls = []
        gateway = CapabilityGateway({"email.deliver": lambda **kwargs: calls.append(kwargs)})
        with self.assertRaises(CapabilityDeniedError) as raised:
            gateway.invoke(
                "email.deliver",
                context=context(
                    CapabilitySource.HTTP,
                    request_authorization_verified=True,
                ),
                arguments={"owner": "other@example.com"},
            )
        self.assertEqual(raised.exception.reason, "owner_mismatch")
        self.assertEqual(calls, [])

    def test_policy_logs_only_low_cardinality_metadata(self):
        secret_values = [
            "private query words",
            "recipient@example.com",
            "admin-secret-value",
            "Authorization: Bearer token",
        ]
        with self.assertLogs("AllTimeHelper", level="INFO") as captured:
            CAPABILITY_POLICY.evaluate(
                "email.deliver",
                context(
                    CapabilitySource.HTTP,
                    owner=secret_values[1],
                    request_authorization_verified=False,
                ),
            )
        output = "\n".join(captured.output)
        self.assertIn("capability=email.deliver", output)
        self.assertIn("decision=require_approval", output)
        self.assertIn("reason=external_mutation_not_authorized", output)
        for value in secret_values:
            self.assertNotIn(value, output)

        with self.assertLogs("AllTimeHelper", level="INFO") as unknown_logs:
            CAPABILITY_POLICY.evaluate(
                "not-a-capability admin-secret-value",
                context(CapabilitySource.SYSTEM),
            )
        unknown_output = "\n".join(unknown_logs.output)
        self.assertIn("capability=unknown", unknown_output)
        self.assertNotIn("admin-secret-value", unknown_output)


class CapabilityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.store = PendingWorkflowStore(
            ttl_seconds=60,
            db_file=Path(self.tempdir.name) / "workflows.db",
        )

    def test_planner_sensitive_false_and_fake_flags_still_pause_delivery(self):
        delivery = FakeDeliveryService()
        plan = delivery_plan(
            sensitive=False,
            arguments={"approved": True, "authorized": True, "admin_verified": True},
        )
        result = WorkflowExecutor(
            pending_store=self.store,
            delivery_service=delivery,
        ).execute(plan)
        self.assertTrue(result.paused)
        self.assertEqual(result.actions["deliver"].state.value, "paused")
        self.assertEqual(delivery.calls, [])
        snapshot = self.store.backend.get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["approval_state"], "required")

    def test_store_refuses_unapproved_delivery_claim_when_sensitive_false(self):
        plan = delivery_plan(sensitive=False)
        self.assertTrue(self.store.backend.create(serialize_workflow_for_persistence(plan)))
        self.assertTrue(self.store.backend.claim(plan.workflow_id, OWNER, "execution-1"))
        self.assertFalse(
            self.store.backend.claim_action(
                plan.workflow_id,
                OWNER,
                "deliver",
                "execution-1",
            )
        )

    def test_approved_then_cancelled_workflow_never_calls_sender(self):
        delivery = FakeDeliveryService()
        plan = delivery_plan()
        WorkflowExecutor(
            pending_store=self.store,
            delivery_service=delivery,
        ).execute(plan)
        restored = self.store.peek(OWNER)
        original_policy_state = self.store.backend.policy_state
        cancelled = False

        def cancel_before_policy(workflow_id, owner, execution_id):
            nonlocal cancelled
            if not cancelled:
                cancelled = True
                self.store.backend.request_cancel(workflow_id, owner)
            return original_policy_state(workflow_id, owner, execution_id)

        with patch.object(self.store.backend, "policy_state", side_effect=cancel_before_policy):
            result = WorkflowExecutor(
                pending_store=self.store,
                delivery_service=delivery,
            ).execute(restored, admin_key="valid-key")
        self.assertTrue(result.cancelled)
        self.assertEqual(delivery.calls, [])

    def test_direct_and_agent_tools_enforce_policy_at_handler_execution(self):
        class SearchClient:
            @staticmethod
            def text(*args, **kwargs):
                return [{"title": "Result", "body": "Body", "href": "https://example.com"}]

            @staticmethod
            def news(*args, **kwargs):
                return []

            @staticmethod
            def images(*args, **kwargs):
                return [{"image": "https://example.com/image.png"}]

        with (
            capability_scope(context(CapabilitySource.DIRECT_TOOL)),
            patch.object(tools, "_ddgs_client", return_value=SearchClient()),
        ):
            output = tools.search_tool.func(query="bounded query")
        self.assertIn("https://example.com", output)

        with capability_scope(context(CapabilitySource.HTTP)):
            with self.assertRaises(CapabilityDeniedError) as raised:
                tools.build_email_draft_tool.func(
                    recipient=OWNER,
                    subject="Denied",
                    body="The handler must not execute from HTTP.",
                )
        self.assertEqual(raised.exception.reason, "source_not_allowed")

        with capability_scope(context(CapabilitySource.AGENT)):
            with patch.object(tools, "_ddgs_client", return_value=SearchClient()):
                output = tools.image_search_tool.func(query="agent image")
        self.assertIn("https://example.com", output)

    def test_attachment_policy_does_not_replace_owner_lookup(self):
        with tempfile.TemporaryDirectory() as root, patch.object(attachment_store, "ATTACHMENT_ROOT", root):
            with capability_scope(context(CapabilitySource.HTTP)):
                saved = attachment_store.save_attachment_bytes(
                    "note.txt",
                    "text/plain",
                    b"owner scoped",
                    OWNER,
                )
            with capability_scope(context(CapabilitySource.HTTP, owner="other@example.com")):
                with self.assertRaises(attachment_store.AttachmentStoreError):
                    attachment_store.resolve_attachment_metadata(saved["id"], "other@example.com")

    def test_http_delivery_uses_policy_then_key_verification_and_idempotency(self):
        from fastapi import HTTPException
        from app.routes import email_delivery
        from app.services import email_delivery_service as service_module

        sends = []
        service = service_module.EmailDeliveryService(
            key_verifier=lambda candidate: candidate == "valid-key",
            sender=lambda **kwargs: sends.append(kwargs) or "SIMULATE SUCCESS",
        )
        request = email_delivery.SendDraftRequest(
            draft={"recipient": OWNER, "subject": "HTTP", "body": "Policy"},
            admin_key="valid-key",
            request_id="policy-http-1",
        )
        with (
            patch.object(email_delivery, "email_delivery_service", service),
            patch.object(service_module, "_existing_delivery", return_value=None),
            patch.object(service_module, "_record_delivery"),
            patch.object(CAPABILITY_POLICY, "evaluate", wraps=CAPABILITY_POLICY.evaluate) as evaluate,
        ):
            first = email_delivery.send_approved_email_draft(request, current_user=OWNER)
            duplicate = email_delivery.send_approved_email_draft(request, current_user=OWNER)
        self.assertTrue(first["success"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(sends), 1)
        http_contexts = [
            call.args[1]
            for call in evaluate.call_args_list
            if call.args and call.args[0] == "email.deliver"
        ]
        self.assertTrue(http_contexts)
        self.assertTrue(all(item.source == CapabilitySource.HTTP for item in http_contexts))

        denied = request.model_copy(update={"admin_key": "invalid-key", "request_id": "policy-http-2"})
        with patch.object(email_delivery, "email_delivery_service", service):
            with self.assertRaises(HTTPException) as raised:
                email_delivery.send_approved_email_draft(denied, current_user=OWNER)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(sends), 1)


if __name__ == "__main__":
    unittest.main()
