"""Shared privacy normalization for persisted test-result failure evidence."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from app.services.privacy_service import sanitize_for_persistence
from app.services.redaction_service import REDACTED, SENSITIVE_KEYS


_FAILURE_TEXT_FIELDS = ("error_message", "stack_trace")
_STRUCTURED_JSON_FIELDS = frozenset({"original_data", "payload"})
_IDENTITY_STRING_LIMITS = {
    "build_number": 100,
    "class_name": 500,
    "event_type": 50,
    "project_id": 255,
    "run_id": 255,
    "session_id": 255,
    "status": 50,
    "suite_name": 500,
    "test_case_id": 255,
    "test_name": 1_000,
    "type": 50,
}
_IDENTITY_INTEGER_LIMITS = {
    "duration_ms": (0, 2_147_483_647),
    "timestamp_ms": (0, 9_007_199_254_740_991),
    "total_tests": (0, 2_147_483_647),
}
_SENSITIVE_EVENT_KEYS = SENSITIVE_KEYS | frozenset({
    "proxy_authorization",
    "x_api_key",
    "x_api_token",
    "x_auth_token",
    "x_webhook_secret",
})
_MAX_NESTING_DEPTH = 10
_MAX_COLLECTION_ITEMS = 1_000
_MAX_KEY_LENGTH = 255
_MAX_TOTAL_NODES = 10_000
_MAX_TOTAL_TEXT_CHARS = 1_000_000
_MAX_TEXT_LENGTH = 100_000
LIVE_SANITIZATION_VERSION = 1
LIVE_SANITIZATION_VERSION_FIELD = "_m11_sanitization_version"

_SECRET_ASSIGNMENT_RE = re.compile(
    r'''(?i)\b(password|passwd|pwd|token|api[_-]?key|secret(?:[_-]?key)?|'''
    r'''credentials?)\s*([:=])\s*'''
    r'''(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)'''
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(proxy-authorization|authorization)(\s*:\s*)"
    r"(basic|bearer)(\s+)[^\s,;]+"
)
_AUTH_ASSIGNMENT_RE = re.compile(
    r'''(?i)\b(proxy[_-]?authorization|authorization)\s*([:=])\s*'''
    r'''(?!(?:basic|bearer)\b)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)'''
)
_IPV6_RE = re.compile(
    r"(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])",
    re.IGNORECASE,
)
_REDACTION_PLACEHOLDERS = frozenset({
    REDACTED,
    "[REDACTED_AWS_KEY]",
    "[REDACTED_CC]",
    "[REDACTED_EMAIL]",
    "[REDACTED_IP]",
    "[REDACTED_JWT]",
    "[REDACTED_PHONE]",
    "[REDACTED_SSN]",
})


def _normalized_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def validate_live_identifier(field: str, value: object) -> str:
    """Return an identifier safe for Redis keys/envelopes or reject it."""
    limit = _IDENTITY_STRING_LIMITS[field]
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"invalid {field}")
    if not value.isprintable():
        raise ValueError(f"invalid {field}")
    return value


def _sanitize_text(
    value: str,
    *,
    depth: int,
    budget: list[int],
    require_json: bool = False,
) -> str:
    if len(value) > _MAX_TEXT_LENGTH:
        return REDACTED

    stripped = value.strip()
    if stripped.startswith("[REDACTED"):
        if stripped in _REDACTION_PLACEHOLDERS:
            return _take_text_budget(value, budget)
        return REDACTED
    if stripped.startswith(("{", "[")):
        try:
            structured = json.loads(value)
        except (TypeError, ValueError):
            # JSON-looking evidence that cannot be parsed cannot be proven safe;
            # quoted secret keys also bypass free-text key/value regexes.
            return REDACTED
        else:
            if isinstance(structured, (dict, list)):
                serialized = json.dumps(
                    _sanitize_nested(
                        structured,
                        depth=depth + 1,
                        budget=budget,
                    ),
                    separators=(",", ":"),
                    sort_keys=True,
                )
                return _take_text_budget(serialized, budget)
    elif require_json:
        return REDACTED
    safe = _redact_free_text(value)
    return _take_text_budget(safe, budget)


def _redact_free_text(value: str) -> str:
    safe = _AUTH_HEADER_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}"
            f"{match.group(4)}{REDACTED}"
        ),
        value,
    )
    safe = _AUTH_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        safe,
    )
    safe = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        safe,
    )
    safe = sanitize_for_persistence(safe)
    safe = _IPV6_RE.sub(REDACTED, safe)
    return safe


def _take_text_budget(value: str, budget: list[int]) -> str:
    if len(value) > budget[1]:
        return REDACTED
    budget[1] -= len(value)
    return value


def _sanitize_nested(value: Any, *, depth: int, budget: list[int]) -> Any:
    """Recursively sanitize JSON-like values within strict depth/size budgets."""
    if budget[0] <= 0:
        return REDACTED
    budget[0] -= 1
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if -(2**63) <= value <= (2**63 - 1) else REDACTED
    if isinstance(value, float):
        return value if math.isfinite(value) else REDACTED
    if depth >= _MAX_NESTING_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return _sanitize_text(value, depth=depth, budget=budget)
    if isinstance(value, Mapping):
        if len(value) > min(_MAX_COLLECTION_ITEMS, budget[0]):
            return {"_redacted": REDACTED}
        result: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = _sanitize_key(key, depth=depth, budget=budget)
            if key_text in result:
                return {"_redacted": REDACTED}
            if _normalized_key(key) in _SENSITIVE_EVENT_KEYS:
                result[key_text] = REDACTED
            else:
                result[key_text] = _sanitize_nested(
                    nested,
                    depth=depth + 1,
                    budget=budget,
                )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > min(_MAX_COLLECTION_ITEMS, budget[0]):
            return [REDACTED]
        return [
            _sanitize_nested(item, depth=depth + 1, budget=budget)
            for item in value
        ]
    return REDACTED


def _sanitize_key(key: object, *, depth: int, budget: list[int]) -> str:
    key_text = str(key)
    if len(key_text) > _MAX_KEY_LENGTH:
        return "_redacted"
    return _sanitize_text(key_text, depth=depth, budget=budget)


def _sanitize_identity(field: str, value: Any, budget: list[int]) -> Any:
    if value is None:
        return None
    if field in _IDENTITY_INTEGER_LIMITS:
        minimum, maximum = _IDENTITY_INTEGER_LIMITS[field]
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
            else None
        )
    limit = _IDENTITY_STRING_LIMITS[field]
    return _take_text_budget(value[:limit], budget) if isinstance(value, str) else REDACTED


def sanitize_test_result_payload(payload: object) -> dict[str, Any]:
    """Return a sanitized copy suitable for Redis, Mongo, or SQL persistence.

    Failure evidence crosses several ingestion transports and is often applied
    at more than one boundary so pre-deployment buffers and archive replays are
    safe too. The shared redactor is idempotent, making those repeated checks
    intentional and harmless.
    """
    if not isinstance(payload, Mapping):
        return {"_redacted": REDACTED}
    if len(payload) > _MAX_COLLECTION_ITEMS:
        compact: dict[str, Any] = {"_redacted": REDACTED}
        budget = [_MAX_TOTAL_NODES, _MAX_TOTAL_TEXT_CHARS]
        for field in _IDENTITY_STRING_LIMITS:
            if field in payload:
                compact[field] = _sanitize_identity(field, payload[field], budget)
        for field in _IDENTITY_INTEGER_LIMITS:
            if field in payload:
                compact[field] = _sanitize_identity(field, payload[field], budget)
        for field in _FAILURE_TEXT_FIELDS:
            if field in payload:
                compact[field] = REDACTED if payload[field] is not None else None
        return compact

    safe: dict[str, Any] = {}
    budget = [_MAX_TOTAL_NODES, _MAX_TOTAL_TEXT_CHARS]
    for raw_field, value in payload.items():
        field = _sanitize_key(raw_field, depth=0, budget=budget)
        if field in safe:
            return {"_redacted": REDACTED}
        normalized_field = _normalized_key(raw_field)
        if normalized_field in _SENSITIVE_EVENT_KEYS:
            safe[field] = REDACTED if value is not None else None
        elif field in _FAILURE_TEXT_FIELDS:
            if value is None:
                safe[field] = None
            elif isinstance(value, str):
                safe[field] = _sanitize_text(value, depth=0, budget=budget)
            else:
                # SQL failure columns are text. Structured values are malformed,
                # and serializing them risks creating a representation that the
                # free-text redactor does not understand, so fail closed.
                safe[field] = REDACTED
        elif field in _STRUCTURED_JSON_FIELDS:
            safe[field] = (
                _sanitize_text(
                    value,
                    depth=0,
                    budget=budget,
                    require_json=True,
                )
                if isinstance(value, str)
                else REDACTED
            )
        elif field in _IDENTITY_STRING_LIMITS or field in _IDENTITY_INTEGER_LIMITS:
            # These fields define routing, identity, or fingerprints. File and
            # live ingestion intentionally preserve them identically.
            safe[field] = _sanitize_identity(field, value, budget)
        else:
            safe[field] = _sanitize_nested(value, depth=0, budget=budget)
    return safe
