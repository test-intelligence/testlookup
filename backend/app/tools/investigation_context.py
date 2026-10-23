"""Server-owned identity for ReAct investigation tools.

Tool arguments are model output and therefore never establish tenant scope.
The orchestrator binds this context before the executor starts and resets it
in ``finally`` so concurrent investigations cannot observe one another.

W10 (INT-01): the integration settings a tool uses (Splunk, OpenShift) are
resolved by the server BEFORE any HTTP and before the model call, and reach
the tools through ONE accessor, ``runtime_integrations()``, from either of two
binding points:

* ``InvestigationContext.integrations`` -- the ReAct path
  (``services/agent.py`` ``run_triage_agent``);
* a standalone ContextVar, ``bind_runtime_integrations(cfg)`` -- the deep
  workflow's log stage (``LogIntelligenceAgent.run``), which calls two Splunk
  tools server-side with no InvestigationContext bound.

``None`` from the accessor means nothing is bound: each tool then falls back
to ``IntegrationRuntimeConfig.from_settings(<its own module's settings>)``.
The config carries tokens in process memory only (``repr=False``); it never
enters LangGraph state or a checkpoint.

The server-side lookups' results are memoised per run here too
(``set_prefetched_observations``), so a tool the model calls again answers
from the memo instead of a second HTTP call.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Optional

if TYPE_CHECKING:  # no import cycle: the resolver imports nothing from tools
    from app.services.integration_config_service import IntegrationRuntimeConfig


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
    # W10 INT-01: the resolved runtime integration config (tokens repr=False).
    # Last, with a default, so every existing constructor call keeps working.
    integrations: "IntegrationRuntimeConfig | None" = None


_CONTEXT: ContextVar[InvestigationContext | None] = ContextVar(
    "triage_investigation_context", default=None,
)

_RUNTIME_INTEGRATIONS: ContextVar["IntegrationRuntimeConfig | None"] = ContextVar(
    "runtime_integration_config", default=None,
)

_PREFETCHED: ContextVar[Mapping[str, str] | None] = ContextVar(
    "server_side_lookup_observations", default=None,
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


# ── Runtime integration config (W10 INT-01) ─────────────────────────────────


def bind_runtime_integrations(cfg: "IntegrationRuntimeConfig | None") -> Token:
    """Bind ``cfg`` for code that runs with no InvestigationContext (the log
    stage). Reset with ``reset_runtime_integrations`` in ``finally``."""
    return _RUNTIME_INTEGRATIONS.set(cfg)


def reset_runtime_integrations(token: Token) -> None:
    try:
        _RUNTIME_INTEGRATIONS.reset(token)
    except Exception:  # pragma: no cover - defensive context cleanup
        _RUNTIME_INTEGRATIONS.set(None)


def runtime_integrations() -> "IntegrationRuntimeConfig | None":
    """The bound InvestigationContext's integrations when set, else the
    standalone ContextVar, else None (callers then use ``from_settings``)."""
    context = _CONTEXT.get()
    if context is not None and context.integrations is not None:
        return context.integrations
    return _RUNTIME_INTEGRATIONS.get()


# ── Server-side lookup memo (W10 D-4) ───────────────────────────────────────


def set_prefetched_observations(observations: Mapping[str, str]) -> Token:
    """tool name -> the server-side lookup's text, for this run only."""
    return _PREFETCHED.set(MappingProxyType(dict(observations)))


def prefetched_observation(tool_name: str) -> Optional[str]:
    observations = _PREFETCHED.get()
    if not observations:
        return None
    return observations.get(tool_name)


def reset_prefetched_observations(token: Token) -> None:
    try:
        _PREFETCHED.reset(token)
    except Exception:  # pragma: no cover - defensive context cleanup
        _PREFETCHED.set(None)
