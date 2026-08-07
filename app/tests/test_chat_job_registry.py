import json
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from app.logic.chat_job_registry import (
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
            store = SQLiteChatJobStore(db_file, retention_seconds=10, max_storage_bytes=1024, max_event_storage_bytes=10_000)
            job_id = str(uuid.uuid4())
            store.create(job_id, "owner@example.com")
            content = "🙂" * 300
            store.publish(job_id, "owner@example.com", {"content": content})
            with store._open() as db:
                row = db.execute("SELECT event_storage_bytes,content_bytes FROM chat_jobs WHERE job_id=?", (job_id,)).fetchone()
                self.assertEqual(row["event_storage_bytes"], len(json.dumps({"content": content}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
            with self.assertRaises(ChatJobCapacityError):
                store.create(str(uuid.uuid4()), "owner@example.com")


if __name__ == "__main__":
    unittest.main()
