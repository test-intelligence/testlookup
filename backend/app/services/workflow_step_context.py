"""Task-local identity for a capability instantiated as a workflow step."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterable, Iterator

_STEP_ID: ContextVar[str | None] = ContextVar("workflow_step_id", default=None)
_ALLOWED_TOOLS: ContextVar[frozenset[str] | None] = ContextVar(
    "workflow_allowed_tools", default=None
)


def runtime_stage_name(default: str) -> str:
    return _STEP_ID.get() or default


def tool_allowed(tool_name: str) -> bool:
    """Return whether the active frozen step config permits a tool."""
    allowed = _ALLOWED_TOOLS.get()
    return allowed is None or tool_name in allowed


@contextmanager
def workflow_step_scope(
    step_id: str,
    *,
    allowed_tools: Iterable[str] | None = None,
) -> Iterator[None]:
    token = _STEP_ID.set(step_id)
    tools_token = _ALLOWED_TOOLS.set(
        None if allowed_tools is None else frozenset(allowed_tools)
    )
    try:
        yield
    finally:
        _ALLOWED_TOOLS.reset(tools_token)
        _STEP_ID.reset(token)
