"""
TestLookup — OpenTelemetry distributed tracing setup.

Initialises a global TracerProvider with:
- OTLP HTTP exporter → Jaeger (or any OTEL-compatible collector)
- BatchSpanProcessor for low-overhead async export
- Auto-instrumentation for FastAPI, SQLAlchemy, httpx, Redis

- A span processor, ahead of every exporter, that takes the query string and
  the path of a webhook URL out of the URL attributes (see ``add_url_redaction``)

Exports get_tracer() and get_current_trace_id() helpers for use across the codebase.
"""
import logging
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.request_path import redact_path_credentials

logger = logging.getLogger(__name__)

# Module-level guard so setup_tracing() is truly idempotent
_initialised = False


def _otlp_trace_endpoint(endpoint: str) -> str:
    """Return the signal-specific OTLP/HTTP trace endpoint exactly once."""
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/traces"):
        return endpoint
    return f"{endpoint}/v1/traces"


# ── URL redaction for spans ──────────────────────────────────────────────────
#
# Spans do not pass through the log redaction (``logging_config``): in
# development they are printed to stdout and elsewhere they go to the
# configured collector. A URL can carry a credential in its query string (the
# live-stream route takes the access token there, download routes a one-time
# ticket), and an incoming-webhook URL is a secret in its path.

REDACTED = "[REDACTED]"

#: Hosts whose URL path is the credential (an incoming-webhook URL). Matched on
#: the host itself or as a suffix (``<tenant>.webhook.office.com``). Kept in
#: step with ``project_export_scrub.SECRET_PATH_HOSTS`` by a test; not imported
#: from there because that module loads the ORM models.
WEBHOOK_HOSTS: tuple[str, ...] = (
    "hooks.slack.com",
    "outlook.office.com",
    "webhook.office.com",
    "discord.com",
    "discordapp.com",
    "chat.googleapis.com",
)

#: Attributes that hold a whole URL.
_FULL_URL_ATTRIBUTES = ("http.url", "url.full")
#: Attributes that hold the host a client span talks to.
_HOST_ATTRIBUTES = ("server.address", "net.peer.name", "http.host")


def _is_webhook_host(host: object) -> bool:
    if not isinstance(host, str) or not host:
        return False
    name = host.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower().rstrip(".")
    return any(name == known or name.endswith("." + known) for known in WEBHOOK_HOSTS)


def _redact_full_url(value: str) -> str:
    """Scheme, host and path of ``value``; the query replaced, the rest dropped.

    User and password are dropped, and so is the fragment. A webhook host keeps
    nothing but its name.
    """
    parts = urlsplit(value)
    host = parts.hostname or ""
    if not parts.scheme or not host:
        # Not something we can take apart: keep what precedes any query.
        head = value.split("?", 1)[0].split("#", 1)[0]
        return redact_path_credentials(head) + ("?" + REDACTED if "?" in value else "")
    if ":" in host:  # an IPv6 literal
        host = "[" + host + "]"
    origin = parts.scheme + "://" + host + (":" + str(parts.port) if parts.port else "")
    if _is_webhook_host(parts.hostname):
        return origin + "/" + REDACTED
    return origin + redact_path_credentials(parts.path) + ("?" + REDACTED if parts.query else "")


def _redact_target(value: str) -> str:
    """``/path?query`` with the query, and a share link's token, replaced."""
    head, mark, _ = value.partition("?")
    return redact_path_credentials(head.split("#", 1)[0]) + ("?" + REDACTED if mark else "")


def redact_span_attributes(attributes: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The attributes with their URLs redacted, or ``None`` when nothing changes."""
    changed: dict[str, Any] = {}
    to_webhook = any(_is_webhook_host(attributes.get(key)) for key in _HOST_ATTRIBUTES)
    for key in _FULL_URL_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, str) and value:
            changed[key] = _redact_full_url(value)
    query = attributes.get("url.query")
    if isinstance(query, str) and query:
        changed["url.query"] = REDACTED
    target = attributes.get("http.target")
    if isinstance(target, str) and target:
        changed["http.target"] = "/" + REDACTED if to_webhook else _redact_target(target)
    path = attributes.get("url.path")
    if isinstance(path, str) and path:
        changed["url.path"] = "/" + REDACTED if to_webhook else redact_path_credentials(path)
    changed = {key: value for key, value in changed.items() if attributes.get(key) != value}
    if not changed:
        return None
    return {**attributes, **changed}


#: Marks the processor ``add_url_redaction`` builds, so a caller can recognise it.
_URL_REDACTION_MARK = "_testlookup_url_redaction"


def is_url_redaction(processor: object) -> bool:
    return bool(getattr(processor, _URL_REDACTION_MARK, False))


def add_url_redaction(provider: Any) -> None:
    """Register the URL redaction on ``provider``. Call it BEFORE adding an exporter.

    Processors see a finished span in the order they were added, and every one
    of them is handed the same span object, so the first processor can replace
    the attributes the exporters after it will read. A span is read-only once
    it has ended; the replacement is a new attribute set of the same type.
    """
    from opentelemetry.attributes import BoundedAttributes
    from opentelemetry.sdk.trace import SpanProcessor

    url_keys = (*_FULL_URL_ATTRIBUTES, "url.query", "http.target", "url.path")

    class _UrlRedaction(SpanProcessor):
        def on_end(self, span: Any) -> None:
            current = getattr(span, "_attributes", None)
            if not current:
                return
            try:
                redacted = redact_span_attributes(current)
            except Exception as exc:  # noqa: BLE001 - tracing must not break a request
                # Fail closed: a URL we could not take apart is not exported.
                logger.debug("span URL redaction failed: %s", type(exc).__name__)
                redacted = {k: v for k, v in current.items() if k not in url_keys}
            if redacted is None:
                return
            replacement = BoundedAttributes(
                maxlen=getattr(current, "maxlen", None),
                attributes=redacted,
                immutable=True,
                max_value_len=getattr(current, "max_value_len", None),
            )
            replacement.dropped = getattr(current, "dropped", 0)
            span._attributes = replacement

    setattr(_UrlRedaction, _URL_REDACTION_MARK, True)
    provider.add_span_processor(_UrlRedaction())


def _add_exporter(provider: Any, exporter: Any) -> None:
    """Add a batching exporter, with the URL redaction in front of the first one."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    processors = getattr(getattr(provider, "_active_span_processor", None), "_span_processors", ())
    if not any(is_url_redaction(p) for p in processors):
        add_url_redaction(provider)
    provider.add_span_processor(BatchSpanProcessor(exporter))


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
                         When None: in development spans are written to stdout;
                         anywhere else NOTHING is exported and one warning is
                         logged (re-audit N22 — the stdout fallback printed
                         ~90k log lines per 10 minutes on a real deployment).
    """
    global _initialised
    if _initialised:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
    except ImportError:
        logger.warning("opentelemetry-sdk not installed — tracing disabled")
        return

    try:
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

                trace_endpoint = _otlp_trace_endpoint(otlp_endpoint)
                exporter = OTLPSpanExporter(endpoint=trace_endpoint, timeout=10)
                _add_exporter(provider, exporter)
                logger.info("OTEL tracing enabled → %s", trace_endpoint)
            except ImportError:
                if environment == "development":
                    logger.warning(
                        "opentelemetry-exporter-otlp-proto-http not installed; "
                        "falling back to ConsoleSpanExporter"
                    )
                    _add_exporter(provider, ConsoleSpanExporter())
                else:
                    logger.warning(
                        "opentelemetry-exporter-otlp-proto-http not installed; "
                        "spans are NOT exported (no stdout fallback outside development)"
                    )
        elif environment == "development":
            logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set — writing spans to stdout")
            _add_exporter(provider, ConsoleSpanExporter())
        else:
            # A provider with no processor still issues trace ids (log
            # correlation keeps working) but exports nothing. Logged once:
            # the _initialised guard makes this function run to here once.
            logger.warning(
                "OTEL_ENABLED is true but OTEL_EXPORTER_OTLP_ENDPOINT is not set "
                "(APP_ENV=%s); spans are not exported. Set the endpoint, or "
                "OTEL_ENABLED=false.",
                environment,
            )

        trace.set_tracer_provider(provider)
    except Exception as exc:  # noqa: BLE001 — tracing must not block app startup
        logger.warning("OTEL tracing initialization failed; tracing disabled: %s", exc)
        return

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
