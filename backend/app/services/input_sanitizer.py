"""
Input Sanitizer Service — Phase 4 Safety & HITL.

Centralised sanitisation for free-text inputs (logs, traces, error messages,
service names) before they reach LLM tool calls or prompts.

Responsibilities:
  - Detect and neutralise prompt-injection patterns
  - Strip control characters and dangerous shell/query metacharacters
  - Enforce length limits per field type
  - Redact sensitive data patterns (tokens, passwords, secrets)
"""
from __future__ import annotations

import logging
import re
logger = logging.getLogger("services.input_sanitizer")

# ── Length budgets (characters) ──────────────────────────────────────────────

MAX_SERVICE_NAME = 128
MAX_ERROR_MESSAGE = 4_000
MAX_STACK_TRACE = 16_000
MAX_FREE_TEXT = 8_000
MAX_QUERY_PARAM = 256

# ── Prompt-injection detection patterns ──────────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # Direct instruction override attempts
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above)", re.I),
    re.compile(r"you\s+are\s+now\s+a", re.I),
    re.compile(r"act\s+as\s+(if\s+you\s+are|a|an)\s+", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    # Delimiter-based injection
    re.compile(r"```\s*(system|assistant|user)\s*\n", re.I),
    re.compile(r"<\s*/?\s*(system|instruction|prompt)\s*>", re.I),
    re.compile(r"\[INST\]", re.I),
    re.compile(r"<<\s*SYS\s*>>", re.I),
    # Role hijacking
    re.compile(r"(human|user|assistant)\s*:\s*", re.I),
]

# ── Sensitive data patterns (redacted before LLM) ───────────────────────────

_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer / API tokens
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I), r"\1[REDACTED]"),
    (re.compile(r"(Authorization:\s*)[^\s]+", re.I), r"\1[REDACTED]"),
    # API keys (common formats)
    (re.compile(r"(api[_-]?key\s*[=:]\s*)[^\s,;\"']+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(token\s*[=:]\s*)[^\s,;\"']+", re.I), r"\1[REDACTED]"),
    # Passwords
    (re.compile(r"(password\s*[=:]\s*)[^\s,;\"']+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(secret\s*[=:]\s*)[^\s,;\"']+", re.I), r"\1[REDACTED]"),
    # Connection strings with embedded credentials
    (re.compile(r"(://[^:]+:)[^@]+(@)", re.I), r"\1[REDACTED]\2"),
    # AWS-style keys
    (re.compile(r"(AKIA[0-9A-Z]{16})", re.I), "[REDACTED_AWS_KEY]"),
    # JWT tokens (three base64 segments separated by dots)
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[REDACTED_JWT]"),
]

# ── Shell/query metacharacter stripping ──────────────────────────────────────

# For service names and identifiers: allow only safe characters
_SAFE_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9\-_./: ]")

# For Splunk query parameters: strip injection-enabling characters
_SPLUNK_UNSAFE_RE = re.compile(r"[|`;$(){}[\]\\]")

# Control characters (except newline/tab which are legitimate in stack traces)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── Public API ───────────────────────────────────────────────────────────────


def sanitize_service_name(value: str) -> str:
    """Sanitise a service/pod/namespace name for use in external queries."""
    value = value.strip()[:MAX_SERVICE_NAME]
    value = _SAFE_IDENTIFIER_RE.sub("", value)
    return value


def sanitize_error_message(value: str) -> str:
    """Sanitise an error message before it reaches the LLM prompt."""
    value = _truncate(value, MAX_ERROR_MESSAGE)
    value = _strip_control_chars(value)
    value = _redact_sensitive(value)
    value = _neutralise_injections(value)
    return value


def sanitize_stack_trace(value: str) -> str:
    """Sanitise a stack trace before it reaches the LLM prompt."""
    value = _truncate(value, MAX_STACK_TRACE)
    value = _strip_control_chars(value)
    value = _redact_sensitive(value)
    value = _neutralise_injections(value)
    return value


def sanitize_free_text(value: str, max_length: int = MAX_FREE_TEXT) -> str:
    """Sanitise arbitrary free-text input (log excerpts, descriptions, etc.)."""
    value = _truncate(value, max_length)
    value = _strip_control_chars(value)
    value = _redact_sensitive(value)
    value = _neutralise_injections(value)
    return value


def sanitize_query_param(value: str) -> str:
    """Sanitise a query parameter for external API calls (Splunk, etc.)."""
    value = value.strip()[:MAX_QUERY_PARAM]
    value = _SPLUNK_UNSAFE_RE.sub("", value)
    value = _strip_control_chars(value)
    return value


def detect_injection(value: str) -> bool:
    """Return True if the text contains likely prompt-injection patterns."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(value):
            return True
    return False


def sanitize_tool_output(value: str, max_length: int = MAX_FREE_TEXT) -> str:
    """
    Sanitise tool output before it propagates into action agents.
    Strips injections and sensitive data that may have leaked from external systems.
    """
    value = _truncate(value, max_length)
    value = _strip_control_chars(value)
    value = _redact_sensitive(value)
    value = _neutralise_injections(value)
    return value


# ── Internal helpers ─────────────────────────────────────────────────────────


def _truncate(value: str, max_length: int) -> str:
    if len(value) > max_length:
        return value[:max_length] + f"\n... [truncated at {max_length} chars]"
    return value


def _strip_control_chars(value: str) -> str:
    return _CONTROL_CHARS_RE.sub("", value)


def _redact_sensitive(value: str) -> str:
    for pattern, replacement in _SENSITIVE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _neutralise_injections(value: str) -> str:
    """
    Replace detected injection patterns with a safe marker.
    We do not silently drop content — the marker makes it visible
    that something was neutralised, aiding debugging.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(value):
            logger.warning("Prompt injection pattern detected and neutralised")
            value = pattern.sub("[SANITIZED_INPUT]", value)
    return value
