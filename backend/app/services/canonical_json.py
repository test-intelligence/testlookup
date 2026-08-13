"""Strict canonical JSON shared by signed and hashed AI evidence contracts."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import date, datetime
from typing import Any


def normalize_canonical_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON values must not contain NaN or infinity")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: normalize_canonical_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_canonical_json(item) for item in value]
    raise TypeError(f"unsupported canonical JSON value type: {type(value).__name__}")


def canonical_json_bytes(value: Any, *, max_bytes: int | None = None) -> bytes:
    payload = json.dumps(
        normalize_canonical_json(value),
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if max_bytes is not None and len(payload) > max_bytes:
        raise ValueError(f"canonical JSON exceeds the {max_bytes}-byte safety limit")
    return payload


def stable_json_sha256(value: Any, *, max_bytes: int | None = None) -> str:
    return hashlib.sha256(canonical_json_bytes(value, max_bytes=max_bytes)).hexdigest()
