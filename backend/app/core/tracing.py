"""
TestLookup — OpenTelemetry distributed tracing setup.

Initialises a global TracerProvider with:
- OTLP HTTP exporter → Jaeger (or any OTEL-compatible collector)
- BatchSpanProcessor for low-overhead async export
- Auto-instrumentation for FastAPI, SQLAlchemy, httpx, Redis

- Export-boundary sanitization of span attributes, events, status and metadata;
  the original finished span is never modified by the exporter wrapper.

Exports get_tracer() and get_current_trace_id() helpers for use across the codebase.
"""
import logging
import re
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.request_path import redact_path_credentials
from app.core.telemetry_identity import telemetry_identity
from app.services.redaction_service import is_log_secret_name, redact_log_message

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
    if parts.scheme and not host:
        return REDACTED
    if not host:
        # Not something we can take apart: keep what precedes any query.
        head = value.split("?", 1)[0].split("#", 1)[0]
        return redact_path_credentials(head) + ("?" + REDACTED if "?" in value else "")
    if ":" in host:  # an IPv6 literal
        host = "[" + host + "]"
    origin = (parts.scheme + "://" if parts.scheme else "//") + host + (":" + str(parts.port) if parts.port else "")
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
    """Every configured exporter receives only sanitized span snapshots."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    processors = getattr(getattr(provider, "_active_span_processor", None), "_span_processors", ())
    if not any(is_url_redaction(p) for p in processors):
        add_url_redaction(provider)
    provider.add_span_processor(BatchSpanProcessor(redacting_span_exporter(exporter)))


# Match references inside exception messages/stacks and custom diagnostic text,
# not just attributes with a semantic-convention URL name. URL parsing removes
# every query/fragment regardless of its parameter spelling or encoding.
_TEXT_URL = re.compile(r"(?:[a-z][a-z0-9+.-]{0,31}:)?//[^\s\"'<>]+", re.IGNORECASE)
_TEXT_SECRET = re.compile(
    r"(?P<head>(?<![A-Za-z0-9])"
    r"(?:password|passwd|passphrase|token|secret|api[_-]?key|credentials?|authorization|cookie|"
    r"(?:private|signing|hmac|ssh|deploy|encryption|access)[_-]?key|smtp[_-]?pass)"
    r"(?:\\*[\"'])?\s*[:=]\s*)",
    re.IGNORECASE,
)
_TEXT_CREDENTIAL = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_TEXT_OPAQUE_TOKEN = re.compile(
    r"\b(?:eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|(?:tl|qai|scim)_[A-Za-z0-9_-]{16,})\b"
)
# Raw payloads are not an operational trace contract, even if their content
# does not happen to resemble a known credential pattern.
_PAYLOAD_ATTRIBUTES = frozenset({
    "http.request.body", "http.response.body", "http.request.body.content",
    "http.response.body.content", "gen_ai.prompt", "gen_ai.completion",
    "gen_ai.input.messages", "gen_ai.output.messages", "llm.prompt", "llm.completion",
})
_MAX_TRACE_TEXT_CHARS = 16_384


def _redact_span_text(value: str) -> str:
    # Bound the shared operational-text scrubbers' work. Omit an oversized
    # field completely: truncating first could leave an unrecognized fragment
    # of a credential, and giant exception text must not monopolize export.
    if len(value) > _MAX_TRACE_TEXT_CHARS:
        return "[REDACTED_OVERSIZED_TRACE_TEXT]"

    def reference(match: re.Match[str]) -> str:
        address = match.group()
        # JSON encoding escapes the surrounding quote, not the URL itself.
        # Preserve that delimiter's backslashes when replacing a URL so the
        # following assignment scan can still locate the encoded value's end.
        trailing = len(address) - len(address.rstrip("\\"))
        suffix = address[-trailing:] if trailing else ""
        try:
            return _redact_full_url(address[:-trailing] if trailing else address) + suffix
        except (ValueError, TypeError):
            return REDACTED + suffix

    value = _TEXT_URL.sub(reference, value)
    value = _redact_secret_assignments(value)
    value = _TEXT_CREDENTIAL.sub(lambda match: match[1] + " " + REDACTED, value)
    value = _TEXT_OPAQUE_TOKEN.sub(REDACTED, value)
    return redact_log_message(value)


def _redact_secret_assignments(value: str) -> str:
    """Replace credential values without decoding surrounding diagnostic text.

    A quoted JSON value can itself be encoded into another JSON string. Its
    delimiter then has 1, 3, 7, ... preceding backslashes. Quotes inside that
    value have a different escape parity. Recognizing the delimiter locally
    handles both forms and passwords containing escaped quotes/backslashes.
    """
    pieces = []
    cursor = 0
    while match := _TEXT_SECRET.search(value, cursor):
        start = match.end()
        pieces.append(value[cursor:start])
        opening = re.match(r"(\\*)([\"'])", value[start:])
        if opening:
            slashes, quote = opening.groups()
            search_at = start + opening.end()
            end = len(value)
            replacement = opening.group() + REDACTED
            while (position := value.find(quote, search_at)) >= 0:
                before = position
                while before > start and value[before - 1] == "\\":
                    before -= 1
                if (position - before) % (2 * (len(slashes) + 1)) == len(slashes):
                    end = position + 1
                    replacement += slashes + quote
                    break
                search_at = position + 1
            # Malformed/unclosed credentials lose the remaining value, rather
            # than leaving part of a secret in a recoverable alternate form.
            pieces.append(replacement)
        else:
            unquoted = re.match(r"[^\s,;}\]]*", value[start:])
            assert unquoted is not None  # This expression always matches, including an empty value.
            end = start + unquoted.end()
            pieces.append(REDACTED)
        cursor = end
    pieces.append(value[cursor:])
    return "".join(pieces)


def _redact_trace_value(key: str, value: Any, *, depth: int = 0) -> Any:
    normalized = key.lower().replace("-", "_")
    if normalized == "url.query" and value:
        return REDACTED
    if (
        normalized in _PAYLOAD_ATTRIBUTES
        or is_log_secret_name(normalized.replace(".", "_"))
        or is_log_secret_name(normalized.rsplit(".", 1)[-1])
    ):
        return value if value is None or isinstance(value, bool) else REDACTED
    if isinstance(value, str):
        return _redact_span_text(value)
    if depth >= 10:
        return REDACTED
    if isinstance(value, Mapping):
        return _redact_trace_attributes(value, depth=depth + 1)
    if isinstance(value, (list, tuple)):
        return tuple(_redact_trace_value(key, item, depth=depth + 1) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Never stringify an unsupported value after sanitization: its repr may
    # contain exactly the sensitive input that the exporter must not receive.
    return REDACTED


def _redact_trace_attributes(attributes: Optional[Mapping[str, Any]], *, depth: int = 0) -> dict[str, Any]:
    current = attributes or {}
    try:
        current = redact_span_attributes(current) or current
    except (TypeError, ValueError):
        current = {
            key: value for key, value in current.items()
            if key not in (*_FULL_URL_ATTRIBUTES, "url.query", "http.target", "url.path")
        }
    return {
        _redact_span_text(key): _redact_trace_value(key, value, depth=depth)
        for key, value in current.items() if isinstance(key, str)
    }


def _sanitized_span(span: Any) -> Any:
    """Copy public SDK data into an export-only snapshot, including all copies.

    Event/status objects remain untouched on the original ReadableSpan. This
    boundary is shared by OTLP, console and test exporters, rather than relying
    on mutating the SDK's private event/status storage from an on_end processor.
    """
    from opentelemetry.attributes import BoundedAttributes
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import Link, SpanContext, Status, TraceState

    def attributes(original):
        copied = BoundedAttributes(
            attributes=_redact_trace_attributes(original), maxlen=None, immutable=True,
        )
        copied.dropped = getattr(original, "dropped", 0)
        return copied

    def context(original):
        if original is None:
            return None
        return SpanContext(
            trace_id=original.trace_id, span_id=original.span_id,
            is_remote=original.is_remote, trace_flags=original.trace_flags,
            trace_state=TraceState([
                (key, _redact_trace_value(key, value))
                for key, value in original.trace_state.items()
            ]),
        )

    scope = span.instrumentation_scope
    safe_scope = None if scope is None else InstrumentationScope(
        name=_redact_span_text(scope.name),
        version=_redact_span_text(scope.version) if scope.version else scope.version,
        schema_url=_redact_span_text(scope.schema_url) if scope.schema_url else scope.schema_url,
        attributes=attributes(scope.attributes),
    )
    dropped = (span.dropped_attributes, span.dropped_events, span.dropped_links)

    class _SanitizedSpan(ReadableSpan):
        # Preserve SDK loss accounting while building ordinary immutable input
        # containers, without reading or changing any SDK private storage.
        @property
        def dropped_attributes(self):
            return dropped[0]

        @property
        def dropped_events(self):
            return dropped[1]

        @property
        def dropped_links(self):
            return dropped[2]

    return _SanitizedSpan(
        name=_redact_span_text(span.name), context=context(span.context), parent=context(span.parent),
        kind=span.kind, start_time=span.start_time, end_time=span.end_time,
        attributes=attributes(span.attributes),
        events=tuple(Event(
            name=_redact_span_text(event.name), timestamp=event.timestamp,
            attributes=attributes(event.attributes),
            limit=None,
        ) for event in span.events),
        links=tuple(Link(context(link.context), attributes(link.attributes)) for link in span.links),
        status=Status(span.status.status_code, _redact_span_text(span.status.description) if span.status.description else None),
        resource=Resource(
            attributes(span.resource.attributes),
            _redact_span_text(span.resource.schema_url) if span.resource.schema_url else span.resource.schema_url,
        ),
        instrumentation_scope=safe_scope,
    )


def redacting_span_exporter(exporter: Any) -> Any:
    """Wrap an exporter with the complete privacy boundary; fail closed per span."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class _RedactingExporter(SpanExporter):
        def export(self, spans):
            safe = []
            for span in spans:
                try:
                    safe.append(_sanitized_span(span))
                except Exception as exc:  # noqa: BLE001 -- never export raw on failure
                    logger.warning("trace span omitted: sanitization failed (%s)", type(exc).__name__)
            return exporter.export(tuple(safe)) if safe else SpanExportResult.FAILURE

        def shutdown(self):
            return exporter.shutdown()

        def force_flush(self, timeout_millis=30000):
            return exporter.force_flush(timeout_millis=timeout_millis)

    return _RedactingExporter()


def setup_tracing(
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: Optional[str] = None,
    *,
    service_role: str = "api",
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
        from app.core.config import settings

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment": environment,
                "service.namespace": "testlookup",
                "testlookup.service.role": service_role,
                **telemetry_identity(settings.BUILD_REVISION, settings.OBSERVABILITY_INSTALLATION),
            }
        )

        provider = TracerProvider(resource=resource)

        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                trace_endpoint = _otlp_trace_endpoint(otlp_endpoint)
                if urlsplit(trace_endpoint).hostname in {
                    "otel-collector", "testlookup-otel.testlookup-telemetry.svc",
                }:
                    import requests

                    session = requests.Session()
                    session.trust_env = False
                    session.max_redirects = 0
                    exporter = OTLPSpanExporter(endpoint=trace_endpoint, timeout=10, session=session)
                else:
                    exporter = OTLPSpanExporter(endpoint=trace_endpoint, timeout=10)
                _add_exporter(provider, exporter)
                logger.info("OTEL tracing enabled")
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
