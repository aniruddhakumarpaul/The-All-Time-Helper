import asyncio
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ObservabilityTests(unittest.TestCase):
    def tearDown(self):
        from app.observability import reset_observability_for_tests
        from app.logic.usage_ledger import reset_usage_ledger_for_tests

        reset_observability_for_tests()
        reset_usage_ledger_for_tests()

    def test_initialization_disabled_and_idempotent(self):
        from app.observability import initialize_observability

        with patch.dict(os.environ, {"HELPER_OTEL_ENABLED": "false"}, clear=False):
            first = initialize_observability()
            second = initialize_observability()
        self.assertFalse(first["otel_enabled"])
        self.assertEqual(first, second)

    def test_in_memory_genai_span_metrics_and_privacy(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from app.logic.usage_ledger import reset_usage_ledger_for_tests
        from app.observability import GenAIAttempt, initialize_observability, telemetry_scope

        sentinel = "recipient@example.test SECRET-PROMPT-CONTENT"
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            span_exporter = InMemorySpanExporter()
            metric_reader = InMemoryMetricReader()
            with patch.dict(
                os.environ,
                {
                    "HELPER_OTEL_ENABLED": "true",
                    "HELPER_USAGE_LEDGER_ENABLED": "true",
                    "USAGE_DB_FILE": str(Path(tmp) / "usage.db"),
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "",
                },
                clear=False,
            ):
                reset_usage_ledger_for_tests()
                initialize_observability(span_exporter=span_exporter, metric_reader=metric_reader)
                with telemetry_scope(owner=sentinel, job_id="job-safe"):
                    attempt = GenAIAttempt(model="openrouter/test-model", attempt=1, source="test")
                    attempt.observe_chunk()
                    attempt.finish(SimpleNamespace(
                        model="test-model",
                        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
                        _hidden_params={"response_cost": 0.0012},
                    ))
                    failed = GenAIAttempt(model="openrouter/test-model", attempt=2, source="test")
                    failed.finish(error=RuntimeError(sentinel))

                spans = span_exporter.get_finished_spans()
                self.assertEqual([item.name for item in spans].count("gen_ai.chat"), 2)
                exported = repr([(item.name, dict(item.attributes)) for item in spans])
                self.assertNotIn(sentinel, exported)
                self.assertTrue(all(not item.events for item in spans))
                metrics = metric_reader.get_metrics_data()
                names = {
                    metric.name
                    for resource in metrics.resource_metrics
                    for scope in resource.scope_metrics
                    for metric in scope.metrics
                }
                self.assertIn("gen_ai.client.operation.duration", names)
                self.assertIn("gen_ai.client.token.usage", names)
                self.assertIn("gen_ai.client.operation.time_to_first_chunk", names)

    def test_stream_finalization_is_deduplicated(self):
        from app.logic.usage_ledger import UsageLedger
        from app.observability import GenAIAttempt, _ObservedStream

        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp, patch.dict(
            os.environ,
            {"HELPER_USAGE_LEDGER_ENABLED": "true", "USAGE_DB_FILE": str(Path(tmp) / "usage.db")},
            clear=False,
        ):
            from app.logic import usage_ledger as ledger_module

            ledger_module.reset_usage_ledger_for_tests()
            attempt = GenAIAttempt(model="openrouter/test-model", source="test")
            response = SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
            )
            list(_ObservedStream(iter([response]), attempt))
            attempt.finish(response)
            self.assertEqual(UsageLedger(Path(tmp) / "usage.db").count(), 1)

    def test_queue_worker_propagates_trace_and_application_context(self):
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from app.inference_queue import InferenceQueue
        from app.logic.usage_ledger import reset_usage_ledger_for_tests
        from app.observability import (
            current_telemetry_context,
            initialize_observability,
            start_span,
            telemetry_scope,
        )

        async def run():
            queue = InferenceQueue(max_workers=1, max_queue_depth=1, fast_workers=1, fast_queue_depth=1)
            try:
                with telemetry_scope(owner="owner@example.test", job_id="context-job"):
                    with start_span("helper.chat.execute"):
                        return await queue.submit(
                            "context-job",
                            lambda: current_telemetry_context().job_id,
                            threading.Event(),
                            timeout=2,
                            owner="owner@example.test",
                        )
            finally:
                await queue.shutdown()

        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            exporter = InMemorySpanExporter()
            with patch.dict(
                os.environ,
                {
                    "HELPER_OTEL_ENABLED": "true",
                    "USAGE_DB_FILE": str(Path(tmp) / "usage.db"),
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "",
                },
                clear=False,
            ):
                reset_usage_ledger_for_tests()
                initialize_observability(span_exporter=exporter)
                self.assertEqual(asyncio.run(run()), "context-job")
            spans = {span.name: span for span in exporter.get_finished_spans()}
            root = spans["helper.chat.execute"]
            self.assertEqual(spans["helper.queue.wait"].parent.span_id, root.context.span_id)
            self.assertEqual(spans["helper.inference.execute"].parent.span_id, root.context.span_id)

    def test_exporter_and_ledger_callback_failures_do_not_escape(self):
        from opentelemetry.sdk.trace.export import SpanExporter

        from app.observability import GenAIAttempt, initialize_observability, start_span

        class FailingExporter(SpanExporter):
            def export(self, spans):
                raise RuntimeError("SECRET-EXPORTER-DETAIL")

            def shutdown(self):
                return None

        class FailingLedger:
            def record(self, event):
                raise RuntimeError("SECRET-LEDGER-DETAIL")

        with patch.dict(
            os.environ,
            {"HELPER_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": ""},
            clear=False,
        ):
            initialize_observability(span_exporter=FailingExporter())
            with self.assertLogs("AllTimeHelper", level=logging.WARNING) as captured:
                with start_span("helper.test.export_failure"):
                    pass
                with patch("app.observability.get_usage_ledger", return_value=FailingLedger()):
                    GenAIAttempt(model="openrouter/test").finish()
        output = "\n".join(captured.output)
        self.assertNotIn("SECRET-EXPORTER-DETAIL", output)
        self.assertNotIn("SECRET-LEDGER-DETAIL", output)

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
