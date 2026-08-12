import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.logic.action_outbox import (
    ACTION_OUTBOX_SCHEMA_VERSION,
    CANCELLED,
    CLAIMED,
    DISPATCHING,
    PREPARED,
    SUCCEEDED,
    UNKNOWN_EXTERNAL_RESULT,
    ActionOutboxCapacityError,
    ActionOutboxConflict,
    ActionOutboxError,
    SQLiteActionOutboxStore,
)
from app.logic.capability_policy import CapabilityContext, CapabilitySource
from app.services.email_delivery_service import (
    EmailDeliveryUnavailable,
    EmailDeliveryService,
    EmailIdempotencyConflict,
    email_payload_fingerprint,
)
from app.contracts.email_draft import normalize_email_draft


OWNER = "outbox-owner@example.com"
DRAFT = {
    "recipient": "recipient@example.com",
    "subject": "Transactional dispatch",
    "body": "Private body sentinel-5a19f7",
}
FP_A = "a" * 64
FP_B = "b" * 64


def http_context() -> CapabilityContext:
    return CapabilityContext(
        owner=OWNER,
        source=CapabilitySource.HTTP,
        request_authorization_verified=True,
    )


class ActionOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.addCleanup(self.tempdir.cleanup)
        self.db_file = Path(self.tempdir.name) / "workflows.db"

    def store(self, **overrides) -> SQLiteActionOutboxStore:
        options = {
            "retention_seconds": 60,
            "lease_seconds": 0.2,
            "lease_renew_seconds": 0.05,
            "max_retained": 50,
            "max_storage_bytes": 1_000_000,
        }
        options.update(overrides)
        return SQLiteActionOutboxStore(self.db_file, **options)

    def prepare(self, store, *, key="request-1", fingerprint=FP_A, source="http"):
        return store.prepare(
            owner=OWNER,
            capability_id="email.deliver",
            source=source,
            idempotency_key=key,
            payload_fingerprint=fingerprint,
        )

    def test_prepare_is_idempotent_and_conflicting_payload_is_rejected(self):
        store = self.store()
        first = self.prepare(store)
        duplicate = self.prepare(store)

        self.assertEqual(first["outbox_id"], duplicate["outbox_id"])
        with self.assertRaises(ActionOutboxConflict):
            self.prepare(store, fingerprint=FP_B)
        with closing(sqlite3.connect(self.db_file)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM external_action_outbox").fetchone()[0], 1)

    def test_only_registered_external_mutations_can_be_prepared(self):
        store = self.store()
        for capability in ("dangerous.custom.action", "web.search", "memory.write"):
            with self.subTest(capability=capability), self.assertRaises(ActionOutboxError):
                store.prepare(
                    owner=OWNER,
                    capability_id=capability,
                    source="http",
                    idempotency_key="not-allowed",
                    payload_fingerprint=FP_A,
                )

    def test_four_stores_initialize_one_schema_concurrently(self):
        barrier = threading.Barrier(4)

        def initialize():
            barrier.wait()
            return self.store().db_file

        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = [future.result() for future in [pool.submit(initialize) for _ in range(4)]]
        self.assertEqual(paths, [self.db_file] * 4)
        with closing(sqlite3.connect(self.db_file)) as db:
            self.assertEqual(
                db.execute("SELECT version FROM external_action_schema WHERE id=1").fetchone()[0],
                ACTION_OUTBOX_SCHEMA_VERSION,
            )

    def test_two_independent_store_claimants_have_one_winner(self):
        first = self.store()
        second = self.store()
        record = self.prepare(first)
        barrier = threading.Barrier(2)

        def claim(store, execution):
            barrier.wait()
            return store.claim(record["outbox_id"], OWNER, execution)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [
                future.result()
                for future in (
                    pool.submit(claim, first, "worker-a"),
                    pool.submit(claim, second, "worker-b"),
                )
            ]
        self.assertEqual(sorted(outcomes), [False, True])

    def test_claimed_crash_before_dispatch_is_reclaimable(self):
        store = self.store()
        record = self.prepare(store)
        self.assertTrue(store.claim(record["outbox_id"], OWNER, "stale-worker"))
        time.sleep(0.25)
        store.recover_expired()
        self.assertEqual(store.get(record["outbox_id"], OWNER)["state"], PREPARED)
        self.assertTrue(store.claim(record["outbox_id"], OWNER, "new-worker"))

    def test_dispatching_crash_becomes_unknown_and_is_not_reclaimable(self):
        store = self.store()
        record = self.prepare(store)
        self.assertTrue(store.claim(record["outbox_id"], OWNER, "stale-worker"))
        self.assertTrue(store.mark_dispatch_started(record["outbox_id"], OWNER, "stale-worker"))
        time.sleep(0.25)
        store.recover_expired()
        recovered = store.get(record["outbox_id"], OWNER)
        self.assertEqual(recovered["state"], UNKNOWN_EXTERNAL_RESULT)
        self.assertFalse(store.claim(record["outbox_id"], OWNER, "new-worker"))

    def test_stale_worker_cannot_commit_success_after_reclaim(self):
        store = self.store()
        record = self.prepare(store)
        self.assertTrue(store.claim(record["outbox_id"], OWNER, "worker-a"))
        time.sleep(0.25)
        store.recover_expired()
        self.assertTrue(store.claim(record["outbox_id"], OWNER, "worker-b"))
        self.assertTrue(store.mark_dispatch_started(record["outbox_id"], OWNER, "worker-b"))
        self.assertFalse(store.mark_succeeded(record["outbox_id"], OWNER, "worker-a"))
        self.assertTrue(store.mark_succeeded(record["outbox_id"], OWNER, "worker-b"))
        self.assertEqual(store.get(record["outbox_id"], OWNER)["state"], SUCCEEDED)

    def test_cancellation_respects_dispatch_boundary_and_success(self):
        store = self.store()
        prepared = self.prepare(store, key="prepared")
        self.assertTrue(store.cancel(prepared["outbox_id"], OWNER))
        self.assertEqual(store.get(prepared["outbox_id"], OWNER)["state"], CANCELLED)

        claimed = self.prepare(store, key="claimed")
        self.assertTrue(store.claim(claimed["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.cancel(claimed["outbox_id"], OWNER))
        self.assertEqual(store.get(claimed["outbox_id"], OWNER)["state"], CANCELLED)

        dispatching = self.prepare(store, key="dispatching")
        self.assertTrue(store.claim(dispatching["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.mark_dispatch_started(dispatching["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.cancel(dispatching["outbox_id"], OWNER))
        self.assertEqual(store.get(dispatching["outbox_id"], OWNER)["state"], UNKNOWN_EXTERNAL_RESULT)

        succeeded = self.prepare(store, key="succeeded")
        self.assertTrue(store.claim(succeeded["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.mark_dispatch_started(succeeded["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.mark_succeeded(succeeded["outbox_id"], OWNER, "worker"))
        self.assertFalse(store.cancel(succeeded["outbox_id"], OWNER))
        self.assertEqual(store.get(succeeded["outbox_id"], OWNER)["state"], SUCCEEDED)

    def test_cancel_racing_claim_always_prevents_dispatch(self):
        first = self.store()
        second = self.store()
        record = self.prepare(first, key="cancel-race")
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait(timeout=3)
            return first.claim(record["outbox_id"], OWNER, "worker")

        def cancel():
            barrier.wait(timeout=3)
            return second.cancel(record["outbox_id"], OWNER)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim), pool.submit(cancel)]
            [future.result(timeout=8) for future in futures]
        self.assertEqual(first.get(record["outbox_id"], OWNER)["state"], CANCELLED)
        self.assertFalse(first.mark_dispatch_started(record["outbox_id"], OWNER, "worker"))

    def test_capacity_never_prunes_active_or_unknown_records(self):
        store = self.store(max_storage_bytes=1200)
        active = self.prepare(store, key="active")
        with self.assertRaises(ActionOutboxCapacityError):
            self.prepare(store, key="overflow")
        self.assertEqual(store.get(active["outbox_id"], OWNER)["state"], PREPARED)

        self.assertTrue(store.claim(active["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.mark_dispatch_started(active["outbox_id"], OWNER, "worker"))
        self.assertTrue(store.mark_unknown(
            active["outbox_id"], OWNER, "worker", error_category="transport_ambiguous",
        ))
        with self.assertRaises(ActionOutboxCapacityError):
            self.prepare(store, key="still-full")
        self.assertEqual(store.get(active["outbox_id"], OWNER)["state"], UNKNOWN_EXTERNAL_RESULT)
        self.assertLessEqual(store.logical_usage_bytes(), store.max_storage_bytes)

    def test_capacity_rejection_happens_before_sender_invocation(self):
        store = self.store(max_storage_bytes=1200)
        self.prepare(store, key="occupy-capacity")
        calls = []
        service = EmailDeliveryService(
            key_verifier=lambda _key: True,
            sender=lambda **kwargs: calls.append(kwargs) or "SIMULATE SUCCESS",
            outbox_store=store,
        )
        with patch("app.services.email_delivery_service._existing_delivery", return_value=None):
            with self.assertRaises(EmailDeliveryUnavailable):
                service.send_approved_email(
                    draft=DRAFT, owner=OWNER, admin_key="request-only",
                    request_id="capacity-rejected", capability_context=http_context(),
                )
        self.assertEqual(calls, [])

    def test_independent_email_services_dispatch_once_and_conflict_is_controlled(self):
        store_a = self.store()
        store_b = self.store()
        calls = []
        calls_lock = threading.Lock()

        def sender(**kwargs):
            with calls_lock:
                calls.append(kwargs["recipient"])
            time.sleep(0.15)
            return "SIMULATE SUCCESS"

        service_a = EmailDeliveryService(key_verifier=lambda _key: True, sender=sender, outbox_store=store_a)
        service_b = EmailDeliveryService(key_verifier=lambda _key: True, sender=sender, outbox_store=store_b)
        barrier = threading.Barrier(2)

        def send(service):
            barrier.wait()
            return service.send_approved_email(
                draft=DRAFT, owner=OWNER, admin_key="request-only",
                request_id="concurrent-http", capability_context=http_context(),
            )

        with patch("app.services.email_delivery_service._existing_delivery", return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [future.result() for future in (pool.submit(send, service_a), pool.submit(send, service_b))]
            self.assertEqual(len(calls), 1)
            self.assertTrue(any(result.success for result in results))
            with self.assertRaises(EmailIdempotencyConflict):
                service_a.send_approved_email(
                    draft={**DRAFT, "subject": "Changed"}, owner=OWNER,
                    admin_key="request-only", request_id="concurrent-http",
                    capability_context=http_context(),
                )
        self.assertEqual(len(calls), 1)

    def test_success_before_receipt_failure_recovers_as_unknown(self):
        class ReceiptFailureStore(SQLiteActionOutboxStore):
            def mark_succeeded(self, *args, **kwargs):
                raise sqlite3.OperationalError("injected receipt failure")

        store = ReceiptFailureStore(
            self.db_file, retention_seconds=60, lease_seconds=0.2,
            lease_renew_seconds=0.05, max_storage_bytes=1_000_000,
        )
        service = EmailDeliveryService(
            key_verifier=lambda _key: True,
            sender=lambda **_kwargs: "LIVE SUCCESS",
            outbox_store=store,
        )
        with patch("app.services.email_delivery_service._existing_delivery", return_value=None):
            with self.assertRaises(sqlite3.OperationalError):
                service.send_approved_email(
                    draft=DRAFT, owner=OWNER, admin_key="request-only",
                    request_id="receipt-crash", capability_context=http_context(),
                )
        time.sleep(0.25)
        store.recover_expired()
        with closing(sqlite3.connect(self.db_file)) as db:
            state = db.execute(
                "SELECT state FROM external_action_outbox WHERE capability_id='email.deliver'"
            ).fetchone()[0]
        self.assertEqual(state, UNKNOWN_EXTERNAL_RESULT)

    def test_legacy_success_is_adopted_without_sender_call_or_new_legacy_write(self):
        store = self.store()
        calls = []
        service = EmailDeliveryService(
            key_verifier=lambda _key: True,
            sender=lambda **kwargs: calls.append(kwargs) or "LIVE SUCCESS",
            outbox_store=store,
        )
        with (
            patch("app.services.email_delivery_service._existing_delivery", return_value="LIVE SUCCESS"),
            patch("app.services.email_delivery_service._record_delivery") as legacy_write,
        ):
            result = service.send_approved_email(
                draft=DRAFT, owner=OWNER, admin_key="request-only",
                request_id="legacy-success", capability_context=http_context(),
            )
        self.assertTrue(result.success)
        self.assertTrue(result.duplicate)
        self.assertEqual(calls, [])
        legacy_write.assert_not_called()
        self.assertEqual(store.get(result.outbox_id, OWNER)["state"], SUCCEEDED)

    def test_legacy_success_converges_a_claimed_never_dispatched_row(self):
        store = self.store()
        record = self.prepare(store, key="legacy-after-claim", source="workflow")
        self.assertTrue(store.claim(record["outbox_id"], OWNER, "workflow-execution"))
        adopted = store.record_legacy_success(
            owner=OWNER,
            capability_id="email.deliver",
            source="workflow",
            idempotency_key="legacy-after-claim",
            payload_fingerprint=FP_A,
        )
        self.assertEqual(adopted["state"], SUCCEEDED)
        self.assertIsNone(adopted["execution_id"])

    def test_outbox_persists_fingerprints_not_payload_or_credentials(self):
        store = self.store()
        draft = normalize_email_draft(DRAFT)
        self.assertEqual(len(email_payload_fingerprint(draft)), 64)
        service = EmailDeliveryService(
            key_verifier=lambda _key: True,
            sender=lambda **_kwargs: "SIMULATE SUCCESS",
            outbox_store=store,
        )
        with patch("app.services.email_delivery_service._existing_delivery", return_value=None):
            service.send_approved_email(
                draft=draft, owner=OWNER, admin_key="admin-secret-sentinel",
                request_id="privacy", capability_context=http_context(),
            )
        raw = self.db_file.read_bytes()
        for sentinel in (
            OWNER.encode(), b"recipient@example.com", b"Transactional dispatch",
            b"Private body sentinel-5a19f7", b"admin-secret-sentinel",
        ):
            self.assertNotIn(sentinel, raw)


if __name__ == "__main__":
    unittest.main()
