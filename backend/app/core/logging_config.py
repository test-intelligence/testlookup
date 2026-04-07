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


def _privacy_redaction(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Redact PII and secrets from all string values in log records (PR-5)."""
    from app.services.redaction_service import redact_text  # noqa: PLC0415

    for key, val in event_dict.items():
        if isinstance(val, str) and key not in ("event", "timestamp", "level", "logger", "service", "version", "env", "trace_id", "span_id"):
            event_dict[key] = redact_text(val)
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
        structlog.processors.UnicodeDecoder(),
        _privacy_redaction,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=True)
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
