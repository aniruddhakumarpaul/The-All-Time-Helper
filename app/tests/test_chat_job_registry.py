import tempfile
import time
import unittest
import uuid
from pathlib import Path

from app.logic.chat_job_registry import ChatJobRegistry, InMemoryChatJobStore, SQLiteChatJobStore


class ChatJobRegistryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()