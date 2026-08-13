"""Fail-closed, bounded sanitization for durable AI evidence payloads."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.services.redaction_service import REDACTED, SENSITIVE_KEYS, redact_text

MAX_PERSISTED_PAYLOAD_DEPTH = 16
MAX_PERSISTED_PAYLOAD_ITEMS = 20_000
MAX_PERSISTED_STRING_CHARS = 10_000
_SENSITIVE_TEXT_FIELDS = frozenset({
    "headers", "raw_headers", "request_headers", "response_headers",
    "set_cookie", "cookie_header",
})


@dataclass
class SanitizationStats:
    redacted_strings: int = 0
    truncated_strings: int = 0
    omitted_items: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "redacted_strings": self.redacted_strings,
            "truncated_strings": self.truncated_strings,
            "omitted_items": self.omitted_items,
        }


def sanitize_reference_text(value: str, *, limit: int) -> tuple[str, bool, bool]:
    """Redact text and remove credentials/query/fragment from HTTP(S) references."""
    original = str(value)
    redacted = redact_text(original)
    try:
        parsed = urlsplit(redacted)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
            host = parsed.hostname
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            redacted = urlunsplit((parsed.scheme.lower(), host, parsed.path, "", ""))
    except ValueError:
        # Malformed URL components are handled as bounded, non-actionable text.
        pass
    truncated = len(redacted) > limit
    return redacted[:limit], redacted != original, truncated


def sanitize_persistence_payload(value: Any) -> tuple[Any, SanitizationStats]:
    """Recursively redact and bound a JSON-like payload before persistence."""
    stats = SanitizationStats()
    remaining = MAX_PERSISTED_PAYLOAD_ITEMS

    def walk(item: Any, *, depth: int, field_name: str = "") -> Any:
        nonlocal remaining
        if depth > MAX_PERSISTED_PAYLOAD_DEPTH:
            stats.omitted_items += 1
            return "[TRUNCATED_DEPTH]"
        if item is None or isinstance(item, (bool, int, float, uuid.UUID, datetime, date)):
            return item
        if isinstance(item, str):
            if field_name.lower().replace("-", "_") in _SENSITIVE_TEXT_FIELDS:
                stats.redacted_strings += 1
                return REDACTED
            if any(token in field_name.lower() for token in ("uri", "url", "reference")):
                safe, changed, truncated = sanitize_reference_text(
                    item, limit=MAX_PERSISTED_STRING_CHARS
                )
            else:
                redacted = redact_text(item)
                changed = redacted != item
                truncated = len(redacted) > MAX_PERSISTED_STRING_CHARS
                safe = redacted[:MAX_PERSISTED_STRING_CHARS]
            stats.redacted_strings += int(changed)
            stats.truncated_strings += int(truncated)
            return safe
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise TypeError("durable evidence object keys must be strings")
                if remaining <= 0:
                    stats.omitted_items += 1
                    continue
                remaining -= 1
                normalized_key = key.lower().replace("-", "_")
                if normalized_key in SENSITIVE_KEYS:
                    result[key] = REDACTED
                    stats.redacted_strings += 1
                else:
                    result[key] = walk(nested, depth=depth + 1, field_name=key)
            return result
        if isinstance(item, (list, tuple)):
            result: list[Any] = []
            for nested in item:
                if remaining <= 0:
                    stats.omitted_items += 1
                    continue
                remaining -= 1
                result.append(walk(nested, depth=depth + 1, field_name=field_name))
            return result
        raise TypeError(f"unsupported durable evidence type: {type(item).__name__}")

    return walk(value, depth=0), stats
