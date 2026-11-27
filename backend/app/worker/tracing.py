"""Fork-local, opt-in Celery tracing with a single W3C propagation field.

Task arguments, arbitrary headers, baggage and exception text never enter a
span. The consumer span measures execution, while SQL owns business completion.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re

from opentelemetry import context, trace
from opentelemetry.context import Context, Token
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
    setup_tracing(settings.OTEL_SERVICE_NAME, settings.APP_VERSION, settings.APP_ENV,
                  settings.OTEL_EXPORTER_OTLP_ENDPOINT, service_role=role)
    _READY_PID = os.getpid()


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
