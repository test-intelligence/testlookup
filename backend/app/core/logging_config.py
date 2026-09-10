"""
TestLookup — Structured logging configuration.

Configures structlog with:
- JSON renderer in production / colored console in development
- Automatic injection of OpenTelemetry trace_id + span_id into every log record
- Service metadata (name, version, env) on every record
- Stdlib logging bridged through structlog so third-party libraries integrate automatically
- Noisy loggers silenced to WARNING

Call configure_logging() once at application startup before any logger is used.
"""
import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger


def _add_otel_trace_context(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Inject the current OpenTelemetry trace_id / span_id into every log record."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except ImportError:
        pass
    return event_dict


#: Fields owned by the log record's own structure rather than by its caller.
#: Everything else is redacted, ``event`` included -- see _privacy_redaction.
_STRUCTURAL_LOG_FIELDS: frozenset[str] = frozenset({
    "timestamp",
    "level",
    "logger",
    "service",
    "version",
    "env",
    "trace_id",
    "span_id",
})

#: Free-text fields: the message, and the traceback and stack rendered into
#: the record before redaction runs. They carry operational numbers and
#: addresses, so they get the marker-based set -- see _privacy_redaction.
_MESSAGE_FIELDS: frozenset[str] = frozenset({"event", "exception", "stack"})


def _privacy_redaction(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Redact PII and secrets from string values in log records (PR-5).

    ``event`` used to be exempt, which put the exemption on the one field that
    carries free-form text (re-audit H3). Every ``logger.warning("... %s",
    value)`` renders its arguments into ``event``, as does every f-string
    message, so the field most likely to contain a token, a connection string
    or an address was the field guaranteed not to be scrubbed. Structured
    key/value pairs -- which were being redacted -- are the ones a developer
    chose deliberately.

    What stays exempt is only the record's own structure. None of those fields
    can hold caller data: they are set by this module or by structlog itself,
    and redacting them would corrupt the record (an ``@`` in a logger name
    reading as an email address, say).

    The message is operational text, so it gets only what a marker identifies:
    credentials and email addresses (``redact_log_message``). The phone, card
    and IPv4 heuristics match by shape, and applied to the message they ate
    byte counts, epoch seconds, build numbers and the host an operator needs
    from a warning. A structured field keeps the full set, as it always had.
    A traceback is treated like the message: ``format_exc_info`` renders it
    into ``exception`` earlier in the chain, so it is text by the time this
    runs, and the exception message inside it is where a rejected password
    or a connection string usually sits.
    """
    from app.services.redaction_service import (  # noqa: PLC0415
        redact_log_message,
        redact_text,
    )

    for key, val in event_dict.items():
        if not isinstance(val, str) or key in _STRUCTURAL_LOG_FIELDS:
            continue
        event_dict[key] = (
            redact_log_message(val) if key in _MESSAGE_FIELDS else redact_text(val)
        )
    return event_dict


def _add_service_info(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Inject constant service metadata on every log record."""
    # Import lazily to avoid circular-import at module load time
    from app.core.config import settings  # noqa: PLC0415

    event_dict.setdefault("service", settings.APP_NAME)
    event_dict.setdefault("version", settings.APP_VERSION)
    event_dict.setdefault("env", settings.APP_ENV)
    return event_dict


def configure_logging() -> None:
    """
    Set up structlog + stdlib logging.
    Idempotent — safe to call multiple times.
    """
    from app.core.config import settings  # noqa: PLC0415

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    use_json = settings.LOG_FORMAT == "json"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_service_info,
        _add_otel_trace_context,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        # Render exc_info to text BEFORE redaction (re-audit H3, QA). Left to
        # the renderer, a traceback -- exception message and all -- reached the
        # output as an exc_info tuple, after redaction had already run.
        # A structlog .exception() call carries no exc_info of its own -- the
        # generic BoundLogger does not add it -- so its traceback was dropped.
        structlog.dev.set_exc_info,
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _privacy_redaction,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if use_json
        # Tracebacks arrive already rendered, and redacted, by format_exc_info;
        # the plain formatter prints them as they are instead of warning.
        else structlog.dev.ConsoleRenderer(
            colors=True, exception_formatter=structlog.dev.plain_traceback
        )
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Silence high-volume or irrelevant loggers
    _quiet = [
        "uvicorn.access",
        "httpx",
        "httpcore",
        "opentelemetry",
        "chromadb",
        "langchain",
        "langchain_core",
    ]
    for name in _quiet:
        logging.getLogger(name).setLevel(logging.WARNING)
