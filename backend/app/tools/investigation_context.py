"""Server-owned identity for ReAct investigation tools.

Tool arguments are model output and therefore never establish tenant scope.
The orchestrator binds this context before the executor starts and resets it
in ``finally`` so concurrent investigations cannot observe one another.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class InvestigationContext:
    project_id: str
    run_id: str
    test_case_id: str
    test_name: str
    test_fingerprint: str | None = None
    service_name: str | None = None
    timestamp: str | None = None
    ocp_pod_name: str | None = None
    ocp_namespace: str | None = None


_CONTEXT: ContextVar[InvestigationContext | None] = ContextVar(
    "triage_investigation_context", default=None,
)


def set_investigation_context(context: InvestigationContext) -> Token:
    return _CONTEXT.set(context)


def get_investigation_context() -> InvestigationContext | None:
    return _CONTEXT.get()


def reset_investigation_context(token: Token) -> None:
    try:
        _CONTEXT.reset(token)
    except Exception:  # pragma: no cover - defensive context cleanup
        _CONTEXT.set(None)
