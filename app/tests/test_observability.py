import asyncio
import logging
import threading
import unittest
from types import SimpleNamespace


class ObservabilityTests(unittest.TestCase):
    def test_agent_step_logging_does_not_emit_model_content(self):
        from app.logger import log_agent_step, logger

        secret = "recipient@example.test SECRET-PROMPT-123"
        step = SimpleNamespace(
            agent=secret,
            thought=secret,
            tool=secret,
        )
        with self.assertLogs(logger, level=logging.INFO) as captured:
            log_agent_step(step)

        output = "\n".join(captured.output)
        self.assertIn("[AgentTrace]", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("THOUGHT", output)

    def test_queue_trace_excludes_job_output_and_owner(self):
        from app.inference_queue import InferenceQueue

        secret = "recipient@example.test SECRET-RESPONSE-456"

        async def run():
            queue = InferenceQueue(max_workers=1, max_queue_depth=1, fast_workers=1, fast_queue_depth=1)
            try:
                with self.assertLogs("AllTimeHelper", level=logging.INFO) as captured:
                    result = await queue.submit(
                        "trace-test",
                        lambda: secret,
                        threading.Event(),
                        timeout=2,
                        owner="owner-secret@example.test",
                        lane="inference",
                    )
                self.assertEqual(result, secret)
                output = "\n".join(captured.output)
                self.assertIn("[JobTrace]", output)
                self.assertNotIn(secret, output)
                self.assertNotIn("owner-secret@example.test", output)
            finally:
                await queue.shutdown()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
