import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from app.logic.action_outbox import CLAIMED as OUTBOX_CLAIMED, DISPATCHING, SQLiteActionOutboxStore
from app.logic.capability_policy import (
    CAPABILITY_POLICY,
    ApprovalRequirement,
    CapabilityContext,
    CapabilityDeniedError,
    CapabilityEffect,
    CapabilitySource,
    PolicyDecisionType,
)
from app.logic.human_approval import (
    APPROVED,
    CANCELLED,
    CLAIMED,
    CONSUMED,
    EXPIRED,
    PENDING,
    REJECTED,
    SUPERSEDED,
    ApprovalDisplayField,
    ApprovalManifest,
    ApprovalRequestMaterial,
    HumanApprovalCapacityError,
    HumanApprovalConflict,
    HumanApprovalValidationError,
    SQLiteHumanApprovalStore,
    canonical_action_fingerprint,
)


OWNER = "owner@example.test"
OTHER = "other@example.test"
CAPABILITY = "mcp.external_mutation"
REQUEST_KEY = "request:phase6-test"
MANIFEST = ApprovalManifest(
    "Create one issue",
    "Create an issue in the selected repository.",
    (ApprovalDisplayField("Repository", "example/project"),),
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def decision_for(owner=OWNER, source=CapabilitySource.AGENT, **overrides):
    values = {"owner": owner, "source": source}
    values.update(overrides)
    return CAPABILITY_POLICY.evaluate(CAPABILITY, CapabilityContext(**values))


class HumanApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.db_file = Path(self.tempdir.name) / "workflows.db"
        self.now = [1_800_000_000.0]
        self.store = self.make_store()

    def make_store(self, **overrides):
        options = {
            "approval_ttl_seconds": 900,
            "claim_seconds": 30,
            "terminal_retention_seconds": 30 * 24 * 60 * 60,
            "max_retained": 100,
            "max_storage_bytes": 1_000_000,
            "clock": lambda: self.now[0],
        }
        options.update(overrides)
        return SQLiteHumanApprovalStore(self.db_file, **options)

    def request(self, *, owner=OWNER, key=REQUEST_KEY, payload="payload-A", source=CapabilitySource.AGENT, manifest=MANIFEST):
        decision = decision_for(owner, source)
        return self.store.request(
            owner=owner,
            capability_id=CAPABILITY,
            source=source,
            request_key=key,
            payload_fingerprint=fingerprint(payload),
            manifest=manifest,
            decision=decision,
        )

    def approve(self, record, *, store=None):
        return (store or self.store).approve(record["approval_id"], OWNER, record["version"])

    def claim(self, record, *, store=None, owner=OWNER, payload="payload-A", key=REQUEST_KEY):
        return (store or self.store).claim(
            record["approval_id"], owner,
            capability_id=CAPABILITY,
            payload_fingerprint=fingerprint(payload),
            request_key=key,
        )

    def test_schema_and_same_request_are_idempotent_and_owner_is_pseudonymous(self):
        first = self.request()
        duplicate = self.request()
        self.assertEqual(first["approval_id"], duplicate["approval_id"])
        self.assertEqual(first["status"], PENDING)
        self.assertEqual(first["risk_snapshot"], "high")
        self.assertEqual(first["schema_version"], 1)
        self.assertNotIn("owner", first)
        self.assertEqual(first["owner_scope"], hashlib.sha256(OWNER.casefold().encode()).hexdigest())
        self.assertEqual(len(first["request_key_digest"]), 64)
        self.assertEqual(len(first["payload_fingerprint"]), 64)
        with closing(sqlite3.connect(self.db_file)) as db:
            schema = db.execute("SELECT version FROM human_approval_schema WHERE id=1").fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(schema, (1,))
        self.assertIn("human_approvals", tables)
        self.assertNotIn("approvals", tables)

    def test_exact_claim_grant_is_required_by_policy(self):
        record = self.request()
        pending = decision_for(OWNER)
        self.assertEqual(pending.decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(self.approve(record)["status"], APPROVED)
        grant = self.claim(record)
        self.assertIsNotNone(grant)

        def evaluate(**overrides):
            values = {
                "owner": OWNER,
                "source": CapabilitySource.AGENT,
                "human_approval_grant": grant,
                "action_payload_fingerprint": fingerprint("payload-A"),
                "approval_request_key": REQUEST_KEY,
            }
            values.update(overrides)
            return CAPABILITY_POLICY.evaluate(CAPABILITY, CapabilityContext(**values))

        self.assertEqual(evaluate().decision, PolicyDecisionType.ALLOW)
        self.assertEqual(evaluate(owner=OTHER).decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(evaluate(action_payload_fingerprint=fingerprint("payload-B")).decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(evaluate(approval_request_key="request:other").decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(
            evaluate(human_approval_grant=replace(grant, capability_id="mcp.read")).decision,
            PolicyDecisionType.REQUIRE_APPROVAL,
        )
        self.assertEqual(
            decision_for(OWNER, request_authorization_verified=True).decision,
            PolicyDecisionType.REQUIRE_APPROVAL,
        )

    def test_model_arguments_cannot_forge_approval_and_email_admin_key_stays_separate(self):
        from app.logic.capability_policy import CapabilityGateway

        invoked = []
        gateway = CapabilityGateway({CAPABILITY: lambda **kwargs: invoked.append(kwargs)})
        with self.assertRaises(CapabilityDeniedError) as denied:
            gateway.invoke(
                CAPABILITY,
                context=CapabilityContext(owner=OWNER, source=CapabilitySource.AGENT),
                arguments={
                    "approved": True,
                    "human_confirmed": True,
                    "approval_id": "real-looking-id",
                    "approval_grant": "fake",
                    "authorization_verified": True,
                    "payload_fingerprint": fingerprint("payload-A"),
                },
            )
        self.assertEqual(denied.exception.decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(invoked, [])

        record = self.request()
        self.approve(record)
        grant = self.claim(record)
        generic_only = CAPABILITY_POLICY.evaluate(
            "email.deliver",
            CapabilityContext(
                owner=OWNER,
                source=CapabilitySource.HTTP,
                human_approval_grant=grant,
                action_payload_fingerprint=fingerprint("email"),
                approval_request_key="email:key",
            ),
        )
        self.assertEqual(generic_only.decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertEqual(generic_only.reason, "external_mutation_not_authorized")
        admin_key_only = CAPABILITY_POLICY.evaluate(
            "email.deliver",
            CapabilityContext(
                owner=OWNER,
                source=CapabilitySource.HTTP,
                request_authorization_verified=True,
            ),
        )
        self.assertEqual(admin_key_only.decision, PolicyDecisionType.ALLOW)
        generic_key_and_mcp = CAPABILITY_POLICY.evaluate(
            CAPABILITY,
            CapabilityContext(
                owner=OWNER,
                source=CapabilitySource.AGENT,
                request_authorization_verified=True,
            ),
        )
        self.assertEqual(generic_key_and_mcp.decision, PolicyDecisionType.REQUIRE_APPROVAL)

    def test_payload_change_supersedes_approval_and_invalidates_old_claim(self):
        first = self.request(payload="payload-A")
        self.approve(first)
        stale_grant = self.claim(first, payload="payload-A")
        second = self.request(payload="payload-B")

        self.assertEqual(self.store.get(first["approval_id"], OWNER)["status"], SUPERSEDED)
        self.assertEqual(second["status"], PENDING)
        self.assertFalse(stale_grant.is_valid_for(
            owner=OWNER,
            capability_id=CAPABILITY,
            payload_fingerprint=fingerprint("payload-A"),
            request_key=REQUEST_KEY,
        ))
        changed_action = CAPABILITY_POLICY.evaluate(
            CAPABILITY,
            CapabilityContext(
                owner=OWNER,
                source=CapabilitySource.AGENT,
                human_approval_grant=stale_grant,
                action_payload_fingerprint=fingerprint("payload-B"),
                approval_request_key=REQUEST_KEY,
            ),
        )
        self.assertEqual(changed_action.decision, PolicyDecisionType.REQUIRE_APPROVAL)
        self.assertIsNone(self.claim(second, payload="payload-A"))

    def test_decision_optimistic_version_and_rejected_state_are_terminal(self):
        record = self.request()
        approved = self.store.approve(record["approval_id"], OWNER, 1)
        self.assertEqual(approved["version"], 2)
        self.assertEqual(self.store.approve(record["approval_id"], OWNER, 2)["status"], APPROVED)
        with self.assertRaises(HumanApprovalConflict):
            self.store.reject(record["approval_id"], OWNER, 1)

        rejected = self.request(key="request:reject")
        self.assertEqual(self.store.reject(rejected["approval_id"], OWNER, 1)["status"], REJECTED)
        duplicate = self.request(key="request:reject")
        self.assertEqual(duplicate["approval_id"], rejected["approval_id"])
        self.assertEqual(duplicate["status"], REJECTED)
        with self.assertRaises(HumanApprovalConflict):
            self.store.approve(rejected["approval_id"], OWNER, 1)

    def test_approve_reject_race_has_one_winner_across_store_instances(self):
        record = self.request(key="request:decision-race")
        store_a = self.make_store()
        store_b = self.make_store()
        barrier = threading.Barrier(2)

        def decide(store, action):
            barrier.wait()
            try:
                result = getattr(store, action)(record["approval_id"], OWNER, 1)
                return result["status"]
            except HumanApprovalConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda args: decide(*args), ((store_a, "approve"), (store_b, "reject"))))
        self.assertEqual(sum(item != "conflict" for item in outcomes), 1)
        current = self.store.get(record["approval_id"], OWNER)
        self.assertIn(current["status"], {APPROVED, REJECTED})
        self.assertNotEqual(current["status"], "approved_and_rejected")

    def test_claim_race_has_one_live_grant(self):
        record = self.request(key="request:claim-race")
        self.approve(record)
        stores = (self.make_store(), self.make_store())
        barrier = threading.Barrier(2)

        def claim(store):
            barrier.wait()
            return self.claim(record, store=store, key="request:claim-race")

        with ThreadPoolExecutor(max_workers=2) as pool:
            grants = list(pool.map(claim, stores))
        winners = [grant for grant in grants if grant is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.store.get(record["approval_id"], OWNER)["claim_id"], winners[0].claim_id)

    def test_claim_expiry_recovers_to_approved_then_approval_ttl_expires(self):
        self.store = self.make_store(approval_ttl_seconds=60, claim_seconds=10)
        record = self.request(key="request:expiry")
        self.approve(record)
        grant = self.claim(record, key="request:expiry")
        self.now[0] += 11

        restarted = self.make_store(approval_ttl_seconds=60, claim_seconds=10)
        with closing(sqlite3.connect(self.db_file)) as db:
            before_recovery = db.execute(
                "SELECT status FROM human_approvals WHERE approval_id=?", (record["approval_id"],)
            ).fetchone()[0]
        self.assertEqual(before_recovery, CLAIMED)
        self.assertEqual(restarted.recover_expired(), 1)
        self.assertEqual(restarted.get(record["approval_id"], OWNER)["status"], APPROVED)
        self.assertFalse(grant.is_valid_for(
            owner=OWNER, capability_id=CAPABILITY,
            payload_fingerprint=fingerprint("payload-A"), request_key="request:expiry",
        ))

        fresh = self.claim(record, store=restarted, key="request:expiry")
        self.assertIsNotNone(fresh)
        self.now[0] += 60
        self.assertEqual(restarted.recover_expired(), 1)
        self.assertEqual(restarted.get(record["approval_id"], OWNER)["status"], EXPIRED)
        self.assertIsNone(self.claim(record, store=restarted, key="request:expiry"))

    def test_expired_terminal_approval_remains_visible_until_retention_window(self):
        self.store = self.make_store(approval_ttl_seconds=60, terminal_retention_seconds=120)
        record = self.request(key="request:retention")
        self.now[0] += 60
        self.assertEqual(self.store.get(record["approval_id"], OWNER)["status"], EXPIRED)
        self.now[0] += 119
        self.assertEqual(self.store.get(record["approval_id"], OWNER)["status"], EXPIRED)
        self.now[0] += 1
        self.assertIsNone(self.store.get(record["approval_id"], OWNER))

    def test_consumed_approval_cannot_be_reclaimed_or_reapproved(self):
        record = self.request(key="request:consume")
        self.approve(record)
        grant = self.claim(record, key="request:consume")
        self.assertTrue(self.store.consume(grant))
        self.assertFalse(self.store.consume(grant))
        self.assertIsNone(self.claim(record, key="request:consume"))
        with self.assertRaises(HumanApprovalConflict):
            self.store.approve(record["approval_id"], OWNER, 2)
        self.assertEqual(self.store.get(record["approval_id"], OWNER)["status"], CONSUMED)

    def test_cancellation_preserves_consumed_and_cancels_claimed(self):
        first = self.request(key="request:cancel")
        self.approve(first)
        grant = self.claim(first, key="request:cancel")
        self.assertTrue(self.store.cancel(first["approval_id"], OWNER))
        self.assertEqual(self.store.get(first["approval_id"], OWNER)["status"], CANCELLED)
        self.assertFalse(grant.is_valid_for(
            owner=OWNER, capability_id=CAPABILITY,
            payload_fingerprint=fingerprint("payload-A"), request_key="request:cancel",
        ))

        second = self.request(key="request:consumed-cancel")
        self.approve(second)
        second_grant = self.claim(second, key="request:consumed-cancel")
        self.store.consume(second_grant)
        self.assertFalse(self.store.cancel(second["approval_id"], OWNER))
        self.assertEqual(self.store.get(second["approval_id"], OWNER)["status"], CONSUMED)

    def test_manifest_bounds_and_secret_values_are_rejected(self):
        invalid = (
            ApprovalManifest("x" * 121, "summary"),
            ApprovalManifest("title", "summary", tuple(
                ApprovalDisplayField(f"field-{index}", "value") for index in range(13)
            )),
            ApprovalManifest("title", "summary", (ApprovalDisplayField("Admin Key", "secret"),)),
            ApprovalManifest("title", "Authorization: Bearer token-secret-value", ()),
        )
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                with self.assertRaises(HumanApprovalValidationError):
                    self.request(key=f"request:invalid-{len(str(manifest))}", manifest=manifest)

    def test_canonical_action_fingerprint_is_stable_and_argument_sensitive(self):
        first = canonical_action_fingerprint({
            "server_id": "github",
            "tool_alias": "create_issue",
            "arguments": {"title": "One", "body": "Details"},
        })
        same = canonical_action_fingerprint({
            "arguments": {"body": "Details", "title": "One"},
            "tool_alias": "create_issue",
            "server_id": "github",
        })
        changed = canonical_action_fingerprint({
            "server_id": "github",
            "tool_alias": "create_issue",
            "arguments": {"title": "Two", "body": "Details"},
        })
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)
        with self.assertRaises(HumanApprovalValidationError):
            canonical_action_fingerprint({"arguments": {"body": object()}})

    def test_capacity_never_prunes_active_approvals_and_prunes_terminal_records(self):
        self.store = self.make_store(max_retained=1)
        active = self.request(key="request:active-capacity")
        with self.assertRaises(HumanApprovalCapacityError):
            self.request(key="request:over-capacity")
        self.assertEqual(self.store.get(active["approval_id"], OWNER)["status"], PENDING)

        self.store.reject(active["approval_id"], OWNER, 1)
        next_record = self.request(key="request:after-terminal")
        self.assertEqual(next_record["status"], PENDING)
        self.assertEqual(self.store.aggregate_counts()[PENDING], 1)

    def test_storage_counts_utf8_blob_bytes(self):
        record = self.request(
            key="request:utf8",
            manifest=ApprovalManifest("東京 project", "Review this change."),
        )
        with closing(sqlite3.connect(self.db_file)) as db:
            display = db.execute(
                "SELECT display_json FROM human_approvals WHERE approval_id=?", (record["approval_id"],)
            ).fetchone()[0]
        self.assertGreaterEqual(self.store.logical_usage_bytes(), len(display.encode("utf-8")))
        self.assertGreater(len(display.encode("utf-8")), len(display))

    def test_schema_initialization_is_safe_across_four_workers(self):
        db_file = Path(self.tempdir.name) / "parallel-init.db"
        with ThreadPoolExecutor(max_workers=4) as pool:
            stores = list(pool.map(lambda _: SQLiteHumanApprovalStore(db_file), range(4)))
        self.assertEqual(len(stores), 4)
        db = sqlite3.connect(db_file)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM human_approval_schema").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT version FROM human_approval_schema WHERE id=1").fetchone()[0], 1)
        finally:
            db.close()

    def test_restart_preserves_pending_approved_and_consumed_states(self):
        pending = self.request(key="request:restart-pending")
        approved = self.request(key="request:restart-approved")
        self.approve(approved)
        consumed = self.request(key="request:restart-consumed")
        self.approve(consumed)
        self.store.consume(self.claim(consumed, key="request:restart-consumed"))

        restarted = self.make_store()
        self.assertEqual(restarted.get(pending["approval_id"], OWNER)["status"], PENDING)
        self.assertEqual(restarted.get(approved["approval_id"], OWNER)["status"], APPROVED)
        self.assertEqual(restarted.get(consumed["approval_id"], OWNER)["status"], CONSUMED)

    def test_database_logs_spans_metrics_and_usage_ledger_exclude_sentinels(self):
        sentinels = (
            "APPROVAL_OWNER_SECRET@example.com",
            "APPROVAL_ADMIN_SECRET_9382",
            "APPROVAL_MCP_TOKEN_SECRET_9382",
            "APPROVAL_AUTH_HEADER_SECRET_9382",
            "APPROVAL_PAYLOAD_SECRET_9382",
        )
        telemetry_path = Path(self.tempdir.name) / "usage.db"
        spans = []
        metrics = []

        @contextmanager
        def record_span(name, attributes=None, **kwargs):
            spans.append((name, dict(attributes or {})))
            yield None

        with (
            patch("app.logic.human_approval.start_span", side_effect=record_span),
            patch("app.logic.human_approval.increment_counter", side_effect=lambda name, attrs: metrics.append((name, dict(attrs)))),
            patch.dict(os.environ, {"USAGE_DB_FILE": str(telemetry_path)}, clear=False),
            self.assertLogs("AllTimeHelper", level=logging.INFO) as captured,
        ):
            record = self.request(
                owner=sentinels[0],
                key="request:telemetry-private",
                payload=sentinels[-1],
            )
            self.store.approve(record["approval_id"], sentinels[0], 1)
            grant = self.store.claim(
                record["approval_id"], sentinels[0], capability_id=CAPABILITY,
                payload_fingerprint=fingerprint(sentinels[-1]), request_key="request:telemetry-private",
            )
            self.store.consume(grant)

        output = "\n".join(captured.output)
        for secret in sentinels:
            self.assertNotIn(secret, output)
        self.assertIn("[ApprovalTrace]", output)
        combined_telemetry = json.dumps({"spans": spans, "metrics": metrics}, sort_keys=True)
        for secret in sentinels:
            self.assertNotIn(secret, combined_telemetry)
        for name, attributes in metrics:
            self.assertIn(name, {
                "helper.approval.requests", "helper.approval.decisions", "helper.approval.expired",
            })
            self.assertNotIn("approval_id", attributes)
            self.assertNotIn("owner", attributes)
            self.assertNotIn("payload_fingerprint", attributes)
            self.assertNotIn("request_key_digest", attributes)
        self.assertIn("helper.approval.request", {name for name, _ in spans})
        self.assertIn("helper.approval.decision", {name for name, _ in spans})
        self.assertIn("helper.approval.claim", {name for name, _ in spans})
        self.assertIn("helper.approval.consume", {name for name, _ in spans})

        files = [Path(str(self.db_file) + suffix) for suffix in ("", "-wal", "-shm")]
        files.append(telemetry_path)
        persisted = b"".join(path.read_bytes() for path in files if path.exists())
        for secret in sentinels:
            self.assertNotIn(secret.encode(), persisted)
        with closing(sqlite3.connect(self.db_file)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(human_approvals)")}
        self.assertNotIn("owner", columns)
        self.assertNotIn("request_key", columns)
        self.assertNotIn("action_payload", columns)


class ApprovalRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.store = SQLiteHumanApprovalStore(Path(self.tempdir.name) / "workflows.db")
        self.record = self.store.request(
            owner=OWNER,
            capability_id=CAPABILITY,
            source=CapabilitySource.AGENT,
            request_key="request:http",
            payload_fingerprint=fingerprint("http-payload"),
            manifest=MANIFEST,
            decision=decision_for(OWNER),
        )
        from app.routes import approvals
        self.approvals = approvals
        self.app = FastAPI()
        self.app.add_middleware(approvals.PrivateApprovalResponsesMiddleware)
        self.app.include_router(approvals.router)

    async def request_as(self, owner, method, path, *, json_body=None):
        if owner is None:
            self.app.dependency_overrides.clear()
        else:
            self.app.dependency_overrides[self.approvals.get_current_user] = lambda: owner
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            with patch.object(self.approvals, "human_approval_store", self.store):
                return await client.request(method, path, json=json_body)

    async def test_routes_are_owner_scoped_private_and_sanitized(self):
        listed = await self.request_as(OWNER, "GET", "/approvals")
        detail = await self.request_as(OWNER, "GET", f"/approvals/{self.record['approval_id']}")
        hidden = await self.request_as(OTHER, "GET", f"/approvals/{self.record['approval_id']}")
        hidden_approve = await self.request_as(
            OTHER, "POST", f"/approvals/{self.record['approval_id']}/approve", json_body={"expected_version": 1},
        )
        hidden_reject = await self.request_as(
            OTHER, "POST", f"/approvals/{self.record['approval_id']}/reject", json_body={"expected_version": 1},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden_approve.status_code, 404)
        self.assertEqual(hidden_reject.status_code, 404)
        for response in (listed, detail, hidden, hidden_approve, hidden_reject):
            self.assertEqual(response.headers["cache-control"], "private, no-store")
        payload = detail.json()
        self.assertEqual(payload["status"], PENDING)
        for forbidden in (
            "owner_scope", "owner", "payload_fingerprint", "request_key_digest", "claim_id",
            "workflow_id", "workflow_action_id", "job_id", "policy_version", "source",
        ):
            self.assertNotIn(forbidden, payload)

    async def test_decision_only_changes_state_no_public_create_and_all_errors_no_store(self):
        approved = await self.request_as(
            OWNER, "POST", f"/approvals/{self.record['approval_id']}/approve", json_body={"expected_version": 1},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], APPROVED)
        self.assertEqual(self.store.get(self.record["approval_id"], OWNER)["status"], APPROVED)
        stale = await self.request_as(
            OWNER, "POST", f"/approvals/{self.record['approval_id']}/reject", json_body={"expected_version": 1},
        )
        no_create = await self.request_as(OWNER, "POST", "/approvals", json_body={})
        invalid_body = await self.request_as(
            OWNER, "POST", f"/approvals/{self.record['approval_id']}/approve",
            json_body={"expected_version": 2, "approval_id": self.record["approval_id"]},
        )
        unauthorized = await self.request_as(None, "GET", "/approvals")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(no_create.status_code, 405)
        self.assertEqual(invalid_body.status_code, 422)
        self.assertEqual(unauthorized.status_code, 401)
        for response in (approved, stale, no_create, invalid_body, unauthorized):
            self.assertEqual(response.headers["cache-control"], "private, no-store")
        paths = {(route.path, method) for route in self.app.routes for method in getattr(route, "methods", set())}
        self.assertNotIn(("/approvals", "POST"), paths)


class WorkflowApprovalSeamTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.db_file = Path(self.tempdir.name) / "workflows.db"

    def test_pause_approval_and_outbox_boundaries_share_one_sqlite_transaction(self):
        from app.logic.capability_policy import WORKFLOW_ACTION_CAPABILITY_IDS
        from app.logic.workflow_store import (
            PAUSED,
            PENDING_ACTION,
            RUNNING,
            SQLiteWorkflowStore,
            WORKFLOW_SCHEMA_VERSION,
        )

        store = SQLiteWorkflowStore(self.db_file)
        owner = "workflow-owner@example.test"
        workflow_id = str(uuid4())
        action_id = "future_mutation"
        execution_id = "workflow-execution"
        mapping = dict(WORKFLOW_ACTION_CAPABILITY_IDS)
        mapping["future_mutation"] = CAPABILITY
        with patch("app.logic.capability_policy.WORKFLOW_ACTION_CAPABILITY_IDS", mapping):
            record = {
                "schema_version": WORKFLOW_SCHEMA_VERSION,
                "workflow_id": workflow_id,
                "owner": owner,
                "intent": "future_mutation",
                "approval_state": "not_required",
                "created_at": time.time(),
                "expires_at": time.time() + 600,
                "topic": "test",
                "actions": [{
                    "id": action_id,
                    "action_type": "future_mutation",
                    "depends_on": [],
                    "optional_depends_on": [],
                    "can_run_parallel": False,
                    "sensitive": False,
                    "terminal": True,
                }],
            }
            self.assertTrue(store.create(record))
            self.assertTrue(store.claim(workflow_id, owner, execution_id))
            state = store.policy_state(workflow_id, owner, execution_id)
            decision = CAPABILITY_POLICY.evaluate(CAPABILITY, CapabilityContext(
                owner=owner,
                source=CapabilitySource.WORKFLOW,
                workflow_id=workflow_id,
                workflow_status=state["workflow_status"],
                workflow_lease_valid=state["workflow_lease_valid"],
            ))
            self.assertEqual(decision.decision, PolicyDecisionType.REQUIRE_APPROVAL)
            approval = store.pause_for_human_approval(
                workflow_id, owner, action_id, execution_id,
                capability_id=CAPABILITY,
                payload_fingerprint=fingerprint("one exact action"),
                manifest=MANIFEST,
                decision=decision,
                record=record,
            )
            self.assertEqual(approval["status"], PENDING)
            snapshot = store.get(workflow_id, owner)
            self.assertEqual(snapshot["status"], PAUSED)
            self.assertTrue(snapshot["approval_required"])
            self.assertIsNone(snapshot["approval"])

            human_store = SQLiteHumanApprovalStore(self.db_file)
            human_store.approve(approval["approval_id"], owner, approval["version"])
            now = time.time()
            with store._open() as db:
                db.execute("BEGIN IMMEDIATE")
                grant = human_store._claim_locked(
                    db, approval["approval_id"], owner,
                    capability_id=CAPABILITY,
                    payload_fingerprint=fingerprint("one exact action"),
                    request_key=f"workflow:{workflow_id}:{action_id}",
                    now=now,
                )
                outbox, _created = store.action_outbox._prepare_locked(
                    db,
                    owner=owner,
                    capability_id=CAPABILITY,
                    source="workflow",
                    idempotency_key=f"workflow:{workflow_id}:{action_id}",
                    payload_fingerprint=fingerprint("one exact action"),
                    workflow_id=workflow_id,
                    workflow_action_id=action_id,
                    now=now,
                )
                claimed, _ = store.action_outbox._claim_locked(
                    db,
                    outbox_id=outbox["outbox_id"],
                    owner=owner,
                    execution_id="dispatch-execution",
                    now=now,
                )
                self.assertTrue(claimed)
                db.rollback()
            self.assertEqual(human_store.get(approval["approval_id"], owner)["status"], APPROVED)
            self.assertIsNone(store.action_outbox.get(outbox["outbox_id"], owner))

            with store._open() as db:
                db.execute("BEGIN IMMEDIATE")
                grant = human_store._claim_locked(
                    db, approval["approval_id"], owner,
                    capability_id=CAPABILITY,
                    payload_fingerprint=fingerprint("one exact action"),
                    request_key=f"workflow:{workflow_id}:{action_id}",
                    now=now,
                )
                outbox, _created = store.action_outbox._prepare_locked(
                    db,
                    owner=owner,
                    capability_id=CAPABILITY,
                    source="workflow",
                    idempotency_key=f"workflow:{workflow_id}:{action_id}",
                    payload_fingerprint=fingerprint("one exact action"),
                    workflow_id=workflow_id,
                    workflow_action_id=action_id,
                    now=now,
                )
                claimed, _ = store.action_outbox._claim_locked(
                    db,
                    outbox_id=outbox["outbox_id"],
                    owner=owner,
                    execution_id="dispatch-execution",
                    now=now,
                )
                self.assertTrue(claimed)
                db.commit()
            self.assertEqual(human_store.get(approval["approval_id"], owner)["status"], CLAIMED)
            self.assertEqual(store.action_outbox.get(outbox["outbox_id"], owner)["state"], OUTBOX_CLAIMED)

            with store._open() as db:
                db.execute("BEGIN IMMEDIATE")
                self.assertTrue(human_store._consume_locked(db, grant, now))
                started, _ = store.action_outbox._mark_dispatch_started_locked(
                    db,
                    outbox_id=outbox["outbox_id"],
                    owner=owner,
                    execution_id="dispatch-execution",
                    now=now,
                )
                self.assertTrue(started)
                db.rollback()
            self.assertEqual(human_store.get(approval["approval_id"], owner)["status"], CLAIMED)
            self.assertEqual(store.action_outbox.get(outbox["outbox_id"], owner)["state"], OUTBOX_CLAIMED)

            with store._open() as db:
                db.execute("BEGIN IMMEDIATE")
                self.assertTrue(human_store._consume_locked(db, grant, now))
                started, _ = store.action_outbox._mark_dispatch_started_locked(
                    db,
                    outbox_id=outbox["outbox_id"],
                    owner=owner,
                    execution_id="dispatch-execution",
                    now=now,
                )
                self.assertTrue(started)
                db.commit()
            self.assertEqual(human_store.get(approval["approval_id"], owner)["status"], CONSUMED)
            self.assertEqual(store.action_outbox.get(outbox["outbox_id"], owner)["state"], DISPATCHING)

    def test_workflow_cancel_cancels_linked_unconsumed_approval(self):
        from app.logic.workflow_store import PAUSED, PENDING_ACTION, SQLiteWorkflowStore, WORKFLOW_SCHEMA_VERSION

        store = SQLiteWorkflowStore(self.db_file)
        owner = "cancel-owner@example.test"
        workflow_id = str(uuid4())
        action_id = "future_mutation"
        from app.logic.capability_policy import WORKFLOW_ACTION_CAPABILITY_IDS
        patched_mapping = dict(WORKFLOW_ACTION_CAPABILITY_IDS)
        patched_mapping[action_id] = CAPABILITY
        with patch("app.logic.capability_policy.WORKFLOW_ACTION_CAPABILITY_IDS", patched_mapping):
            record = {
                "schema_version": WORKFLOW_SCHEMA_VERSION,
                "workflow_id": workflow_id,
                "owner": owner,
                "intent": "future_mutation",
                "approval_state": "not_required",
                "created_at": time.time(),
                "expires_at": time.time() + 600,
                "topic": "test",
                "actions": [{
                    "id": action_id, "action_type": action_id, "depends_on": [],
                    "optional_depends_on": [], "can_run_parallel": False,
                    "sensitive": False, "terminal": True,
                }],
            }
            store.create(record)
            store.claim(workflow_id, owner, "execution")
            state = store.policy_state(workflow_id, owner, "execution")
            decision = CAPABILITY_POLICY.evaluate(CAPABILITY, CapabilityContext(
                owner=owner, source=CapabilitySource.WORKFLOW,
                workflow_status=state["workflow_status"], workflow_lease_valid=True,
            ))
            approval = store.pause_for_human_approval(
                workflow_id, owner, action_id, "execution",
                capability_id=CAPABILITY,
                payload_fingerprint=fingerprint("cancel me"),
                manifest=MANIFEST, decision=decision, record=record,
            )
            self.assertTrue(store.request_cancel(workflow_id, owner))
            self.assertEqual(store.human_approvals.get(approval["approval_id"], owner)["status"], CANCELLED)
            self.assertEqual(store.get(workflow_id, owner)["status"], "cancelled")


class HumanApprovalWorkflowRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.db_file = Path(self.tempdir.name) / "workflows.db"

    def make_plan(self):
        from app.logic.workflow_orchestrator import (
            WorkflowAction,
            WorkflowActionType,
            WorkflowIntent,
            WorkflowPlan,
        )

        return WorkflowPlan(
            owner=OWNER,
            intent=WorkflowIntent.GENERAL_RESPONSE,
            actions=[WorkflowAction(
                id="future_mutation",
                action_type=WorkflowActionType.GENERAL_RESPONSE,
                arguments={"message": "This must not execute before approval."},
                terminal=True,
            )],
            expires_at=time.time() + 600,
        )

    def patched_action_capability(self):
        from app.logic.capability_policy import WORKFLOW_ACTION_CAPABILITY_IDS

        mapping = dict(WORKFLOW_ACTION_CAPABILITY_IDS)
        mapping["general_response"] = CAPABILITY
        return mapping

    def test_explicit_confirmation_pauses_with_internal_approval_not_admin_key(self):
        from app.logic.capability_policy import WORKFLOW_ACTION_CAPABILITY_IDS
        from app.logic.workflow_orchestrator import PendingWorkflowStore, WorkflowExecutor
        from app.logic.workflow_store import PAUSED, PAUSED_ACTION, SQLiteWorkflowStore

        class FutureActionBuilder:
            def build(self, *, action, plan, results, draft):
                return ApprovalRequestMaterial(
                    payload_fingerprint=canonical_action_fingerprint({
                        "server_id": "test-server",
                        "tool_alias": "test_write",
                        "arguments": {"message": action.arguments["message"]},
                    }),
                    manifest=MANIFEST,
                )

        backend = SQLiteWorkflowStore(self.db_file)
        facade = PendingWorkflowStore(ttl_seconds=60, backend=backend)
        plan = self.make_plan()
        expected_fingerprint = canonical_action_fingerprint({
            "server_id": "test-server",
            "tool_alias": "test_write",
            "arguments": {"message": plan.actions[0].arguments["message"]},
        })
        executor = WorkflowExecutor(
            pending_store=facade,
            human_approval_builders={"general_response": FutureActionBuilder()},
        )
        with patch(
            "app.logic.capability_policy.WORKFLOW_ACTION_CAPABILITY_IDS",
            self.patched_action_capability(),
        ):
            result = executor.execute(plan)

        self.assertTrue(result.paused)
        self.assertNotIn("Admin Key", result.message)
        self.assertIn("confirmation", result.message)
        approval_rows = backend.human_approvals.list_for_owner(OWNER)
        self.assertEqual(len(approval_rows), 1)
        self.assertEqual(approval_rows[0]["status"], PENDING)
        self.assertEqual(approval_rows[0]["payload_fingerprint"], expected_fingerprint)
        self.assertEqual(approval_rows[0]["source"], CapabilitySource.WORKFLOW.value)
        snapshot = backend.get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["status"], PAUSED)
        self.assertEqual(snapshot["approval_state"], "human_required")
        self.assertTrue(snapshot["approval_required"])
        self.assertEqual(snapshot["actions"][0]["state"], PAUSED_ACTION)
        with closing(sqlite3.connect(self.db_file)) as db:
            outbox_count = db.execute("SELECT COUNT(*) FROM external_action_outbox").fetchone()[0]
        self.assertEqual(outbox_count, 0)

    def test_explicit_confirmation_without_reviewed_builder_fails_closed(self):
        from app.logic.workflow_orchestrator import PendingWorkflowStore, WorkflowExecutor
        from app.logic.workflow_store import BLOCKED_ACTION, SQLiteWorkflowStore

        backend = SQLiteWorkflowStore(self.db_file)
        facade = PendingWorkflowStore(ttl_seconds=60, backend=backend)
        plan = self.make_plan()
        with patch(
            "app.logic.capability_policy.WORKFLOW_ACTION_CAPABILITY_IDS",
            self.patched_action_capability(),
        ):
            result = WorkflowExecutor(pending_store=facade).execute(plan)

        self.assertFalse(result.paused)
        self.assertNotIn("Admin Key", result.message)
        self.assertEqual(backend.human_approvals.aggregate_counts()[PENDING], 0)
        snapshot = backend.get(plan.workflow_id, OWNER)
        self.assertEqual(snapshot["actions"][0]["state"], BLOCKED_ACTION)
        self.assertEqual(snapshot["actions"][0]["error_category"], "approval_builder_unavailable")


if __name__ == "__main__":
    unittest.main()
