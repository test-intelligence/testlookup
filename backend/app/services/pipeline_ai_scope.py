"""The AI configuration a pipeline froze at its start (W04 AIC-02 / AIC-03).

Settings > AI holds two numbers every pipeline stage must agree on: the
confidence threshold (what triage, gap detection, the router's
``threshold_check`` and the ticket policy compare against) and the LLM call
timeout. Before W04 each reader resolved them on its own: some from the stored
setting, most from the environment, several as import-time constants. A plan
could say "triage at 80" while the stage triaged at 95, and a resume after an
admin edit replayed a different number than the run started with.

This module is the one place those values live for the duration of a pipeline:

* :func:`build_ai_config_snapshot` freezes them ONCE, in ``_create_pipeline_run``,
  from the effective config it already resolves with ``fresh=True``. The
  snapshot is persisted in ``execution_metadata["ai_config_snapshot"]`` so a
  same-id resume replays exactly the same numbers.
* :func:`pipeline_ai_scope` publishes the snapshot through a ``ContextVar`` for
  the whole graph invocation (the mechanism of ``cost_budget_scope``): every
  task the graph spawns inherits it, so helpers with no ``state`` in hand
  (``confidence_gate.check_confidence``, ``get_llm``) still see the frozen
  values. Outside a pipeline the scope is empty and callers keep their live
  behaviour (the resolver's 60 s cache).
* :func:`effective_llm_timeout` is the single timeout rule: the frozen global
  value, tightened by an EXPLICIT project agent override, tightened by the
  site's own cap. Lower layers only ever tighten.

``settings.AI_TIMEOUT_SECONDS`` may be read here (the env default when nothing
is frozen); ``settings.AI_CONFIDENCE_THRESHOLD`` is not: its default comes from
``confidence_gate.env_default_threshold()`` so the threshold ratchet
(``tests/regression/test_confidence_threshold_reads_go_through_the_gate.py``)
stays at its allowlist.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Optional, TypedDict

import structlog

from app.core.config import settings
from app.services.confidence_gate import (
    SOURCE_AI_CONFIG,
    SOURCE_ENV_DEFAULT,
    env_default_threshold,
    normalize_threshold,
)

logger = structlog.get_logger("services.pipeline_ai_scope")

SNAPSHOT_SCHEMA = 1
ORIGIN_PIPELINE_START = "pipeline_start"
ORIGIN_RESUMED_LEGACY = "resumed_legacy"
_VALID_SOURCES = (SOURCE_AI_CONFIG, SOURCE_ENV_DEFAULT)
#: The agent-config ``source`` of a project-stored row (``agent_config_service.serialize``).
_AGENT_SOURCE_PROJECT = "project"


class AIConfigSnapshot(TypedDict):
    schema: int                               # 1
    confidence_threshold: int                 # 0..100, the Settings > AI value in force at pipeline start
    confidence_threshold_source: str          # ai_config | env_default
    llm_timeout_seconds: int                  # the global effective timeout at pipeline start
    llm_timeout_source: str                   # ai_config | env_default
    agent_timeout_overrides: dict[str, int]   # agent_id -> seconds; only EXPLICIT project tightenings
    origin: str                               # "pipeline_start" | "resumed_legacy"
    frozen_at: str                            # ISO-8601 UTC


_AI_SCOPE: ContextVar[Optional[Mapping[str, Any]]] = ContextVar("pipeline_ai_scope", default=None)


@contextlib.contextmanager
def pipeline_ai_scope(snapshot: Optional[Mapping[str, Any]]) -> Iterator[None]:
    """Publish ``snapshot`` to every call made inside this block.

    A ContextVar, so it follows the graph into every task it spawns. ``None``
    (or a non-mapping) keeps whatever scope is already in force: a helper that
    has no snapshot must not switch off the one of the pipeline that called it.
    """
    if not isinstance(snapshot, Mapping):
        yield
        return
    token = _AI_SCOPE.set(snapshot)
    try:
        yield
    finally:
        _AI_SCOPE.reset(token)


def current_ai_scope() -> Optional[AIConfigSnapshot]:
    """The snapshot of the pipeline this call runs inside, else ``None``."""
    value = _AI_SCOPE.get()
    return value if isinstance(value, Mapping) else None  # type: ignore[return-value]


# -- coercion helpers --------------------------------------------------------------------------


def _positive_int(raw: Any) -> Optional[int]:
    """An ``int`` >= 1 (never a ``bool``), else ``None``."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        if raw != raw or raw < 1:  # NaN or sub-second
            return None
        return int(raw)
    if not isinstance(raw, int):
        return None
    return raw if raw >= 1 else None


def _env_default_timeout() -> int:
    return max(1, int(settings.AI_TIMEOUT_SECONDS))


def _source(raw: Any, fallback: str) -> str:
    return str(raw) if raw in _VALID_SOURCES else fallback


def agent_timeout_override(frozen_agent: Mapping[str, Any]) -> Optional[int]:
    """The per-agent timeout a PROJECT explicitly set, else ``None``.

    ``frozen_agent`` is a ``freeze_for_invocation(...)`` dict. The per-agent
    default is the capability SLA (``root_cause_analysis`` 120 s, ``summary``
    60 s ...), not a policy: applied blindly it would lower every analysis call
    from the 300 s global to 120 s on every install. So the term participates
    only when the project stored a row (``source == "project"``) AND its value
    differs from the capability default.
    """
    if not isinstance(frozen_agent, Mapping):
        return None
    if frozen_agent.get("source") != _AGENT_SOURCE_PROJECT:
        return None
    config = frozen_agent.get("config")
    if not isinstance(config, Mapping):
        return None
    value = _positive_int(config.get("timeout_seconds"))
    if value is None:
        return None
    agent_id = str(frozen_agent.get("agent_id") or config.get("agent_id") or "")
    if not agent_id:
        return None
    try:
        from app.services.agent_config_service import default_config  # noqa: PLC0415

        default_value = int(default_config(agent_id).timeout_seconds)
    except Exception:  # noqa: BLE001 — unknown capability: no default to compare with
        return None
    return value if value != default_value else None


def build_ai_config_snapshot(
    effective: Mapping[str, Any],
    resolved_agent_configs: Optional[Mapping[str, Mapping[str, Any]]] = None,
    *,
    origin: str = ORIGIN_PIPELINE_START,
) -> AIConfigSnapshot:
    """Freeze the confidence threshold and LLM timeout a pipeline runs with.

    ``effective`` is ``get_effective_ai_config(...)``'s dict; an unusable value
    in it falls back to the env default with source ``env_default`` (a junk
    stored value must not brick the pipeline). ``resolved_agent_configs`` maps
    agent id -> ``freeze_for_invocation`` dict; only explicit project
    tightenings become ``agent_timeout_overrides``.
    """
    effective = effective if isinstance(effective, Mapping) else {}
    threshold = normalize_threshold(effective.get("confidence_threshold"))
    if threshold is None:
        threshold, threshold_source = env_default_threshold(), SOURCE_ENV_DEFAULT
    else:
        threshold_source = _source(effective.get("confidence_threshold_source"), SOURCE_ENV_DEFAULT)

    timeout = _positive_int(effective.get("timeout_seconds"))
    if timeout is None:
        timeout, timeout_source = _env_default_timeout(), SOURCE_ENV_DEFAULT
    else:
        inferred = SOURCE_ENV_DEFAULT if timeout == _env_default_timeout() else SOURCE_AI_CONFIG
        timeout_source = _source(effective.get("timeout_seconds_source"), inferred)

    overrides: dict[str, int] = {}
    for agent_id, frozen in (resolved_agent_configs or {}).items():
        value = agent_timeout_override(frozen) if isinstance(frozen, Mapping) else None
        if value is not None and value <= timeout:
            overrides[str(agent_id)] = int(value)

    return AIConfigSnapshot(
        schema=SNAPSHOT_SCHEMA,
        confidence_threshold=int(threshold),
        confidence_threshold_source=threshold_source,
        llm_timeout_seconds=int(timeout),
        llm_timeout_source=timeout_source,
        agent_timeout_overrides=overrides,
        origin=str(origin or ORIGIN_PIPELINE_START),
        frozen_at=datetime.now(timezone.utc).isoformat(),
    )


def _snapshot_from(state: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if isinstance(state, Mapping):
        snapshot = state.get("ai_config_snapshot")
        if isinstance(snapshot, Mapping):
            return snapshot
    return None


def _override_for(snapshot: Optional[Mapping[str, Any]], agent_id: Optional[str]) -> Optional[int]:
    if not agent_id or not isinstance(snapshot, Mapping):
        return None
    overrides = snapshot.get("agent_timeout_overrides")
    if not isinstance(overrides, Mapping):
        return None
    return _positive_int(overrides.get(agent_id))


def effective_llm_timeout(
    state: Optional[Mapping[str, Any]] = None,
    *,
    agent_id: Optional[str] = None,
    cap: Optional[int] = None,
) -> int:
    """The one LLM-call timeout rule, in whole seconds (>= 1).

    base = ``state["llm_timeout_seconds"]`` (an int), else the scope's
    ``llm_timeout_seconds``, else the env default. Then tightened by the
    snapshot's (state's, else scope's) explicit override for ``agent_id``, then
    by ``cap``. Never raises.
    """
    scope = current_ai_scope()
    base: Optional[int] = None
    if isinstance(state, Mapping):
        base = _positive_int(state.get("llm_timeout_seconds"))
    if base is None and scope is not None:
        base = _positive_int(scope.get("llm_timeout_seconds"))
    if base is None:
        base = _env_default_timeout()

    override = _override_for(_snapshot_from(state), agent_id)
    if override is None:
        override = _override_for(scope, agent_id)
    if override is not None:
        base = min(base, override)

    capped = _positive_int(cap) if cap is not None else None
    if cap is not None:
        return max(1, min(base, capped if capped is not None else 1))
    return max(1, base)


def current_confidence_threshold() -> tuple[int, str]:
    """``(threshold, source)``: the frozen pipeline value, else the env default.

    Synchronous on purpose (``ml.classifier``, ``confidence_validation`` are
    sync). Inside a pipeline it is the value Settings > AI held when the
    pipeline started; outside one it is the configured default with source
    ``env_default`` (async callers that want the LIVE stored value use
    ``confidence_gate.resolve_confidence_threshold``).
    """
    scope = current_ai_scope()
    if scope is not None:
        value = normalize_threshold(scope.get("confidence_threshold"))
        if value is not None:
            return value, _source(scope.get("confidence_threshold_source"), SOURCE_ENV_DEFAULT)
    return env_default_threshold(), SOURCE_ENV_DEFAULT


def state_confidence_threshold(state: Optional[Mapping[str, Any]]) -> tuple[int, str]:
    """``(threshold, source)`` from a pipeline state, else :func:`current_confidence_threshold`.

    The state's ``confidence_threshold`` wins (it is the frozen value the plan
    was built with); the source comes from the state's snapshot when present.
    """
    if isinstance(state, Mapping):
        # Strict (an ``int`` in 0..100, never a string or bool): the compiled
        # router's projection (``workflow_compiler._frozen_confidence_threshold``)
        # applies the same rule, so a recorded decision and the branch taken
        # can never read the same state differently.
        raw = state.get("confidence_threshold")
        value = raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 100 else None
        if value is not None:
            snapshot = _snapshot_from(state) or current_ai_scope()
            if snapshot is not None and snapshot.get("confidence_threshold_source") in _VALID_SOURCES:
                return value, str(snapshot["confidence_threshold_source"])
            inferred = SOURCE_ENV_DEFAULT if value == env_default_threshold() else SOURCE_AI_CONFIG
            return value, inferred
    return current_confidence_threshold()


__all__ = [
    "AIConfigSnapshot",
    "ORIGIN_PIPELINE_START",
    "ORIGIN_RESUMED_LEGACY",
    "SNAPSHOT_SCHEMA",
    "agent_timeout_override",
    "build_ai_config_snapshot",
    "current_ai_scope",
    "current_confidence_threshold",
    "effective_llm_timeout",
    "pipeline_ai_scope",
    "state_confidence_threshold",
]
