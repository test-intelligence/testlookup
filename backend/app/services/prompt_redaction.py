"""
Prompt redaction — thin re-export layer over the unified redaction_service.

Kept for backward compatibility: existing callers import from here.
All logic lives in ``app.services.redaction_service``.
"""
from app.services.redaction_service import (  # noqa: F401
    SENSITIVE_KEYS as _SENSITIVE_KEYS,
    redact_dict,
    redact_for_llm,
    redact_text,
)

__all__ = ["redact_text", "redact_dict", "redact_for_llm", "_SENSITIVE_KEYS"]
