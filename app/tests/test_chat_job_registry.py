import unittest

from app.logic.chat_job_registry import ChatJobRegistry


class ChatJobRegistryTests(unittest.TestCase):
    def test_owner_scoped_events_and_terminal_content(self):
        registry = ChatJobRegistry()
        registry.create("job-1", "owner@example.com")
        registry.publish("job-1", "owner@example.com", {"status": "Working"})
        registry.complete("job-1", "owner@example.com", "completed answer")

        snapshot = registry.snapshot("job-1", "owner@example.com")
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["content"], "completed answer")
        self.assertTrue(any(item["event"].get("done") for item in snapshot["events"]))
        self.assertIsNone(registry.snapshot("job-1", "other@example.com"))

    def test_after_sequence_returns_only_new_events(self):
        registry = ChatJobRegistry()
        registry.create("job-2", "owner@example.com")
        registry.publish("job-2", "owner@example.com", {"status": "One"})
        first = registry.snapshot("job-2", "owner@example.com")
        registry.publish("job-2", "owner@example.com", {"status": "Two"})
        second = registry.snapshot("job-2", "owner@example.com", after=first["next_seq"])

        self.assertEqual([item["event"]["status"] for item in second["events"]], ["Two"])

    def test_failure_is_safe_and_terminal(self):
        registry = ChatJobRegistry()
        registry.create("job-3", "owner@example.com")
        registry.fail("job-3", "owner@example.com", "safe failure")

        snapshot = registry.snapshot("job-3", "owner@example.com")
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["content"], "safe failure")
        self.assertEqual(snapshot["events"][-2]["event"]["message"]["content"], "safe failure")


if __name__ == "__main__":
    unittest.main()