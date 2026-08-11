import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import Response

from app.logic.usage_ledger import UsageEvent, UsageLedger, pseudonymous_owner_scope


def event(index: int, *, owner: str = "owner@example.test", cost=0.01) -> UsageEvent:
    return UsageEvent(
        event_id=f"event-{index}",
        occurred_at=time.time(),
        owner=owner,
        job_id=f"job-{index}",
        workflow_id=None,
        operation="chat",
        provider="openrouter",
        request_model="openrouter/test",
        response_model="test",
        status="completed",
        input_tokens=10,
        output_tokens=3,
        cost_usd=cost,
        cost_source="litellm" if cost is not None else "unknown",
        duration_ms=25,
        attempt=1,
        source="test",
    )


class UsageLedgerTests(unittest.TestCase):
    def test_owner_is_pseudonymous_and_summary_is_scoped(self):
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            path = Path(tmp) / "usage.db"
            ledger = UsageLedger(path)
            self.assertTrue(ledger.record(event(1)))
            self.assertTrue(ledger.record(event(2, owner="other@example.test", cost=None)))
            summary = ledger.summary("owner@example.test", "24h")
            self.assertEqual(summary["model_calls"], 1)
            self.assertEqual(summary["input_tokens"], 10)
            self.assertEqual(summary["unknown_cost_calls"], 0)
            with closing(sqlite3.connect(path)) as db:
                owner_scope = db.execute("SELECT owner_scope FROM usage_events WHERE event_id='event-1'").fetchone()[0]
                raw = repr(db.execute("SELECT * FROM usage_events").fetchall())
            self.assertEqual(owner_scope, pseudonymous_owner_scope("owner@example.test"))
            self.assertNotIn("owner@example.test", raw)

    def test_event_id_deduplicates_and_unknown_cost_stays_null(self):
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            path = Path(tmp) / "usage.db"
            ledger = UsageLedger(path)
            unknown = event(1, cost=None)
            self.assertTrue(ledger.record(unknown))
            self.assertTrue(ledger.record(unknown))
            self.assertEqual(ledger.count(), 1)
            summary = ledger.summary("owner@example.test")
            self.assertEqual(summary["known_cost_usd"], 0.0)
            self.assertEqual(summary["unknown_cost_calls"], 1)

    def test_retention_and_event_cap_prune_oldest_first(self):
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            ledger = UsageLedger(Path(tmp) / "usage.db", retention_days=1, max_events=2)
            expired = event(0)
            object.__setattr__(expired, "occurred_at", time.time() - 2 * 86400)
            ledger.record(expired)
            ledger.record(event(1))
            ledger.record(event(2))
            ledger.record(event(3))
            self.assertEqual(ledger.count(), 2)
            with self.assertRaises(ValueError):
                ledger.summary("owner@example.test", "1h")

    def test_four_writer_threads_are_lossless(self):
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            ledger = UsageLedger(Path(tmp) / "usage.db", busy_timeout_ms=2000, write_retries=5, max_events=1000)
            failures = []

            def writer(offset: int) -> None:
                for item in range(100):
                    if not ledger.record(event(offset + item)):
                        failures.append(offset + item)

            threads = [threading.Thread(target=writer, args=(index * 1000,)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(failures, [])
            self.assertEqual(ledger.count(), 400)

    def test_locked_database_drops_telemetry_without_raising(self):
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            path = Path(tmp) / "usage.db"
            ledger = UsageLedger(path, busy_timeout_ms=5, write_retries=1)
            lock = sqlite3.connect(path, timeout=0.01, isolation_level=None)
            lock.execute("BEGIN IMMEDIATE")
            try:
                self.assertFalse(ledger.record(event(1)))
            finally:
                lock.rollback()
                lock.close()

    def test_usage_summary_route_is_owner_scoped_and_no_store(self):
        from app.routes.usage import usage_summary

        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            ledger = UsageLedger(Path(tmp) / "usage.db")
            ledger.record(event(1))
            response = Response()
            with patch("app.routes.usage.get_usage_ledger", return_value=ledger):
                payload = asyncio.run(usage_summary(response, "24h", "owner@example.test"))
            self.assertEqual(payload["model_calls"], 1)
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            self.assertNotIn("job_id", payload)


if __name__ == "__main__":
    unittest.main()
