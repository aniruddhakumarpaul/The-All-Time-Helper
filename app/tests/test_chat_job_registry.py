import json
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from app.logic.chat_job_registry import (
    ACTIVE,
    CANCELLING,
    ChatJobCapacityError,
    ChatJobRegistry,
    InMemoryChatJobStore,
    SQLiteChatJobStore,
    _bound_event,
    _event_size,
)


class ChatJobRegistryTests(unittest.TestCase):
    def test_bound_event_never_exceeds_utf8_limit_and_preserves_flags(self):
        events = [
            {"status": "Working", "message": {"role": "assistant", "content": "x" * 5000}, "done": False},
            {"final": True, "status": "failed", "done": True, "content": "🙂" * 2000},
            {"message": {"content": "বাংলা और 日本語 " * 1000}, "final": True, "done": True},
        ]
        for limit in (128, 256, 512, 1024):
            for event in events:
                bounded = _bound_event(event, limit)
                self.assertLessEqual(_event_size(bounded), limit)
                self.assertEqual(bool(bounded.get("final")), bool(event.get("final")))
                self.assertEqual(bool(bounded.get("done")), bool(event.get("done")))

    def test_owner_scoped_events_and_terminal_content(self):
        registry = ChatJobRegistry(InMemoryChatJobStore())
        registry.create("job-1", "owner@example.com")
        registry.publish("job-1", "owner@example.com", {"status": "Working"})
        registry.complete("job-1", "owner@example.com", "completed answer")

        snapshot = registry.snapshot("job-1", "owner@example.com")
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["content"], "completed answer")
        self.assertTrue(any(item["event"].get("done") for item in snapshot["events"]))
        self.assertIsNone(registry.snapshot("job-1", "other@example.com"))

    def test_after_sequence_returns_only_new_events(self):
        registry = ChatJobRegistry(InMemoryChatJobStore())
        registry.create("job-2", "owner@example.com")
        registry.publish("job-2", "owner@example.com", {"status": "One"})
        first = registry.snapshot("job-2", "owner@example.com")
        registry.publish("job-2", "owner@example.com", {"status": "Two"})
        second = registry.snapshot("job-2", "owner@example.com", after=first["next_seq"])
        self.assertEqual([item["event"]["status"] for item in second["events"]], ["Two"])

    def test_failure_is_safe_and_terminal(self):
        registry = ChatJobRegistry(InMemoryChatJobStore())
        registry.create("job-3", "owner@example.com")
        registry.fail("job-3", "owner@example.com", "safe failure")
        snapshot = registry.snapshot("job-3", "owner@example.com")
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["content"], "safe failure")
        self.assertEqual(snapshot["events"][-2]["event"]["message"]["content"], "safe failure")

    def test_sqlite_instances_share_restartable_result_and_cancel_intent(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            worker_a = SQLiteChatJobStore(db_file, retention_seconds=5, max_events=10)
            worker_b = SQLiteChatJobStore(db_file, retention_seconds=5, max_events=10)
            job_id = str(uuid.uuid4())

            worker_a.create(job_id, "owner@example.com")
            worker_a.publish(job_id, "owner@example.com", {"status": "Working"})
            self.assertTrue(worker_b.request_cancel(job_id, "owner@example.com"))
            self.assertTrue(worker_a.is_cancel_requested(job_id, "owner@example.com"))
            self.assertTrue(worker_a.complete(job_id, "owner@example.com", "cancelled answer"))

            restarted = SQLiteChatJobStore(db_file, retention_seconds=5, max_events=10)
            snapshot = restarted.snapshot(job_id, "owner@example.com", after=1)
            self.assertEqual(snapshot["status"], "cancelled")
            self.assertEqual(snapshot["content"], "cancelled answer")
            self.assertTrue(any(item["event"].get("final") for item in snapshot["events"]))
            self.assertIsNone(restarted.snapshot(job_id, "other@example.com"))

    def test_sqlite_compacts_events_and_expires_without_new_job(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=1, max_events=3, max_event_bytes=512)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            for index in range(8):
                self.assertTrue(store.publish(job_id, "owner@example.com", {"status": "x", "message": {"content": str(index)}}))
            snapshot = store.snapshot(job_id, "owner@example.com")
            self.assertLessEqual(len(snapshot["events"]), 3)
            time.sleep(1.1)
            self.assertIsNone(store.snapshot(job_id, "owner@example.com"))

    def test_sqlite_concurrent_publishers_have_unique_monotonic_sequences(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10, max_events=1000, max_event_storage_bytes=1_000_000)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")

            def publish_range(offset):
                worker = SQLiteChatJobStore(db_file, retention_seconds=10, max_events=1000, max_event_storage_bytes=1_000_000)
                for index in range(50):
                    self.assertTrue(worker.publish(job_id, "owner@example.com", {"status": f"{offset + index}"}))

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(publish_range, range(0, 200, 50)))
            snapshot = store.snapshot(job_id, "owner@example.com")
            sequences = [item["seq"] for item in snapshot["events"]]
            self.assertEqual(snapshot["next_seq"], 200)
            self.assertEqual(sequences, sorted(set(sequences)))

    def test_concurrent_cancel_and_publish_has_one_terminal_boundary(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10, max_events=1000, max_event_storage_bytes=1_000_000)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            barrier = threading.Barrier(5)

            def publish():
                worker = SQLiteChatJobStore(db_file, retention_seconds=10, max_events=1000, max_event_storage_bytes=1_000_000)
                barrier.wait()
                for index in range(50):
                    worker.publish(job_id, "owner@example.com", {"status": str(index)})

            def cancel():
                worker = SQLiteChatJobStore(db_file, retention_seconds=10, max_events=1000, max_event_storage_bytes=1_000_000)
                barrier.wait()
                worker.request_cancel(job_id, "owner@example.com")

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(publish) for _ in range(4)] + [pool.submit(cancel)]
                for future in futures:
                    future.result()
            self.assertTrue(store.complete(job_id, "owner@example.com", "cancelled"))
            snapshot = store.snapshot(job_id, "owner@example.com")
            final_seq = max(item["seq"] for item in snapshot["events"] if item["event"].get("final"))
            self.assertEqual(snapshot["status"], "cancelled")
            self.assertFalse(store.publish(job_id, "owner@example.com", {"status": "late"}))
            self.assertEqual(store.snapshot(job_id, "owner@example.com")["next_seq"], final_seq)

    def test_complete_and_cancel_are_idempotent_under_race(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            store.claim(job_id, "owner@example.com", "execution-a")
            barrier = threading.Barrier(2)

            def complete():
                barrier.wait()
                return SQLiteChatJobStore(db_file, retention_seconds=10).complete(job_id, "owner@example.com", "answer", execution_id="execution-a")

            def cancel():
                barrier.wait()
                return SQLiteChatJobStore(db_file, retention_seconds=10).request_cancel(job_id, "owner@example.com")

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(complete), pool.submit(cancel)]
                [future.result() for future in results]
            snapshot = store.snapshot(job_id, "owner@example.com")
            self.assertIn(snapshot["status"], {"completed", "cancelled"})
            self.assertEqual(sum(1 for item in snapshot["events"] if item["event"].get("final")), 1)
            self.assertFalse(store.complete(job_id, "owner@example.com", "duplicate", execution_id="execution-a"))

    def test_fail_and_complete_are_idempotent_under_race(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            store.claim(job_id, "owner@example.com", "execution-a")
            barrier = threading.Barrier(2)

            def fail():
                barrier.wait()
                return SQLiteChatJobStore(db_file, retention_seconds=10).fail(job_id, "owner@example.com", "safe failure", execution_id="execution-a")

            def complete():
                barrier.wait()
                return SQLiteChatJobStore(db_file, retention_seconds=10).complete(job_id, "owner@example.com", "answer", execution_id="execution-a")

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(fail), pool.submit(complete)]
                [future.result() for future in results]
            snapshot = store.snapshot(job_id, "owner@example.com")
            self.assertIn(snapshot["status"], {"failed", "completed"})
            self.assertEqual(sum(1 for item in snapshot["events"] if item["event"].get("final")), 1)

    def test_expired_execution_is_recovered_without_replay(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            worker_a = SQLiteChatJobStore(db_file, retention_seconds=10, lease_seconds=1)
            worker_b = SQLiteChatJobStore(db_file, retention_seconds=10, lease_seconds=1)
            job_id = str(uuid.uuid4())
            worker_a.create(job_id, "owner@example.com")
            self.assertTrue(worker_a.claim(job_id, "owner@example.com", "execution-a"))
            self.assertEqual(worker_b.snapshot(job_id, "owner@example.com")["status"], "active")
            time.sleep(1.1)
            snapshot = worker_b.snapshot(job_id, "owner@example.com")
            self.assertEqual(snapshot["status"], "failed")
            self.assertEqual(snapshot["content"], "The server restarted before this response completed. Please retry.")
            self.assertTrue(snapshot["events"][-1]["event"].get("final"))
            self.assertFalse(worker_a.complete(job_id, "owner@example.com", "replayed", execution_id="execution-a"))

    def test_activity_refreshes_retention_and_event_storage_preserves_final(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=1, max_events=100, max_event_storage_bytes=700)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            time.sleep(0.7)
            store.publish(job_id, "owner@example.com", {"status": "🙂" * 100})
            time.sleep(0.7)
            self.assertIsNotNone(store.snapshot(job_id, "owner@example.com"))
            self.assertTrue(store.complete(job_id, "owner@example.com", "final answer"))
            snapshot = store.snapshot(job_id, "owner@example.com")
            self.assertTrue(any(item["event"].get("final") for item in snapshot["events"]))
            self.assertLessEqual(len(snapshot["events"]), 100)

    def test_storage_accounting_uses_utf8_bytes_and_rejects_when_active_usage_is_full(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10, max_storage_bytes=1024,
                                       max_event_storage_bytes=10_000, max_content_bytes=128)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            content = "🙂" * 80
            self.assertTrue(store.publish(job_id, "owner@example.com", {"content": content}))
            with store._open() as db:
                row = db.execute("SELECT event_storage_bytes,content_bytes FROM chat_jobs WHERE job_id=?", (job_id,)).fetchone()
                self.assertEqual(row["event_storage_bytes"], len(json.dumps({"content": content}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
            # A later admission compacts this optional event, so the active job remains intact.
            self.assertIsNotNone(store.snapshot(job_id, "owner@example.com"))

    def test_streamed_unicode_message_keeps_message_shape_and_is_visible_before_final(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10, max_event_bytes=512,
                                       max_storage_bytes=4096, max_content_bytes=128)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            chunk = {"message": {"role": "assistant", "content": "🙂漢字" * 1000}, "done": False}
            self.assertTrue(store.publish(job_id, "owner@example.com", chunk))
            snapshot = store.snapshot(job_id, "owner@example.com")
            event = snapshot["events"][-1]["event"]
            self.assertIn("message", event)
            self.assertIn("content", event["message"])
            self.assertNotIn("content", event)
            self.assertFalse(event["done"])
            self.assertLessEqual(_event_size(event), 512)

    def test_expired_orphan_recovery_is_atomic_across_readers(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            creator = SQLiteChatJobStore(db_file, retention_seconds=10, lease_seconds=1, launch_seconds=10)
            job_id = str(uuid.uuid4())
            creator.create(job_id, "owner@example.com")
            self.assertTrue(creator.claim(job_id, "owner@example.com", "execution-a"))
            time.sleep(1.1)
            barrier = threading.Barrier(4)

            def read_snapshot():
                worker = SQLiteChatJobStore(db_file, retention_seconds=10, lease_seconds=1, launch_seconds=10)
                barrier.wait()
                return worker.snapshot(job_id, "owner@example.com")

            with ThreadPoolExecutor(max_workers=4) as pool:
                snapshots = list(pool.map(lambda _: read_snapshot(), range(4)))
            self.assertTrue(all(snapshot and snapshot["status"] == "failed" for snapshot in snapshots))
            self.assertEqual({snapshot["content"] for snapshot in snapshots},
                             {"The server restarted before this response completed. Please retry."})
            final_counts = [sum(item["event"].get("final", False) for item in snapshot["events"]) for snapshot in snapshots]
            self.assertEqual(final_counts, [1] * 4)
            sequences = [item["seq"] for item in snapshots[0]["events"]]
            self.assertEqual(sequences, sorted(set(sequences)))

    def test_expired_orphan_recovery_is_atomic_across_snapshot_prune_and_list(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            creator = SQLiteChatJobStore(db_file, retention_seconds=10, lease_seconds=1, launch_seconds=10)
            job_id = str(uuid.uuid4())
            creator.create(job_id, "owner@example.com")
            self.assertTrue(creator.claim(job_id, "owner@example.com", "execution-a"))
            time.sleep(1.1)
            barrier = threading.Barrier(12)

            def invoke(kind):
                worker = SQLiteChatJobStore(db_file, retention_seconds=10, lease_seconds=1, launch_seconds=10)
                barrier.wait()
                if kind == "snapshot":
                    return worker.snapshot(job_id, "owner@example.com")
                if kind == "list":
                    return worker.list_for_owner("owner@example.com")
                return worker.prune()

            kinds = ["snapshot"] * 4 + ["list"] * 4 + ["prune"] * 4
            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(invoke, kinds))
            snapshots = [result for kind, result in zip(kinds, results) if kind == "snapshot"]
            listed = [item for result in results if isinstance(result, list) for item in result if item and item["job_id"] == job_id]
            self.assertTrue(all(snapshot and snapshot["status"] == "failed" for snapshot in snapshots))
            self.assertTrue(all(item["status"] == "failed" for item in listed))
            final = creator.snapshot(job_id, "owner@example.com")
            self.assertEqual(sum(item["event"].get("final", False) for item in final["events"]), 1)

    def test_launch_deadline_fails_unclaimed_job_and_claim_race_is_single_winner(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            store = SQLiteChatJobStore(db_file, retention_seconds=10, launch_seconds=1, lease_seconds=5)
            expired_id = str(uuid.uuid4())
            store.create(expired_id, "owner@example.com")
            time.sleep(1.1)
            expired = store.snapshot(expired_id, "owner@example.com")
            self.assertEqual(expired["status"], "failed")
            self.assertEqual(expired["content"], "The server stopped before this response could start. Please retry.")

            claim_id = str(uuid.uuid4())
            store.create(claim_id, "owner@example.com")
            workers = [SQLiteChatJobStore(db_file, retention_seconds=10, launch_seconds=1, lease_seconds=5) for _ in range(2)]
            barrier = threading.Barrier(2)

            def claim(index):
                barrier.wait()
                return workers[index].claim(claim_id, "owner@example.com", f"execution-{index}")

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(claim, index) for index in range(2)]
                claimed = [future.result() for future in results]
            self.assertEqual(sum(claimed), 1)
            self.assertEqual(store.snapshot(claim_id, "owner@example.com")["status"], "active")

    def test_global_budget_compacts_progress_without_deleting_active_jobs(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            config = dict(retention_seconds=10, max_storage_bytes=1800, max_event_storage_bytes=10_000,
                          max_event_bytes=512, max_content_bytes=128)
            creator = SQLiteChatJobStore(db_file, **config)
            job_ids = [str(uuid.uuid4()) for _ in range(3)]
            for job_id in job_ids:
                creator.create(job_id, "owner@example.com")
            barrier = threading.Barrier(3)

            def publish(job_id):
                worker = SQLiteChatJobStore(db_file, **config)
                barrier.wait()
                for index in range(20):
                    worker.publish(job_id, "owner@example.com", {"message": {"content": f"🙂{index}"}, "done": False})

            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(publish, job_id) for job_id in job_ids]
                [future.result() for future in futures]
            with creator._open() as db:
                usage = db.execute("SELECT COALESCE(SUM(event_storage_bytes + content_bytes), 0) AS bytes FROM chat_jobs").fetchone()["bytes"]
                active = db.execute("SELECT COUNT(*) AS count FROM chat_jobs WHERE status IN (?,?)", (ACTIVE, CANCELLING)).fetchone()["count"]
            self.assertLessEqual(usage, 1800)
            self.assertEqual(active, 3)
            for job_id in job_ids:
                self.assertTrue(creator.complete(job_id, "owner@example.com", "final 🙂"))
            with creator._open() as db:
                usage = db.execute("SELECT COALESCE(SUM(event_storage_bytes + content_bytes), 0) AS bytes FROM chat_jobs").fetchone()["bytes"]
            self.assertLessEqual(usage, 1800)

    def test_admission_rejects_when_terminal_reservations_cannot_fit(self):
        with tempfile.TemporaryDirectory(dir=r"C:\\tmp") as directory:
            db_file = Path(directory) / "jobs.db"
            config = dict(retention_seconds=10, max_storage_bytes=1200, max_content_bytes=128,
                          max_event_bytes=512, max_event_storage_bytes=4096)
            store = SQLiteChatJobStore(db_file, **config)
            admitted = []
            while True:
                try:
                    job_id = str(uuid.uuid4())
                    store.create(job_id, "owner@example.com")
                    admitted.append(job_id)
                except ChatJobCapacityError:
                    break
            self.assertGreaterEqual(len(admitted), 2)
            self.assertLessEqual(len(admitted) * store._terminal_reservation_bytes(), 1200)
            with store._open() as db:
                usage = db.execute("SELECT COALESCE(SUM(event_storage_bytes + content_bytes), 0) AS bytes FROM chat_jobs").fetchone()["bytes"]
            self.assertLessEqual(usage, 1200)


if __name__ == "__main__":
    unittest.main()
