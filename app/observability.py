"""Application-owned OpenTelemetry and privacy-safe GenAI accounting."""

from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, Iterator, Mapping

from app.logger import logger
from app.logic.telemetry_metadata import (
    provider_from_model,
    safe_error_category,
    safe_fallback_reason,
    safe_memory_operation,
    safe_operation_outcome,
    safe_provider_label,
    safe_source_label,
    safe_telemetry_model_label,
    safe_workflow_action,
    safe_workflow_intent,
    safe_workflow_state,
)
from app.logic.usage_ledger import UsageEvent, get_usage_ledger


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelemetryContext:
    owner: str | None = None
    job_id: str | None = None
    workflow_id: str | None = None
    source: str = "application"


_context: ContextVar[TelemetryContext] = ContextVar("helper_telemetry_context", default=TelemetryContext())


@contextmanager
def telemetry_scope(
    *,
    owner: str | None = None,
    job_id: str | None = None,
    workflow_id: str | None = None,
    source: str | None = None,
) -> Iterator[TelemetryContext]:
    current = _context.get()
    updated = replace(
        current,
        owner=owner if owner is not None else current.owner,
        job_id=job_id if job_id is not None else current.job_id,
        workflow_id=workflow_id if workflow_id is not None else current.workflow_id,
        source=source if source is not None else current.source,
    )
    token = _context.set(updated)
    try:
        yield updated
    finally:
        _context.reset(token)


def current_telemetry_context() -> TelemetryContext:
    return _context.get()


class _State:
    def __init__(self) -> None:
        self.enabled = False
        self.initialized = False
        self.otlp_configured = False
        self.tracer_provider: Any = None
        self.meter_provider: Any = None
        self.tracer: Any = None
        self.meter: Any = None
        self.instruments: dict[str, Any] = {}


_state = _State()
_state_lock = threading.RLock()


def _resource_attributes() -> dict[str, str]:
    attributes = {"service.name": os.getenv("OTEL_SERVICE_NAME", "all-time-helper")}
    allowed = {"service.version", "deployment.environment", "deployment.environment.name"}
    for item in str(os.getenv("OTEL_RESOURCE_ATTRIBUTES") or "").split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip() in allowed and value.strip():
            attributes[key.strip()] = value.strip()
    return attributes


def _queue_depth_observer(options: Any) -> list[Any]:
    try:
        from opentelemetry.metrics import Observation
        from app.inference_queue import inference_queue

        return [
            Observation(inference_queue.inference_queue_depth, {"lane": "inference"}),
            Observation(inference_queue.tool_queue_depth, {"lane": "tool"}),
        ]
    except Exception:
        return []


def initialize_observability(*, span_exporter: Any = None, metric_reader: Any = None) -> dict[str, bool]:
    """Initialize isolated providers once. Export remains opt-in and fail-open."""
    with _state_lock:
        if _state.initialized:
            return observability_status()
        _state.enabled = _env_enabled("HELPER_OTEL_ENABLED", False)
        _state.otlp_configured = bool(str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip())
        if not _state.enabled:
            _state.initialized = True
            instrument_litellm()
            return observability_status()
        try:
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

            resource = Resource(_resource_attributes())
            tracer_provider = TracerProvider(resource=resource)
            readers: list[Any] = []
            endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
            protocol = str(os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")).strip().lower()
            if protocol != "http/protobuf":
                raise ValueError("unsupported_otlp_protocol")
            if span_exporter is not None:
                tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
            elif endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            if metric_reader is not None:
                readers.append(metric_reader)
            elif endpoint:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

                readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint)))
            meter_provider = MeterProvider(metric_readers=readers, resource=resource, shutdown_on_exit=False)
            tracer = tracer_provider.get_tracer("app.observability")
            meter = meter_provider.get_meter("app.observability")
            instruments = {
                "gen_ai.client.operation.duration": meter.create_histogram("gen_ai.client.operation.duration", unit="s"),
                "gen_ai.client.token.usage": meter.create_histogram("gen_ai.client.token.usage", unit="{token}"),
                "gen_ai.client.operation.time_to_first_chunk": meter.create_histogram("gen_ai.client.operation.time_to_first_chunk", unit="s"),
                "helper.gen_ai.provider_fallbacks": meter.create_counter("helper.gen_ai.provider_fallbacks"),
                "helper.inference.queue.wait.duration": meter.create_histogram("helper.inference.queue.wait.duration", unit="s"),
                "helper.inference.execution.duration": meter.create_histogram("helper.inference.execution.duration", unit="s"),
                "helper.workflow.operation.duration": meter.create_histogram("helper.workflow.operation.duration", unit="s"),
                "helper.workflow.action.duration": meter.create_histogram("helper.workflow.action.duration", unit="s"),
                "helper.capability.invocations": meter.create_counter("helper.capability.invocations"),
                "helper.memory.operation.duration": meter.create_histogram("helper.memory.operation.duration", unit="s"),
                "helper.memory.operation.failures": meter.create_counter("helper.memory.operation.failures"),
                "helper.chat_job.created": meter.create_counter("helper.chat_job.created"),
                "helper.chat_job.recovered": meter.create_counter("helper.chat_job.recovered"),
                "helper.chat_job.cancelled": meter.create_counter("helper.chat_job.cancelled"),
                "helper.chat_job.interrupted": meter.create_counter("helper.chat_job.interrupted"),
                "helper.external_action.prepared": meter.create_counter("helper.external_action.prepared"),
                "helper.external_action.dispatches": meter.create_counter("helper.external_action.dispatches"),
                "helper.external_action.unknown_results": meter.create_counter("helper.external_action.unknown_results"),
            }
            meter.create_observable_gauge(
                "helper.inference.queue.depth",
                callbacks=[_queue_depth_observer],
                unit="{job}",
            )
            _state.tracer_provider = tracer_provider
            _state.meter_provider = meter_provider
            _state.tracer = tracer
            _state.meter = meter
            _state.instruments = instruments
            _state.initialized = True
            instrument_litellm()
        except Exception as exc:
            _state.enabled = False
            _state.initialized = True
            logger.warning("[Telemetry] otel_initialization_failed error_type=%s", type(exc).__name__)
            instrument_litellm()
        return observability_status()


def observability_status() -> dict[str, bool]:
    try:
        ledger_healthy = get_usage_ledger().healthy()
    except Exception as exc:
        ledger_healthy = False
        logger.warning("[Telemetry] usage_ledger_status_failed error_type=%s", type(exc).__name__)
    return {
        "otel_enabled": bool(_state.enabled),
        "otlp_configured": bool(_state.otlp_configured),
        "usage_ledger_healthy": ledger_healthy,
    }


def _bounded_call(fn: Any, timeout_seconds: float) -> None:
    thread = threading.Thread(target=lambda: _safe_call(fn), daemon=True, name="telemetry-shutdown")
    thread.start()
    thread.join(max(0.01, timeout_seconds))


def _safe_call(fn: Any) -> None:
    try:
        fn()
    except Exception as exc:
        logger.warning("[Telemetry] shutdown_failed error_type=%s", type(exc).__name__)


def shutdown_observability(timeout_seconds: float = 2.0) -> None:
    global _state
    with _state_lock:
        if not _state.initialized:
            return
        provider = _state.tracer_provider
        meter_provider = _state.meter_provider
        if provider is not None:
            _bounded_call(lambda: provider.force_flush(timeout_millis=int(timeout_seconds * 500)), timeout_seconds / 2)
            _bounded_call(provider.shutdown, timeout_seconds / 2)
        if meter_provider is not None:
            _bounded_call(lambda: meter_provider.force_flush(timeout_millis=int(timeout_seconds * 500)), timeout_seconds / 2)
            _bounded_call(meter_provider.shutdown, timeout_seconds / 2)
        _state = _State()


def reset_observability_for_tests() -> None:
    global _state
    shutdown_observability(0.2)
    with _state_lock:
        _state = _State()


@contextmanager
def start_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    start_time_ns: int | None = None,
) -> Iterator[Any]:
    if not _state.enabled or _state.tracer is None:
        yield None
        return
    try:
        manager = _state.tracer.start_as_current_span(
            name,
            attributes=dict(attributes or {}),
            start_time=start_time_ns,
            record_exception=False,
            set_status_on_exception=False,
        )
        span = manager.__enter__()
    except Exception as exc:
        logger.warning("[Telemetry] span_failed error_type=%s", type(exc).__name__)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as close_exc:
            logger.warning("[Telemetry] span_close_failed error_type=%s", type(close_exc).__name__)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            logger.warning("[Telemetry] span_close_failed error_type=%s", type(exc).__name__)


def record_histogram(name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
    try:
        instrument = _state.instruments.get(name)
        if instrument is not None:
            instrument.record(float(value), attributes=dict(attributes or {}))
    except Exception as exc:
        logger.warning("[Telemetry] metric_dropped error_type=%s", type(exc).__name__)


def increment_counter(name: str, attributes: Mapping[str, Any] | None = None, amount: int = 1) -> None:
    try:
        instrument = _state.instruments.get(name)
        if instrument is not None:
            instrument.add(int(amount), attributes=dict(attributes or {}))
    except Exception as exc:
        logger.warning("[Telemetry] metric_dropped error_type=%s", type(exc).__name__)


def record_provider_fallback(from_provider: str, to_provider: str, reason: str) -> None:
    increment_counter(
        "helper.gen_ai.provider_fallbacks",
        {
            "from_provider": safe_provider_label(from_provider),
            "to_provider": safe_provider_label(to_provider),
            "reason": safe_fallback_reason(reason),
        },
    )


def trace_memory_operation(operation: str):
    safe_operation = safe_memory_operation(operation)

    def decorate(fn: Any) -> Any:
        @wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            outcome = "completed"
            try:
                with start_span(f"helper.memory.{safe_operation}", {"helper.memory.operation": safe_operation}):
                    return fn(*args, **kwargs)
            except Exception:
                outcome = "failed"
                increment_counter("helper.memory.operation.failures", {"operation": safe_operation})
                raise
            finally:
                record_histogram(
                    "helper.memory.operation.duration",
                    time.perf_counter() - started,
                    {"operation": safe_operation, "outcome": safe_operation_outcome(outcome)},
                )

        return wrapped

    return decorate


def trace_workflow_execution(fn: Any) -> Any:
    @wraps(fn)
    def wrapped(self: Any, plan: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        state = "failed"
        workflow_id = str(getattr(plan, "workflow_id", ""))
        intent = safe_workflow_intent(getattr(getattr(plan, "intent", None), "value", "unknown"))
        with telemetry_scope(workflow_id=workflow_id, source="workflow"):
            with start_span(
                "helper.workflow.execute",
                {"helper.workflow.id": workflow_id, "helper.workflow.intent": intent},
            ):
                try:
                    result = fn(self, plan, *args, **kwargs)
                    if getattr(result, "cancelled", False):
                        state = "cancelled"
                    elif getattr(result, "paused", False):
                        state = "paused"
                    elif any(
                        str(getattr(getattr(item, "state", None), "value", "")) == "failed"
                        for item in getattr(result, "actions", {}).values()
                    ):
                        state = "failed"
                    else:
                        state = "completed"
                    return result
                finally:
                    record_histogram(
                        "helper.workflow.operation.duration",
                        time.perf_counter() - started,
                        {"intent": intent, "state": safe_workflow_state(state)},
                    )

    return wrapped


def trace_workflow_action(fn: Any) -> Any:
    @wraps(fn)
    def wrapped(self: Any, action: Any, plan: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        action_type = safe_workflow_action(getattr(getattr(action, "action_type", None), "value", "unknown"))
        state = "failed"
        with start_span(
            "helper.workflow.action",
            {
                "helper.workflow.id": str(getattr(plan, "workflow_id", "")),
                "helper.workflow.action_type": action_type,
            },
        ):
            try:
                result = fn(self, action, plan, *args, **kwargs)
                state = str(getattr(getattr(result, "state", None), "value", "unknown"))
                return result
            finally:
                record_histogram(
                    "helper.workflow.action.duration",
                    time.perf_counter() - started,
                    {"action_type": action_type, "state": safe_workflow_state(state)},
                )

    return wrapped


def _provider_name(model: str) -> str:
    return provider_from_model(model)


def _read_value(value: Any, *names: str) -> Any:
    for name in names:
        try:
            if isinstance(value, Mapping) and name in value:
                return value.get(name)
            candidate = getattr(value, name, None)
            if candidate is not None:
                return candidate
        except Exception:
            continue
    return None


def _response_usage(response: Any) -> tuple[int | None, int | None]:
    usage = _read_value(response, "usage")
    input_tokens = _read_value(usage, "prompt_tokens", "input_tokens") if usage is not None else None
    output_tokens = _read_value(usage, "completion_tokens", "output_tokens") if usage is not None else None
    if input_tokens is None:
        input_tokens = _read_value(response, "prompt_eval_count")
    if output_tokens is None:
        output_tokens = _read_value(response, "eval_count")
    def normalized(value: Any) -> int | None:
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    return normalized(input_tokens), normalized(output_tokens)


def _response_cost(response: Any) -> tuple[float | None, str]:
    hidden = _read_value(response, "_hidden_params")
    cost = _read_value(hidden, "response_cost") if hidden is not None else None
    if cost is None:
        cost = _read_value(response, "response_cost")
    try:
        return (float(cost), "litellm") if cost is not None else (None, "unknown")
    except (TypeError, ValueError):
        return None, "unknown"


class GenAIAttempt:
    """One provider attempt, finalized once even for streaming responses."""

    def __init__(
        self,
        *,
        model: str,
        provider: str | None = None,
        attempt: int = 1,
        source: str = "provider",
        local_cost: bool = False,
    ) -> None:
        self.event_id = str(uuid.uuid4())
        raw_model = model
        self.provider = safe_provider_label(provider or _provider_name(raw_model))
        self.model = safe_telemetry_model_label(raw_model, provider=self.provider)
        self.attempt = max(1, int(attempt))
        self.source = safe_source_label(source)
        self.local_cost = local_cost
        self.started_wall = time.time()
        self.started = time.perf_counter()
        self.ttfc_ms: float | None = None
        self._finished = False
        self._lock = threading.Lock()
        context = current_telemetry_context()
        self._correlation = context
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
            "helper.attempt": self.attempt,
        }
        if context.job_id:
            attrs["helper.job.id"] = context.job_id
        if context.workflow_id:
            attrs["helper.workflow.id"] = context.workflow_id
        self._span_context = start_span("gen_ai.chat", attrs)
        self.span = self._span_context.__enter__()

    def observe_chunk(self, chunk: Any = None) -> None:
        if self.ttfc_ms is not None:
            return
        self.ttfc_ms = (time.perf_counter() - self.started) * 1000

    def _finish_unsafe(self, response: Any = None, error: Exception | None = None) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        duration_ms = (time.perf_counter() - self.started) * 1000
        raw_response_model = _read_value(response, "model")
        response_model = (
            safe_telemetry_model_label(raw_response_model, provider=self.provider)
            if raw_response_model
            else None
        )
        input_tokens, output_tokens = _response_usage(response)
        if self.local_cost:
            cost_usd, cost_source = 0.0, "local"
        else:
            cost_usd, cost_source = _response_cost(response)
        error_type = safe_error_category(error)
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
        }
        if response_model:
            attributes["gen_ai.response.model"] = response_model
        if error_type:
            attributes["error.type"] = error_type
        record_histogram("gen_ai.client.operation.duration", duration_ms / 1000, attributes)
        if input_tokens is not None:
            record_histogram("gen_ai.client.token.usage", input_tokens, {**attributes, "gen_ai.token.type": "input"})
        if output_tokens is not None:
            record_histogram("gen_ai.client.token.usage", output_tokens, {**attributes, "gen_ai.token.type": "output"})
        if self.ttfc_ms is not None:
            record_histogram("gen_ai.client.operation.time_to_first_chunk", self.ttfc_ms / 1000, attributes)
        if self.span is not None:
            try:
                if response_model:
                    self.span.set_attribute("gen_ai.response.model", response_model)
                if error_type:
                    self.span.set_attribute("error.type", error_type)
            except Exception:
                pass
        try:
            get_usage_ledger().record(
                UsageEvent(
                    event_id=self.event_id,
                    occurred_at=self.started_wall,
                    owner=self._correlation.owner,
                    job_id=self._correlation.job_id,
                    workflow_id=self._correlation.workflow_id,
                    operation="chat",
                    provider=self.provider,
                    request_model=self.model,
                    response_model=response_model,
                    status="failed" if error else "completed",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    cost_source=cost_source,
                    duration_ms=duration_ms,
                    time_to_first_chunk_ms=self.ttfc_ms,
                    attempt=self.attempt,
                    error_category=error_type,
                    source=self.source,
                )
            )
        except Exception as exc:
            logger.warning("[Telemetry] provider_usage_dropped error_type=%s", type(exc).__name__)
        try:
            self._span_context.__exit__(type(error) if error else None, error, error.__traceback__ if error else None)
        except Exception:
            pass

    def finish(self, response: Any = None, error: Exception | None = None) -> None:
        try:
            self._finish_unsafe(response, error)
        except Exception as exc:
            logger.warning("[Telemetry] provider_finalization_failed error_type=%s", type(exc).__name__)
            try:
                self._span_context.__exit__(None, None, None)
            except Exception:
                pass

    def __enter__(self) -> "GenAIAttempt":
        return self

    def __exit__(self, exc_type: Any, exc: Exception | None, traceback: Any) -> bool:
        self.finish(error=exc)
        return False


class _ObservedStream:
    def __init__(self, stream: Any, attempt: GenAIAttempt) -> None:
        self._stream = stream
        self._attempt = attempt
        self._last = None

    def __iter__(self) -> "_ObservedStream":
        return self

    def __next__(self) -> Any:
        try:
            item = next(self._stream)
            self._last = item
            if _chunk_has_content(item):
                self._attempt.observe_chunk(item)
            return item
        except StopIteration:
            self._attempt.finish(self._last)
            raise
        except Exception as exc:
            self._attempt.finish(self._last, exc)
            raise

    def close(self) -> None:
        try:
            close = getattr(self._stream, "close", None)
            if callable(close):
                close()
        finally:
            self._attempt.finish(self._last)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _chunk_has_content(chunk: Any) -> bool:
    try:
        choices = _read_value(chunk, "choices") or []
        if choices:
            delta = _read_value(choices[0], "delta")
            return bool(_read_value(delta, "content"))
        message = _read_value(chunk, "message")
        return bool(_read_value(message, "content"))
    except Exception:
        return False


def instrument_litellm() -> bool:
    """Wrap LiteLLM without mutating or replacing its callback lists."""
    try:
        import litellm

        current = litellm.completion
        if getattr(current, "__helper_observability_patched__", False):
            return True

        def observed_completion(*args: Any, **kwargs: Any) -> Any:
            model = kwargs.get("model") or (args[0] if args else "unknown")
            attempt_number = kwargs.pop("helper_attempt", 1)
            try:
                observed = GenAIAttempt(model=str(model), attempt=attempt_number, source="litellm")
            except Exception as exc:
                logger.warning("[Telemetry] provider_observer_failed error_type=%s", type(exc).__name__)
                return current(*args, **kwargs)
            try:
                response = current(*args, **kwargs)
            except Exception as exc:
                observed.finish(error=exc)
                raise
            if kwargs.get("stream"):
                return _ObservedStream(response, observed)
            observed.finish(response)
            return response

        observed_completion.__helper_observability_patched__ = True
        observed_completion.__helper_token_budget_patched__ = bool(
            getattr(current, "__helper_token_budget_patched__", False)
        )
        observed_completion.__wrapped__ = current
        litellm.completion = observed_completion
        return True
    except Exception as exc:
        logger.warning("[Telemetry] litellm_instrumentation_failed error_type=%s", type(exc).__name__)
        return False
