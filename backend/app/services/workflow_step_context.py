"""Task-local identity for a capability instantiated as a workflow step."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_STEP_ID: ContextVar[str | None] = ContextVar("workflow_step_id", default=None)


def runtime_stage_name(default: str) -> str:
    return _STEP_ID.get() or default


@contextmanager
def workflow_step_scope(step_id: str) -> Iterator[None]:
    token = _STEP_ID.set(step_id)
    try:
        yield
    finally:
        _STEP_ID.reset(token)
