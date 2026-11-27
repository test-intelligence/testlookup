"""Fork-local, opt-in Celery tracing with a single W3C propagation field.

Task arguments, arbitrary headers, baggage and exception text never enter a
span. The consumer span measures execution, while SQL owns business completion.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import logging
import os
import re

from celery import Celery
from celery.apps.beat import Beat
from celery.beat import Service
from kombu.utils.objects import cached_property
from opentelemetry import context, trace
from opentelemetry.context import Context, Token
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.core.config import settings
from app.core.metric_dimensions import task_queue_family

logger = logging.getLogger(__name__)
_TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")
_READY_PID: int | None = None
_ACTIVE = "_testlookup_consumer_trace"


def valid_traceparent(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TRACEPARENT.fullmatch(value)
    return value if match and int(match[1], 16) and int(match[2], 16) else None


def initialize_worker_tracing(**_kwargs) -> None:
    """Initialize the SDK in a child, after fork and only with an endpoint."""
    global _READY_PID
    if not settings.OTEL_ENABLED or not settings.OTEL_EXPORTER_OTLP_ENDPOINT or _READY_PID == os.getpid():
        return
    from app.core.tracing import setup_tracing

    role = "worker-children" if settings.OTEL_LOGS_ROLE == "worker-children" else "worker"
    if setup_tracing(settings.OTEL_SERVICE_NAME, settings.APP_VERSION, settings.APP_ENV,
                     settings.OTEL_EXPORTER_OTLP_ENDPOINT, service_role=role):
        _READY_PID = os.getpid()


def initialize_beat_tracing() -> None:
    """Initialize standalone Beat before its first scheduled publication."""
    if settings.OTEL_ENABLED and settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        from app.core.tracing import setup_tracing

        try:
            setup_tracing(settings.OTEL_SERVICE_NAME, settings.APP_VERSION, settings.APP_ENV,
                          settings.OTEL_EXPORTER_OTLP_ENDPOINT, service_role="beat")
        except Exception as exc:  # noqa: BLE001 - scheduling remains available
            logger.warning("beat_trace_start_failed", extra={"error_type": type(exc).__name__})


def shutdown_worker_tracing(**_kwargs) -> None:
    from app.core.tracing import shutdown_tracing

    try:
        shutdown_tracing()
    except Exception as exc:  # noqa: BLE001 - preserve Celery shutdown
        logger.warning("worker_trace_shutdown_failed", extra={"error_type": type(exc).__name__})


class TracingBeatService(Service):
    """Keep standard scheduler and broker cleanup, then drain telemetry."""

    def start(self, *args, **kwargs):
        initialize_beat_tracing()
        try:
            return super().start(*args, **kwargs)
        finally:
            shutdown_worker_tracing()


class _TracingBeat(Beat):
    Service = TracingBeatService


@contextmanager
def _publication(options):
    """A finite producer span owns success and publication-error exits."""
    if not settings.OTEL_ENABLED or not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        yield
        return
    span = None
    token = None
    try:
        parent = context.get_current()
        if not trace.get_current_span().get_span_context().is_valid:
            headers = options.get("headers")
            supplied = valid_traceparent(headers.get("traceparent")) if isinstance(headers, dict) else None
            if supplied:
                parent = TraceContextTextMapPropagator().extract({"traceparent": supplied}, context=Context())
        queue = options.get("queue")
        span = _tracer().start_span("celery.publish", context=parent, kind=SpanKind.PRODUCER,
                                   attributes={"messaging.system": "celery", "messaging.operation.type": "send",
                                               "messaging.destination.name": task_queue_family(queue)})
        token = context.attach(trace.set_span_in_context(span))
    except Exception as exc:  # noqa: BLE001 - preserve publication behavior
        logger.warning("worker_trace_publish_start_failed", extra={"error_type": type(exc).__name__})
    try:
        yield
    except BaseException:
        if span is not None:
            try:
                span.set_status(Status(StatusCode.ERROR))
            except Exception:  # noqa: BLE001 - preserve the broker exception
                pass
        raise
    finally:
        try:
            if span is not None:
                span.end()
        except Exception as exc:  # noqa: BLE001 - preserve publication result
            logger.warning("worker_trace_publish_finish_failed", extra={"error_type": type(exc).__name__})
        finally:
            if token is not None:
                context.detach(token)


class TracingCelery(Celery):
    """Trace broker publication once for API, task retries and standalone Beat."""

    def send_task(self, *args, **kwargs):
        if not settings.OTEL_ENABLED or not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
            return super().send_task(*args, **kwargs)
        with _publication(kwargs):
            # Redis command instrumentation can otherwise include the encoded
            # Celery task body in LPUSH. Preserve only the manual producer span.
            with suppress_instrumentation():
                return super().send_task(*args, **kwargs)

    @cached_property
    def Beat(self):
        return self.subclass_with_self(_TracingBeat, reverse="Beat")


def propagate_traceparent(*, headers=None, **_kwargs) -> None:
    """Publish only canonical traceparent, retaining unrelated business headers."""
    if not isinstance(headers, dict):
        return
    headers.pop("baggage", None)
    headers.pop("tracestate", None)
    span_context = trace.get_current_span().get_span_context()
    if settings.OTEL_ENABLED and span_context.is_valid:
        headers["traceparent"] = (
            f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-{int(span_context.trace_flags) & 1:02x}"
        )
    else:
        supplied = valid_traceparent(headers.get("traceparent"))
        if supplied is None:
            headers.pop("traceparent", None)


@dataclass
class _Execution:
    span: Span
    token: Token


def _tracer():
    return trace.get_tracer("testlookup.worker")


def start_task_trace(*, task=None, **_kwargs) -> None:
    request = getattr(task, "request", None)
    if request is None or not settings.OTEL_ENABLED or not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        return
    try:
        if not getattr(request, "is_eager", False):
            initialize_worker_tracing()
        # Eager execution keeps its in-process API parent. Broker deliveries
        # start from an empty context and read exactly one validated header.
        parent = context.get_current() if getattr(request, "is_eager", False) else Context()
        headers = getattr(request, "headers", None)
        supplied = valid_traceparent(headers.get("traceparent")) if isinstance(headers, dict) else None
        if supplied is not None:
            parent = TraceContextTextMapPropagator().extract({"traceparent": supplied}, context=Context())
        delivery = getattr(request, "delivery_info", None)
        queue = delivery.get("routing_key") if isinstance(delivery, dict) else None
        span = _tracer().start_span("celery.consume", context=parent, kind=SpanKind.CONSUMER,
                                   attributes={"messaging.system": "celery", "messaging.operation.type": "process",
                                               "messaging.destination.name": task_queue_family(queue)})
        token = context.attach(trace.set_span_in_context(span))
        setattr(request, _ACTIVE, _Execution(span, token))
    except Exception as exc:  # noqa: BLE001 - observation preserves task behavior
        logger.warning("worker_trace_start_failed", extra={"error_type": type(exc).__name__})


def finish_task_trace(*, task=None, state=None, **_kwargs) -> None:
    """End after operational task logging, then restore the caller context."""
    request = getattr(task, "request", None)
    execution = getattr(request, _ACTIVE, None)
    if not isinstance(execution, _Execution):
        return
    delattr(request, _ACTIVE)
    try:
        outcome = str(state or "").lower()
        outcome = outcome if outcome in {"success", "failure", "retry"} else "other"
        execution.span.set_attribute("status", outcome)
        if outcome == "failure":
            execution.span.set_status(Status(StatusCode.ERROR))
        execution.span.end()
    except Exception as exc:  # noqa: BLE001 - preserve task outcome
        logger.warning("worker_trace_finish_failed", extra={"error_type": type(exc).__name__})
    finally:
        context.detach(execution.token)
