"""Per-tool correlation shared with the REST adapter, without caller credentials."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")


@dataclass
class OperationContext:
    request_id: str
    traceparent: str | None
    backend_failed: bool = False
    backend_error_seen: bool = False


_OPERATION: ContextVar[OperationContext | None] = ContextVar("mcp_tool_operation", default=None)


def validated_request_id(value: object) -> str | None:
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


def validated_traceparent(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TRACEPARENT.fullmatch(value)
    if match is None or int(match[1], 16) == 0 or int(match[2], 16) == 0:
        return None
    return value


@contextmanager
def operation_context(request_id: str, traceparent: str | None = None) -> Iterator[OperationContext]:
    identity = validated_request_id(request_id)
    if identity is None or (traceparent is not None and validated_traceparent(traceparent) is None):
        raise ValueError("Invalid operation correlation context")
    operation = OperationContext(identity, traceparent)
    token = _OPERATION.set(operation)
    try:
        yield operation
    finally:
        _OPERATION.reset(token)


def outgoing_operation_headers() -> dict[str, str]:
    operation = _OPERATION.get()
    if operation is None:
        return {}
    headers = {"X-Request-ID": operation.request_id}
    if operation.traceparent is not None:
        headers["traceparent"] = operation.traceparent
    return headers


def mark_backend_failure() -> None:
    """Record a final failed REST operation, including errors rendered as tool text."""
    operation = _OPERATION.get()
    if operation is not None:
        operation.backend_failed = True


def note_backend_error() -> None:
    """Remember a rejected REST request without declaring a handled fallback bad."""
    operation = _OPERATION.get()
    if operation is not None:
        operation.backend_error_seen = True
