"""
Privacy Enforcement Service — policy-mode wrappers for redaction.

Provides named entry points for each enforcement boundary:
  - persistence (database, MongoDB, cache)
  - logging (structlog, telemetry)
  - LLM (AI prompts, tool inputs)
  - reports (HTML, PDF, email, exports)

All modes delegate to the unified redaction service but provide
clear intent for callers and future policy divergence.

Usage:
    from app.services.privacy_service import sanitize_for_persistence
    clean_text = sanitize_for_persistence(raw_error_message)
"""
from app.services.redaction_service import redact_text, redact_dict


def sanitize_for_persistence(text: str) -> str:
    """Redact PII and secrets before writing to database or cache."""
    if not text:
        return text
    return redact_text(text)


def sanitize_for_logging(text: str) -> str:
    """Redact PII and secrets before emitting to logs."""
    if not text:
        return text
    return redact_text(text)


def sanitize_for_llm(text: str) -> str:
    """Redact PII and secrets before sending to AI models."""
    if not text:
        return text
    return redact_text(text)


def sanitize_for_report(text: str) -> str:
    """Redact PII and secrets before rendering in reports or emails."""
    if not text:
        return text
    return redact_text(text)


def sanitize_dict_for_persistence(data: dict | None) -> dict | None:
    """Recursively redact sensitive values before writing to database."""
    return redact_dict(data)
