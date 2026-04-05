"""
TestLookup — OpenTelemetry distributed tracing setup.

Initialises a global TracerProvider with:
- OTLP HTTP exporter → Jaeger (or any OTEL-compatible collector)
- BatchSpanProcessor for low-overhead async export
- Auto-instrumentation for FastAPI, SQLAlchemy, httpx, Redis

Exports get_tracer() and get_current_trace_id() helpers for use across the codebase.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level guard so setup_tracing() is truly idempotent
_initialised = False


def setup_tracing(
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: Optional[str] = None,
) -> None:
    """
    Configure and register the global OpenTelemetry TracerProvider.

    Args:
        service_name:    Reported as service.name in all spans.
        service_version: Reported as service.version.
        environment:     Reported as deployment.environment (dev/staging/prod).
        otlp_endpoint:   Base URL of the OTLP collector, e.g. "http://jaeger:4318".
                         When None, spans are written to stdout (dev fallback).
    """
    global _initialised
    if _initialised:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError:
        logger.warning("opentelemetry-sdk not installed — tracing disabled")
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
            "service.namespace": "testlookup",
        }
    )

    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(
                endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces",
                timeout=10,
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTEL tracing enabled → %s/v1/traces", otlp_endpoint)
        except ImportError:
            logger.warning(
                "opentelemetry-exporter-otlp-proto-http not installed; "
                "falling back to ConsoleSpanExporter"
            )
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set — writing spans to stdout")
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _initialised = True

    _apply_auto_instrumentors()


def _apply_auto_instrumentors() -> None:
    """Attempt to auto-instrument each supported framework. Failures are non-fatal."""
    _try("FastAPI", _instrument_fastapi)
    _try("SQLAlchemy", _instrument_sqlalchemy)
    _try("httpx", _instrument_httpx)
    _try("Redis", _instrument_redis)


def _try(name: str, fn) -> None:
    try:
        fn()
        logger.debug("OTEL instrumented: %s", name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OTEL auto-instrumentation skipped for %s: %s", name, exc)


def _instrument_fastapi() -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor().instrument()


def _instrument_sqlalchemy() -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument()


def _instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def _instrument_redis() -> None:
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    RedisInstrumentor().instrument()


def get_tracer(name: str):
    """Return a named tracer from the active provider."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return _NoopTracer()


def get_current_trace_id() -> Optional[str]:
    """Return the current OTEL trace ID as a 32-char hex string, or None."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except ImportError:
        return None


class _NoopTracer:
    """Fallback tracer when OTEL is not installed."""

    def start_as_current_span(self, name: str, **kwargs):
        from contextlib import nullcontext

        return nullcontext()

    def start_span(self, name: str, **kwargs):
        return _NoopSpan()


class _NoopSpan:
    def set_attribute(self, *args, **kwargs):
        pass

    def set_status(self, *args, **kwargs):
        pass

    def record_exception(self, *args, **kwargs):
        pass

    def end(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
