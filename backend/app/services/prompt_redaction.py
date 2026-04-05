"""
Central redaction utility for LLM-bound context.

Redacts tokens, passwords, authorization headers, API keys, and other
secret-like patterns before prompt assembly or persistence.

Used by: analysis_agent, summary_agent, release_risk_agent, conversation agent.
"""
import re
import logging

logger = logging.getLogger("services.prompt_redaction")

# Patterns that match secret-like values
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Bearer tokens
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]{20,}", re.IGNORECASE), r"\1[REDACTED]"),
    # Authorization headers
    (re.compile(r"(Authorization:\s*)[^\s\n]{10,}", re.IGNORECASE), r"\1[REDACTED]"),
    # API keys (common formats)
    (re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9\-_]{16,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
    # Generic tokens
    (re.compile(r"(token\s*[:=]\s*)['\"]?[A-Za-z0-9\-_\.]{20,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
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
]

# Keys in dicts that should have their values redacted
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth_token", "access_token", "refresh_token",
    "private_key", "secret_key", "credentials", "cookie",
})


def redact_text(text: str) -> str:
    """
    Redact secret-like patterns from free-form text.
    Safe for error messages, stack traces, and log excerpts.
    """
    if not text:
        return text

    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_dict(data: dict, depth: int = 0) -> dict:
    """
    Recursively redact sensitive values in a dict.
    Keys matching _SENSITIVE_KEYS have their values replaced.
    String values are passed through redact_text.
    """
    if depth > 10:
        return data

    result = {}
    for key, value in data.items():
        key_lower = key.lower().replace("-", "_")
        if key_lower in _SENSITIVE_KEYS:
            result[key] = "[REDACTED]"
        elif isinstance(value, str):
            result[key] = redact_text(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value, depth + 1)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(item, depth + 1) if isinstance(item, dict)
                else redact_text(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def redact_for_llm(text: str) -> str:
    """
    Convenience wrapper: redact text specifically before sending to an LLM.
    Identical to redact_text but named for clarity at call sites.
    """
    return redact_text(text)
