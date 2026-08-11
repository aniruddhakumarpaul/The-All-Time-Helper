import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.contracts.email_draft import normalize_email_draft
from app.logic.workflow_orchestrator import (
    PendingWorkflowStore,
    WorkflowAction,
    WorkflowActionType,
    WorkflowApprovalState,
    WorkflowExecutor,
    WorkflowIntent,
    WorkflowPlan,
    restore_workflow_from_persistence,
    serialize_workflow_for_persistence,
)
from app.logic.workflow_store import (
    ACTION_CLASSES,
    CANCELLED,
    COMPLETED_ACTION,
    INTERRUPTED,
    INTERRUPTED_ACTION,
    SQLiteWorkflowStore,
    UNKNOWN_EXTERNAL_RESULT,
    WorkflowActionClass,
    WorkflowCapacityError,
)
from app.routes import workflows
from app.services.email_delivery_service import EmailAuthorizationError, EmailDeliveryResult


OWNER = "owner@example.com"
OTHER = "other@example.com"


class FakeDeliveryService:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    @staticmethod
    def is_authorized(admin_key):
        return admin_key == "valid-key"

    def send_approved_email(self, *, draft, owner, admin_key, request_id):
        if not self.is_authorized(admin_key):
            raise EmailAuthorizationError("invalid")
        with self.lock:
            self.calls.append(request_id)
        return EmailDeliveryResult(
            success=True,
            status="SIMULATE SUCCESS",
            request_id=request_id,
            mode="simulated",
        )


def email_draft():
    return normalize_email_draft({
        "recipient": OWNER,
        "subject": "Durable workflow",
        "body": "This draft survives a controlled approval restart.",
        "tone": "formal",
    })


def make_plan(
    *,
    owner=OWNER,
    actions=None,
    approval_state=WorkflowApprovalState.NOT_REQUIRED,
    active_draft=None,
):
    return WorkflowPlan(
        owner=owner,
        intent=WorkflowIntent.DELIVER_EMAIL,
        actions=actions or [
            WorkflowAction(
                id="deliver",
                action_type=WorkflowActionType.DELIVER_EMAIL,
                sensitive=True,
                terminal=True,
            )
        ],
        approval_state=approval_state,
        active_draft=active_draft or email_draft(),
        topic="durability",
        expires_at=time.time() + 600,
    )


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.db_file = Path(self.tempdir.name) / "workflows.db"

    def backend(self, **overrides):
        options = {
            "retention_seconds": 60,
            "approval_ttl_seconds": 60,
            "lease_seconds": 5,
            "lease_renew_seconds": 0.2,
            "max_events": 50,
            "max_retained_runs": 50,
            "max_result_bytes": 4096,
            "max_storage_bytes": 1_000_000,
        }
        options.update(overrides)
        return SQLiteWorkflowStore(self.db_file, **options)

    def facade(self, backend=None):
        return PendingWorkflowStore(ttl_seconds=60, backend=backend or self.backend())

    @staticmethod
    def expire_lease(db_file, workflow_id):
        with closing(sqlite3.connect(db_file)) as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE workflow_id=?",
                (time.time() - 1, workflow_id),
            )
            db.commit()

    def test_schema_uses_wal_foreign_keys_and_is_idempotent(self):
        first = self.backend()
        second = self.backend()
        with closing(sqlite3.connect(self.db_file)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            version = db.execute("SELECT version FROM workflow_schema WHERE id=1").fetchone()[0]
        self.assertEqual(version, 1)
        self.assertEqual(first.db_file, second.db_file)

    def test_schema_initialization_is_safe_across_workers(self):
        barrier = threading.Barrier(4)

        def initialize(_index):
            barrier.wait()
            return self.backend().db_file

        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(initialize, range(4)))
        self.assertEqual(paths, [self.db_file] * 4)

    def test_action_side_effect_classification_is_explicit(self):
        self.assertEqual(
            ACTION_CLASSES["deliver_email"],
            WorkflowActionClass.EXTERNAL_SIDE_EFFECT,
        )
        for action_type in (
            "web_search",
            "image_search",
            "image_generate",
            "build_email_draft",
            "update_email_draft",
            "attach_image",
            "general_response",
        ):
            self.assertNotEqual(
                ACTION_CLASSES[action_type],
                WorkflowActionClass.EXTERNAL_SIDE_EFFECT,
            )

    def test_workflow_survives_new_store_instance(self):
        plan = make_plan()
        first = self.backend()
        self.assertTrue(first.create(serialize_workflow_for_persistence(plan), job_id="job-1"))

        restarted = self.backend()
        snapshot = restarted.get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["workflow_id"], plan.workflow_id)
        self.assertEqual(snapshot["job_id"], "job-1")
        self.assertEqual(snapshot["actions"][0]["state"], "pending")

    def test_owner_mismatch_is_not_found_for_get_claim_cancel_and_list(self):
        plan = make_plan()
        store = self.backend()
        store.create(serialize_workflow_for_persistence(plan))

        self.assertIsNone(store.get(plan.workflow_id, OTHER))
        self.assertFalse(store.claim(plan.workflow_id, OTHER, "other-execution"))
        self.assertFalse(store.request_cancel(plan.workflow_id, OTHER))
        self.assertEqual(store.list_for_owner(OTHER), [])

    def test_concurrent_action_claim_has_one_winner_and_one_result(self):
        action = WorkflowAction(
            id="generate",
            action_type=WorkflowActionType.IMAGE_GENERATE,
            terminal=True,
        )
        plan = make_plan(actions=[action])
        store = self.backend()
        store.create(serialize_workflow_for_persistence(plan))
        self.assertTrue(store.claim(plan.workflow_id, OWNER, "execution-a"))
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            return self.backend().claim_action(
                plan.workflow_id, OWNER, action.id, "execution-a"
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            wins = [future.result() for future in [pool.submit(claim), pool.submit(claim)]]
        self.assertEqual(wins.count(True), 1)
        self.assertTrue(store.finish_action(
            plan.workflow_id,
            OWNER,
            action.id,
            "execution-a",
            state=COMPLETED_ACTION,
            output={"kind": "text", "value": "safe"},
        ))
        self.assertFalse(store.finish_action(
            plan.workflow_id,
            OWNER,
            action.id,
            "execution-a",
            state=COMPLETED_ACTION,
        ))
        self.assertEqual(store.get(plan.workflow_id, OWNER)["actions"][0]["attempt"], 1)

    def test_sensitive_action_cannot_be_claimed_before_durable_approval(self):
        plan = make_plan()
        store = self.backend()
        store.create(serialize_workflow_for_persistence(plan))
        self.assertTrue(store.claim(plan.workflow_id, OWNER, "execution-a"))
        self.assertFalse(store.claim_action(
            plan.workflow_id,
            OWNER,
            "deliver",
            "execution-a",
        ))

    def test_lost_lease_rejects_stale_completion_and_marks_safe_action_interrupted(self):
        action = WorkflowAction(
            id="search",
            action_type=WorkflowActionType.WEB_SEARCH,
            terminal=True,
        )
        plan = make_plan(actions=[action])
        worker_a = self.backend()
        worker_a.create(serialize_workflow_for_persistence(plan))
        worker_a.claim(plan.workflow_id, OWNER, "execution-a")
        worker_a.claim_action(plan.workflow_id, OWNER, action.id, "execution-a")
        self.expire_lease(self.db_file, plan.workflow_id)

        self.assertFalse(worker_a.finish_action(
            plan.workflow_id,
            OWNER,
            action.id,
            "execution-a",
            state=COMPLETED_ACTION,
        ))
        snapshot = self.backend().get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["status"], INTERRUPTED)
        self.assertEqual(snapshot["actions"][0]["state"], INTERRUPTED_ACTION)

    def test_restart_during_delivery_is_unknown_and_never_reclaimed(self):
        plan = make_plan()
        first = self.backend()
        record = serialize_workflow_for_persistence(plan)
        first.create(record)
        first.claim(plan.workflow_id, OWNER, "pause-execution")
        self.assertTrue(first.pause_for_approval(
            plan.workflow_id,
            OWNER,
            "deliver",
            "pause-execution",
            record | {"approval_state": "required"},
        ))
        self.assertTrue(first.claim(plan.workflow_id, OWNER, "execution-a"))
        self.assertTrue(first.approve(plan.workflow_id, OWNER, "execution-a"))
        self.assertTrue(first.claim_action(plan.workflow_id, OWNER, "deliver", "execution-a"))
        self.expire_lease(self.db_file, plan.workflow_id)

        restarted = self.backend()
        snapshot = restarted.get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["status"], INTERRUPTED)
        self.assertEqual(snapshot["actions"][0]["state"], UNKNOWN_EXTERNAL_RESULT)
        self.assertFalse(restarted.claim(plan.workflow_id, OWNER, "execution-b"))

    def test_approval_survives_restart_and_delivery_executes_once(self):
        delivery = FakeDeliveryService()
        first_facade = self.facade()
        paused = WorkflowExecutor(
            pending_store=first_facade,
            delivery_service=delivery,
        ).execute(make_plan())
        self.assertTrue(paused.paused)

        restarted_facade = self.facade(self.backend())
        restored = restarted_facade.peek(OWNER)
        result = WorkflowExecutor(
            pending_store=restarted_facade,
            delivery_service=delivery,
        ).execute(restored, admin_key="valid-key")
        duplicate = WorkflowExecutor(
            pending_store=self.facade(self.backend()),
            delivery_service=delivery,
        ).execute(restored, admin_key="valid-key")

        self.assertEqual(result.message, "Email simulated successfully.")
        self.assertEqual(len(delivery.calls), 1)
        self.assertIn("unavailable", duplicate.message)

    def test_two_workers_resume_paused_workflow_without_double_delivery(self):
        delivery = FakeDeliveryService()
        first = self.facade()
        WorkflowExecutor(pending_store=first, delivery_service=delivery).execute(make_plan())
        plan_a = self.facade(self.backend()).peek(OWNER)
        plan_b = self.facade(self.backend()).peek(OWNER)
        barrier = threading.Barrier(2)

        def resume(plan):
            barrier.wait()
            return WorkflowExecutor(
                pending_store=self.facade(self.backend()),
                delivery_service=delivery,
            ).execute(plan, admin_key="valid-key")

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result() for future in [pool.submit(resume, plan_a), pool.submit(resume, plan_b)]]
        self.assertEqual(len(delivery.calls), 1)
        self.assertEqual(sum(item.message == "Email simulated successfully." for item in outcomes), 1)

    def test_invalid_approval_never_claims_or_persists_candidate(self):
        delivery = FakeDeliveryService()
        facade = self.facade()
        WorkflowExecutor(pending_store=facade, delivery_service=delivery).execute(make_plan())
        restored = facade.peek(OWNER)
        result = WorkflowExecutor(
            pending_store=facade,
            delivery_service=delivery,
        ).execute(restored, admin_key="invalid-admin-key")

        snapshot = facade.backend.get(restored.workflow_id, OWNER)
        self.assertTrue(result.paused)
        self.assertEqual(snapshot["status"], "paused")
        self.assertIsNone(snapshot["execution_id"])
        self.assertEqual(delivery.calls, [])
        self.assertNotIn(b"invalid-admin-key", self.db_file.read_bytes())

    def test_cancel_before_execution_while_paused_and_duplicate_cancel_are_idempotent(self):
        facade = self.facade()
        plan = make_plan()
        WorkflowExecutor(
            pending_store=facade,
            delivery_service=FakeDeliveryService(),
        ).execute(plan)

        self.assertTrue(facade.backend.request_cancel(plan.workflow_id, OWNER))
        self.assertTrue(facade.backend.request_cancel(plan.workflow_id, OWNER))
        snapshot = facade.backend.get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["status"], CANCELLED)
        self.assertEqual(snapshot["actions"][0]["state"], "cancelled")
        self.assertIsNone(facade.peek(OWNER))

    def test_cancel_during_safe_action_prevents_later_delivery(self):
        started = threading.Event()
        release = threading.Event()
        delivery = FakeDeliveryService()
        actions = [
            WorkflowAction(
                id="search",
                action_type=WorkflowActionType.WEB_SEARCH,
            ),
            WorkflowAction(
                id="deliver",
                action_type=WorkflowActionType.DELIVER_EMAIL,
                depends_on=["search"],
                sensitive=True,
                terminal=True,
            ),
        ]
        plan = make_plan(actions=actions)
        facade = self.facade()

        def search(_query):
            started.set()
            release.wait(timeout=3)
            return "safe result"

        result_box = []
        worker = threading.Thread(
            target=lambda: result_box.append(WorkflowExecutor(
                pending_store=facade,
                delivery_service=delivery,
                web_search=search,
            ).execute(plan)),
        )
        worker.start()
        self.assertTrue(started.wait(timeout=2))
        facade.backend.request_cancel(plan.workflow_id, OWNER)
        time.sleep(0.3)
        release.set()
        worker.join(timeout=4)

        self.assertEqual(delivery.calls, [])
        self.assertTrue(result_box[0].cancelled)
        self.assertEqual(facade.backend.get(plan.workflow_id, OWNER)["status"], CANCELLED)

    def test_expired_paused_workflow_becomes_unavailable(self):
        facade = self.facade()
        plan = make_plan()
        WorkflowExecutor(
            pending_store=facade,
            delivery_service=FakeDeliveryService(),
        ).execute(plan)
        with closing(sqlite3.connect(self.db_file)) as db:
            db.execute(
                "UPDATE workflow_runs SET expires_at=? WHERE workflow_id=?",
                (time.time() - 1, plan.workflow_id),
            )
            db.commit()
        self.assertIsNone(self.backend().get(plan.workflow_id, OWNER))

    def test_persisted_bytes_exclude_credentials_urls_paths_and_attachment_content(self):
        draft = normalize_email_draft({
            "recipient": OWNER,
            "subject": "Safe persistence",
            "body": "Admin Key: configured-admin-secret must remain request scoped.",
            "attachments": [{
                "id": "attachment-1",
                "filename": r"C:\private\photo.png",
                "mime_type": "image/png",
                "content": "data:image/png;base64,TOPSECRETBYTES",
            }],
        })
        plan = make_plan(
            active_draft=draft,
            actions=[WorkflowAction(
                id="search",
                action_type=WorkflowActionType.WEB_SEARCH,
                arguments={
                    "query": "Authorization: Bearer SECRET_TOKEN https://user:pass@example.com/private",
                    "admin_key": "ADMIN_KEY_SECRET",
                    "smtp_password": "SMTP_SECRET",
                },
                terminal=True,
            )],
        )
        with patch.dict("os.environ", {"ADMIN_KEY": "configured-admin-secret"}):
            self.backend().create(serialize_workflow_for_persistence(plan))
        persisted = b"".join(
            path.read_bytes() for path in self.db_file.parent.glob("workflows.db*")
        ).decode("utf-8", errors="ignore")
        for forbidden in (
            "SECRET_TOKEN",
            "ADMIN_KEY_SECRET",
            "SMTP_SECRET",
            "TOPSECRETBYTES",
            "configured-admin-secret",
            "user:pass@example.com",
            r"C:\private",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_unknown_future_workflow_version_fails_closed(self):
        payload = serialize_workflow_for_persistence(make_plan())
        payload["schema_version"] = 99
        with self.assertRaises(ValueError):
            restore_workflow_from_persistence(payload)

    def test_events_and_global_storage_are_bounded(self):
        actions = [
            WorkflowAction(
                id=f"step-{index}",
                action_type=WorkflowActionType.GENERAL_RESPONSE,
                terminal=index == 7,
            )
            for index in range(8)
        ]
        plan = make_plan(actions=actions)
        store = self.backend(max_events=3, max_storage_bytes=12_000)
        store.create(serialize_workflow_for_persistence(plan))
        store.claim(plan.workflow_id, OWNER, "execution-a")
        for action in actions:
            self.assertTrue(store.claim_action(plan.workflow_id, OWNER, action.id, "execution-a"))
            self.assertTrue(store.finish_action(
                plan.workflow_id,
                OWNER,
                action.id,
                "execution-a",
                state=COMPLETED_ACTION,
                output={"kind": "text", "value": "x" * 20},
            ))
        with closing(sqlite3.connect(self.db_file)) as db:
            event_count = db.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE workflow_id=?",
                (plan.workflow_id,),
            ).fetchone()[0]
        self.assertLessEqual(event_count, 3)
        self.assertLessEqual(store.logical_usage(), store.max_storage_bytes)

        too_large = make_plan(actions=[WorkflowAction(
            id="large",
            action_type=WorkflowActionType.GENERAL_RESPONSE,
            arguments={"message": "x" * 100_000},
            terminal=True,
        )])
        tiny = SQLiteWorkflowStore(
            Path(self.tempdir.name) / "tiny.db",
            max_result_bytes=512,
            max_storage_bytes=4096,
        )
        with self.assertRaises(WorkflowCapacityError):
            tiny.create(serialize_workflow_for_persistence(too_large))


class WorkflowRouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        backend = SQLiteWorkflowStore(Path(self.tempdir.name) / "workflows.db")
        self.facade = PendingWorkflowStore(backend=backend)
        self.plan = make_plan()
        self.facade.ensure(self.plan)
        self.app = FastAPI()
        self.app.include_router(workflows.router)

    async def request_as(self, owner, method, path):
        self.app.dependency_overrides[workflows.get_current_user] = lambda: owner
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            with patch.object(workflows, "pending_workflow_store", self.facade):
                return await client.request(method, path)

    async def test_served_recovery_routes_are_owner_scoped_private_and_sanitized(self):
        listed = await self.request_as(OWNER, "GET", "/workflows")
        detail = await self.request_as(
            OWNER, "GET", f"/workflows/{self.plan.workflow_id}"
        )
        hidden = await self.request_as(
            OTHER, "GET", f"/workflows/{self.plan.workflow_id}"
        )
        cancelled = await self.request_as(
            OWNER, "POST", f"/workflows/{self.plan.workflow_id}/cancel"
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(detail.headers["cache-control"], "private, no-store")
        self.assertEqual(hidden.headers["cache-control"], "private, no-store")
        self.assertNotIn("plan", detail.json())
        self.assertNotIn("owner", detail.json())
        self.assertNotIn("output", json.dumps(detail.json()))


if __name__ == "__main__":
    unittest.main()
