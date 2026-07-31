import asyncio
import threading
import time
import unittest

from app.inference_queue import InferenceQueue


class InferenceQueueReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        queue = getattr(self, "queue", None)
        if queue is not None:
            await queue.shutdown()

    async def wait_for_thread_event(self, event):
        for _ in range(100):
            if event.is_set():
                return
            await asyncio.sleep(0.005)
        self.fail("Worker did not reach the expected state.")

    async def test_lanes_are_independent_and_saturation_is_atomic(self):
        self.queue = InferenceQueue(max_workers=1, max_queue_depth=1, fast_workers=1, fast_queue_depth=1)
        inference_started = threading.Event()
        release_inference = threading.Event()

        def blocked_inference():
            inference_started.set()
            release_inference.wait(2)
            return "inference"

        first = asyncio.create_task(self.queue.submit(
            "inference-1", blocked_inference, threading.Event(), owner="user-1", timeout=1
        ))
        await self.wait_for_thread_event(inference_started)

        tool = asyncio.create_task(self.queue.submit(
            "tool-1", lambda: "tool", threading.Event(), owner="user-1", lane="tool", timeout=1
        ))
        self.assertEqual(await tool, "tool")

        queued = asyncio.create_task(self.queue.submit(
            "inference-2", lambda: "queued", threading.Event(), owner="user-1", timeout=1
        ))
        for _ in range(100):
            if self.queue.inference_queue_depth == 1:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(self.queue.inference_queue_depth, 1)

        with self.assertRaisesRegex(RuntimeError, "queue is full"):
            await self.queue.submit("inference-3", lambda: "overflow", threading.Event(), owner="user-1", timeout=1)
        self.assertNotIn("inference-3", self.queue._active_jobs)

        self.assertTrue(self.queue.cancel("inference-2", "user-1"))
        self.assertEqual(await queued, "Operation cancelled.")
        release_inference.set()
        self.assertEqual(await first, "inference")
        self.assertEqual(self.queue.queue_depth, 0)
        self.assertEqual(self.queue._active_jobs, {})

    async def test_owner_scoped_cancellation_and_duplicate_ids(self):
        self.queue = InferenceQueue(max_workers=1, max_queue_depth=1, fast_workers=1, fast_queue_depth=1)
        started = threading.Event()
        release = threading.Event()

        def blocked():
            started.set()
            release.wait(2)
            return "finished"

        task = asyncio.create_task(self.queue.submit(
            "same-id", blocked, threading.Event(), owner="owner-a", timeout=1
        ))
        await self.wait_for_thread_event(started)
        with self.assertRaisesRegex(RuntimeError, "already active"):
            await self.queue.submit("same-id", lambda: "duplicate", threading.Event(), owner="owner-a", timeout=1)
        self.assertFalse(self.queue.cancel("same-id", "owner-b"))
        self.assertTrue(self.queue.cancel("same-id", "owner-a"))
        self.assertEqual(await task, "Operation cancelled.")
        release.set()

    async def test_timeout_and_worker_exception_are_bounded_and_cleaned(self):
        self.queue = InferenceQueue(max_workers=1, max_queue_depth=1, fast_workers=1, fast_queue_depth=1)
        abort_event = threading.Event()

        def slow():
            time.sleep(0.05)
            return "late"

        result = await self.queue.submit("timeout", slow, abort_event, owner="owner-a", timeout=0.005)
        self.assertIn("Inference Timeout", result)
        self.assertTrue(abort_event.is_set())
        for _ in range(100):
            if not self.queue._active_jobs:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(self.queue._active_jobs, {})

        with self.assertRaisesRegex(ValueError, "worker failure"):
            await self.queue.submit(
                "failure", lambda: (_ for _ in ()).throw(ValueError("worker failure")),
                threading.Event(), owner="owner-a", timeout=1
            )
        self.assertEqual(self.queue._active_jobs, {})

    async def test_invalid_configuration_and_repeated_shutdown_are_safe(self):
        with self.assertRaises(ValueError):
            InferenceQueue(max_workers=0)
        with self.assertRaises(ValueError):
            InferenceQueue(fast_queue_depth=0)

        self.queue = InferenceQueue()
        with self.assertRaises(ValueError):
            await self.queue.submit("bad-timeout", lambda: "bad", threading.Event(), timeout=0)
        await self.queue.shutdown()
        await self.queue.shutdown()


if __name__ == "__main__":
    unittest.main()
