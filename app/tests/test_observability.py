import asyncio
import logging
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
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

    def test_model_metadata_is_registry_backed_or_safely_bucketed(self):
        from app.logic.agent_model_registry import FREE_AGENT_PRIMARY, validate_requested_model
        from app.logic.telemetry_metadata import safe_telemetry_model_label

        self.assertEqual(validate_requested_model("helper-auto"), "helper-auto")
        self.assertEqual(validate_requested_model("gemma2:2b"), "gemma2:2b")
        self.assertEqual(validate_requested_model("gemini-1.5-flash-latest"), "gemini-1.5-flash-latest")
        with self.assertRaisesRegex(ValueError, "unsupported_model"):
            validate_requested_model("MODEL_SECRET_9382")
        self.assertEqual(safe_telemetry_model_label(FREE_AGENT_PRIMARY), FREE_AGENT_PRIMARY)
        self.assertEqual(
            safe_telemetry_model_label("gemini/custom-user-route", provider="gemini"),
            "gemini/custom",
        )
        self.assertEqual(safe_telemetry_model_label("MODEL_SECRET_9382"), "unknown")
        self.assertEqual(
            safe_telemetry_model_label("RESPONSE_MODEL_SECRET_9382", provider="openrouter"),
            "openrouter/custom",
        )

    def test_chat_rejects_an_arbitrary_model_before_execution(self):
        from fastapi import HTTPException

        from app.routes.chat import ChatRequest, _chat_endpoint_impl

        with self.assertRaises(HTTPException) as captured:
            asyncio.run(_chat_endpoint_impl(
                ChatRequest(prompt="hello", model="MODEL_SECRET_9382"),
                SimpleNamespace(),
                current_user="owner@example.test",
            ))
        self.assertEqual(captured.exception.status_code, 400)
        self.assertEqual(captured.exception.detail, "Unsupported assistant model.")

    def test_expanded_privacy_sentinels_never_reach_otel_metrics_ledger_or_logs(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from app.logic.usage_ledger import reset_usage_ledger_for_tests
        from app.logger import logger
        from app.logic.capability_policy import (
            CAPABILITY_POLICY,
            CapabilityContext,
            CapabilityGateway,
            CapabilitySource,
        )
        from app.observability import (
            GenAIAttempt,
            initialize_observability,
            record_provider_fallback,
            telemetry_scope,
            trace_workflow_action,
            trace_workflow_execution,
        )

        sentinels = (
            "PROMPT_SECRET_9382",
            "MODEL_SECRET_9382",
            "RESPONSE_MODEL_SECRET_9382",
            "RECIPIENT_SECRET_9382@example.com",
            "ADMIN_SECRET_9382",
            "AUTH_BEARER_SECRET_9382",
            "IMAGE_PROMPT_SECRET_9382",
            "DOCUMENT_SECRET_9382",
        )
        with tempfile.TemporaryDirectory(dir=r"C:\tmp") as tmp:
            db_path = Path(tmp) / "usage.db"
            span_exporter = InMemorySpanExporter()
            metric_reader = InMemoryMetricReader()
            with patch.dict(os.environ, {
                "HELPER_OTEL_ENABLED": "true",
                "HELPER_USAGE_LEDGER_ENABLED": "true",
                "USAGE_DB_FILE": str(db_path),
                "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            }, clear=False):
                reset_usage_ledger_for_tests()
                initialize_observability(span_exporter=span_exporter, metric_reader=metric_reader)
                with self.assertLogs("AllTimeHelper", level=logging.WARNING) as captured:
                    logger.warning("[Telemetry] privacy_sentinel_scan_started")
                    with telemetry_scope(
                        owner=sentinels[3],
                        job_id=str(uuid.uuid4()),
                        workflow_id=str(uuid.uuid4()),
                    ):
                        attempt = GenAIAttempt(
                            model=sentinels[1], provider="openrouter", source=sentinels[0]
                        )
                        attempt.observe_chunk()
                        attempt.finish(
                            SimpleNamespace(
                                model=sentinels[2],
                                usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4),
                            ),
                            RuntimeError("|".join(sentinels)),
                        )
                    record_provider_fallback(sentinels[4], sentinels[5], sentinels[6])
                    GenAIAttempt(model=sentinels[7]).finish()

                    capability_context = CapabilityContext(
                        owner=sentinels[3], source=CapabilitySource.AGENT
                    )
                    gateway = CapabilityGateway({
                        "web.search": lambda **kwargs: kwargs,
                        "image.generate": lambda **kwargs: kwargs,
                        "memory.read": lambda **kwargs: kwargs,
                        "email.draft.build": lambda **kwargs: kwargs,
                    })
                    for capability, arguments in (
                        ("web.search", {"query": sentinels[0]}),
                        ("image.generate", {"prompt": sentinels[6]}),
                        ("memory.read", {"owner": sentinels[3], "text": sentinels[7]}),
                        ("email.draft.build", {"recipient": sentinels[3], "admin_key": sentinels[4]}),
                    ):
                        gateway.invoke(capability, context=capability_context, arguments=arguments)
                    CAPABILITY_POLICY.evaluate(sentinels[7], capability_context)

                    class WorkflowHarness:
                        @trace_workflow_execution
                        def execute(self, plan):
                            return SimpleNamespace(cancelled=False, paused=False, actions={})

                        @trace_workflow_action
                        def execute_action(self, action, plan):
                            return SimpleNamespace(state=SimpleNamespace(value=sentinels[0]))

                    workflow = WorkflowHarness()
                    plan = SimpleNamespace(
                        workflow_id=str(uuid.uuid4()),
                        intent=SimpleNamespace(value=sentinels[7]),
                    )
                    workflow.execute(plan)
                    workflow.execute_action(
                        SimpleNamespace(action_type=SimpleNamespace(value=sentinels[6])), plan
                    )

            spans = span_exporter.get_finished_spans()
            span_payload = repr([
                (span.name, dict(span.attributes), [(event.name, dict(event.attributes)) for event in span.events])
                for span in spans
            ])
            metrics_data = metric_reader.get_metrics_data()
            metric_attributes = [
                dict(point.attributes)
                for resource in metrics_data.resource_metrics
                for scope in resource.scope_metrics
                for metric in scope.metrics
                for point in getattr(metric.data, "data_points", ())
            ]
            metric_payload = repr(metric_attributes)
            log_payload = "\n".join(captured.output)
            database_payload = b"".join(
                path.read_bytes()
                for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"))
                if path.exists()
            ).decode("utf-8", errors="ignore")

            for sentinel in sentinels:
                self.assertNotIn(sentinel, span_payload)
                self.assertNotIn(sentinel, metric_payload)
                self.assertNotIn(sentinel, database_payload)
                self.assertNotIn(sentinel, log_payload)
            for forbidden_dimension in (
                "owner@example.test", "recipient", "job_id", "workflow_id", "request_id",
                "search query", "image prompt", "filename.pdf", "https://example.test/private",
            ):
                self.assertNotIn(forbidden_dimension, metric_payload)
            self.assertIn("openrouter/custom", span_payload)
            self.assertIn("provider_unavailable", span_payload)

            with closing(sqlite3.connect(db_path)) as db:
                rows = repr(db.execute(
                    "SELECT provider,request_model,response_model,status,error_category,source FROM usage_events"
                ).fetchall())
            self.assertIn("openrouter/custom", rows)
            self.assertNotIn("MODEL_SECRET", rows)
            from app.observability import reset_observability_for_tests

            reset_observability_for_tests()
            reset_usage_ledger_for_tests()

    def test_provider_fallback_dimensions_are_finite(self):
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        from app.observability import initialize_observability, record_provider_fallback

        reader = InMemoryMetricReader()
        with patch.dict(os.environ, {
            "HELPER_OTEL_ENABLED": "true", "HELPER_USAGE_LEDGER_ENABLED": "false",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "",
        }, clear=False):
            initialize_observability(metric_reader=reader)
            record_provider_fallback("provider-secret", "destination-secret", "exception-secret")
        attributes = [
            dict(point.attributes)
            for resource in reader.get_metrics_data().resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == "helper.gen_ai.provider_fallbacks"
            for point in metric.data.data_points
        ]
        self.assertEqual(attributes, [{
            "from_provider": "unknown", "to_provider": "unknown", "reason": "unknown",
        }])

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

    def test_token_budget_and_observability_wrapping_are_jointly_idempotent(self):
        import litellm

        from app.logic.cloud_token_budget import apply_cloud_token_budget
        from app.observability import instrument_litellm

        calls = []
        usage_events = []
        callbacks = list(getattr(litellm, "success_callback", []))

        def completion(*args, **kwargs):
            calls.append((args, dict(kwargs)))
            return SimpleNamespace(
                model="google/gemma-4-26b-a4b-it:free",
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        with patch.dict(os.environ, {"HELPER_USAGE_LEDGER_ENABLED": "false"}, clear=False), patch.object(
            litellm, "completion", completion
        ), patch("app.observability.get_usage_ledger") as ledger_getter:
            from app.logic.usage_ledger import reset_usage_ledger_for_tests

            ledger_getter.return_value.record.side_effect = lambda item: usage_events.append(item) or True
            reset_usage_ledger_for_tests()
            apply_cloud_token_budget()
            instrument_litellm()
            apply_cloud_token_budget()
            instrument_litellm()
            wrapped = litellm.completion
            wrapped(model="openrouter/google/gemma-4-26b-a4b-it:free", messages=[])
            self.assertTrue(getattr(wrapped, "__helper_token_budget_patched__", False))
            self.assertTrue(getattr(wrapped, "__helper_observability_patched__", False))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(usage_events), 1)
            self.assertLessEqual(calls[0][1]["max_tokens"], 4096)
            self.assertEqual(getattr(litellm, "success_callback", []), callbacks)

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
