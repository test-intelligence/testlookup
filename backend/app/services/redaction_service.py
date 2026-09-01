"""
Unified redaction service — single source of truth for secret + PII scrubbing.

Two modes of redaction:
  1. **Key-based** (`redact_value`, `redact_dict`): redacts dict values whose
     keys match known sensitive names.  Used by audit dashboards, API responses.
  2. **Pattern-based** (`redact_text`): regex-scrubs free-form strings (stack
     traces, error messages, log excerpts) before LLM ingestion or persistence.

PR-2: Extended to detect PII patterns (email, phone, SSN, credit card, IP)
in addition to secrets (tokens, passwords, API keys).
"""
from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger("services.redaction")

# ── Sensitive key names (union of audit + prompt sets) ───────────────────────
# Matched after lowercasing and normalising hyphens → underscores.

SENSITIVE_KEYS: frozenset[str] = frozenset({
    # Secrets
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "api_token", "authorization", "auth_token", "access_token",
    "refresh_token", "private_key", "secret_key", "credential",
    "credentials", "cookie", "hashed_password", "jwt", "bearer",
    "smtp_password", "minio_secret_key", "webhook_secret",
    # PII (PR-2)
    "email", "email_address", "phone", "phone_number", "mobile",
    "ssn", "social_security", "social_security_number",
    "credit_card", "card_number", "cc_number",
    "dob", "date_of_birth", "address", "street_address",
    "national_id", "passport_number", "drivers_license",
})

# ── Regex patterns for free-form text scrubbing ─────────────────────────────

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer tokens
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]{20,}", re.IGNORECASE), r"\1[REDACTED]"),
    # Authorization headers
    (re.compile(r"(Authorization:\s*)[^\s\n]{10,}", re.IGNORECASE), r"\1[REDACTED]"),
    # Cookies frequently contain session credentials and must be removed even
    # when their values do not resemble long random tokens.
    (re.compile(r"((?:Set-)?Cookie:\s*)[^\r\n]+", re.IGNORECASE), r"\1[REDACTED]"),
    # API keys (common formats)
    (re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[^\s'\",]{4,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    # Generic tokens
    (re.compile(r"(token\s*[:=]\s*)['\"]?[^\s'\",]{4,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    # Passwords
    (re.compile(r"(password\s*[:=]\s*)['\"]?[^\s'\",]{4,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    # Secret keys
    (re.compile(r"(secret[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9\-_]{10,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    # AWS-style keys
    (re.compile(r"(AKIA[A-Z0-9]{16})"), "[REDACTED_AWS_KEY]"),
    # JWT tokens (three base64 segments separated by dots)
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), "[REDACTED_JWT]"),
    # Connection strings with passwords
    (re.compile(r"(://[^:]+:)[^@]{4,}(@)"), r"\1[REDACTED]\2"),
    # ── PII patterns (PR-2) ────────────────────────────────────────────────
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    # Phone numbers (US/international formats)
    (re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    # SSN (xxx-xx-xxxx or xxx xx xxxx)
    (re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"), "[REDACTED_SSN]"),
    # Credit card numbers (4 groups of 4 digits)
    (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"), "[REDACTED_CC]"),
    # IPv4 addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_IP]"),
]

# Compiled pattern for quick key-name content check (used for string values
# whose *key* didn't match but whose *content* might contain secrets).
_SENSITIVE_CONTENT_PATTERN = re.compile(
    r"(password|secret|token|api_key|credential|private_key|bearer|authorization)",
    re.IGNORECASE,
)

# Redaction placeholder — consistent across all paths.
REDACTED = "[REDACTED]"

_MAX_RECURSION_DEPTH = 10


# ── Public API ───────────────────────────────────────────────────────────────

def redact_text(text: str) -> str:
    """Redact secret-like patterns from free-form text.

    Safe for error messages, stack traces, and log excerpts.
    """
    if not text:
        return text
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_value(key: str, value: Any) -> Any:
    """Redact a single value if its key matches known sensitive names.

    Non-string values with sensitive keys are fully replaced.
    String values with non-sensitive keys are pattern-scrubbed.
    """
    if value is None:
        return None
    key_lower = key.lower().replace("-", "_")
    if key_lower in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        # Pattern-scrub even if key doesn't match
        if _SENSITIVE_CONTENT_PATTERN.search(value):
            return redact_text(value)
        return redact_text(value)
    return value


def redact_dict(data: dict | None, *, _depth: int = 0) -> dict | str | None:
    """Recursively redact sensitive values in a dict.

    - Keys matching SENSITIVE_KEYS → value replaced with REDACTED.
    - String values → run through ``redact_text`` for pattern-based scrubbing.
    - Nested dicts and lists are handled recursively (max depth 10).
    """
    if not data:
        return data
    if _depth >= _MAX_RECURSION_DEPTH:
        # Fail closed. A depth cap must never return an unredacted subtree.
        logger.warning(
            "redact_dict: max recursion depth %d reached — redacting subtree",
            _MAX_RECURSION_DEPTH,
        )
        return REDACTED

    result: dict[str, Any] = {}
    for key, value in data.items():
        key_lower = key.lower().replace("-", "_")
        if key_lower in SENSITIVE_KEYS:
            result[key] = REDACTED
        elif isinstance(value, str):
            result[key] = redact_text(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value, _depth=_depth + 1)
        elif isinstance(value, list):
            result[key] = [_redact_nested(item, _depth + 1) for item in value[:100]]
        else:
            result[key] = value
    return result


def _redact_nested(value: Any, depth: int) -> Any:
    """Redact nested dict/list values and replace capped subtrees."""
    if depth >= _MAX_RECURSION_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return redact_dict(value, _depth=depth)
    if isinstance(value, list):
        return [_redact_nested(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_for_llm(text: str) -> str:
    """Convenience wrapper: redact text before sending to an LLM."""
    return redact_text(text)
