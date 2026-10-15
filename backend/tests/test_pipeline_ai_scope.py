"""The AI configuration a pipeline froze at its start (W04 AIC-02 / AIC-03).

``services/pipeline_ai_scope`` is the one place the Settings > AI confidence
threshold and LLM timeout live for the duration of a pipeline. These pin:

* the scope's lifetime (nesting, exit, ``None`` keeps the outer scope, tasks
  spawned inside inherit it);
* the one timeout rule, ``effective_llm_timeout``: state > scope > env, an
  explicit project override tightens, a site cap tightens, floor 1;
* ``agent_timeout_override``: a DEFAULT agent row never lowers the global
  timeout to the capability SLA (300 s -> 120 s on every install, risk R-3);
* the snapshot ``build_ai_config_snapshot`` freezes;
* ``confidence_gate.resolve_confidence_threshold`` reads the scope first.

DB-free: ``agent_config_service.default_config`` is pure.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services import pipeline_ai_scope as scope_mod
from app.services.agent_config_service import default_config
from app.services.pipeline_ai_scope import (
    ORIGIN_RESUMED_LEGACY,
    agent_timeout_override,
    build_ai_config_snapshot,
    current_ai_scope,
    current_confidence_threshold,
    effective_llm_timeout,
    pipeline_ai_scope,
    state_confidence_threshold,
)

RCA = "agent.root_cause_analysis.v1"
SUMMARY = "agent.summary.v1"


def _frozen(agent_id: str, *, source: str, timeout: int) -> dict:
    """The shape ``freeze_for_invocation`` returns, reduced to what is read."""
    return {
        "agent_id": agent_id,
        "source": source,
        "config_version": 1 if source == "project" else 0,
        "config": {"agent_id": agent_id, "timeout_seconds": timeout},
    }


def _snapshot(**overrides) -> dict:
    base = build_ai_config_snapshot(
        {
            "confidence_threshold": 95,
            "confidence_threshold_source": "ai_config",
            "timeout_seconds": 5,
            "timeout_seconds_source": "ai_config",
        }
    )
    base.update(overrides)
    return dict(base)


# ── the scope ────────────────────────────────────────────────────────────────


def test_scope_is_empty_outside_a_pipeline():
    assert current_ai_scope() is None
    assert current_confidence_threshold() == (int(settings.AI_CONFIDENCE_THRESHOLD), "env_default")
    assert effective_llm_timeout() == int(settings.AI_TIMEOUT_SECONDS)


def test_scope_nests_and_restores_on_exit():
    outer, inner = _snapshot(), _snapshot(confidence_threshold=40, llm_timeout_seconds=30)
    with pipeline_ai_scope(outer):
        assert current_ai_scope() is outer
        with pipeline_ai_scope(inner):
            assert current_confidence_threshold() == (40, "ai_config")
            assert effective_llm_timeout() == 30
        assert current_confidence_threshold() == (95, "ai_config")
        assert effective_llm_timeout() == 5
    assert current_ai_scope() is None


def test_scope_restores_after_an_exception():
    with pytest.raises(RuntimeError):
        with pipeline_ai_scope(_snapshot()):
            raise RuntimeError("stage failed")
    assert current_ai_scope() is None


def test_none_keeps_the_scope_already_in_force():
    """A helper with no snapshot must not switch off its caller's."""
    outer = _snapshot()
    with pipeline_ai_scope(outer):
        with pipeline_ai_scope(None):
            assert current_ai_scope() is outer


@pytest.mark.asyncio
async def test_tasks_created_inside_the_scope_see_it():
    """LangGraph runs nodes in tasks created inside ``ainvoke``: a ContextVar
    is copied into each, exactly as ``cost_budget_scope`` relies on."""
    async def read() -> tuple[int, int]:
        await asyncio.sleep(0)
        return current_confidence_threshold()[0], effective_llm_timeout()

    with pipeline_ai_scope(_snapshot()):
        inside = await asyncio.gather(*(asyncio.create_task(read()) for _ in range(3)))
    outside = await asyncio.create_task(read())

    assert inside == [(95, 5)] * 3
    assert outside == (int(settings.AI_CONFIDENCE_THRESHOLD), int(settings.AI_TIMEOUT_SECONDS))


# ── effective_llm_timeout ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,scope,kwargs,expected",
    [
        # base: state > scope > env
        ({"llm_timeout_seconds": 7}, {"llm_timeout_seconds": 50}, {}, 7),
        ({}, {"llm_timeout_seconds": 50}, {}, 50),
        (None, None, {}, 300),
        # a junk state value is ignored, never a 0-second timeout
        ({"llm_timeout_seconds": True}, {"llm_timeout_seconds": 50}, {}, 50),
        ({"llm_timeout_seconds": 0}, None, {}, 300),
        ({"llm_timeout_seconds": "5"}, None, {}, 300),
        # an explicit project override tightens (state snapshot first)
        (
            {"llm_timeout_seconds": 5, "ai_config_snapshot": {"agent_timeout_overrides": {RCA: 3}}},
            None, {"agent_id": RCA}, 3,
        ),
        # ...and only for its own agent
        (
            {"llm_timeout_seconds": 5, "ai_config_snapshot": {"agent_timeout_overrides": {RCA: 3}}},
            None, {"agent_id": SUMMARY}, 5,
        ),
        # the scope's override applies when the state carries no snapshot
        ({}, {"llm_timeout_seconds": 300, "agent_timeout_overrides": {SUMMARY: 45}}, {"agent_id": SUMMARY}, 45),
        # an override never RAISES the global value
        (
            {"llm_timeout_seconds": 5, "ai_config_snapshot": {"agent_timeout_overrides": {RCA: 60}}},
            None, {"agent_id": RCA}, 5,
        ),
        # site cap
        ({"llm_timeout_seconds": 300}, None, {"cap": 120}, 120),
        ({"llm_timeout_seconds": 60}, None, {"cap": 120}, 60),
        # floor 1
        ({"llm_timeout_seconds": 300}, None, {"cap": 0}, 1),
    ],
)
def test_effective_llm_timeout_table(monkeypatch, state, scope, kwargs, expected):
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 300)
    if scope is None:
        assert effective_llm_timeout(state, **kwargs) == expected
        return
    with pipeline_ai_scope(scope):
        assert effective_llm_timeout(state, **kwargs) == expected


def test_effective_llm_timeout_is_never_below_one_even_with_a_broken_env(monkeypatch):
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 0)
    assert effective_llm_timeout() == 1


# ── agent_timeout_override ───────────────────────────────────────────────────


def test_a_default_row_is_not_an_override():
    """No project row: the capability SLA is not a policy (risk R-3)."""
    default = int(default_config(RCA).timeout_seconds)
    assert agent_timeout_override(_frozen(RCA, source="default", timeout=default)) is None


def test_a_stored_row_holding_the_default_value_is_not_an_override():
    """Assertion §2.4-3: a stored agent row with the default timeout must NOT
    lower 300 -> 120."""
    default = int(default_config(RCA).timeout_seconds)
    frozen = _frozen(RCA, source="project", timeout=default)
    assert agent_timeout_override(frozen) is None

    snapshot = build_ai_config_snapshot({"timeout_seconds": 300}, {RCA: frozen})
    assert snapshot["agent_timeout_overrides"] == {}
    assert effective_llm_timeout({"llm_timeout_seconds": 300, "ai_config_snapshot": snapshot}, agent_id=RCA) == 300


def test_an_edited_row_is_an_override():
    frozen = _frozen(RCA, source="project", timeout=3)
    assert agent_timeout_override(frozen) == 3

    snapshot = build_ai_config_snapshot({"timeout_seconds": 5}, {RCA: frozen})
    assert snapshot["agent_timeout_overrides"] == {RCA: 3}
    state = {"llm_timeout_seconds": 5, "ai_config_snapshot": snapshot}
    assert effective_llm_timeout(state, agent_id=RCA) == 3


def test_an_override_above_the_global_value_is_not_frozen():
    """Lower layers only tighten: a 600 s project value under a 5 s global is
    not recorded as an override (it could never apply)."""
    frozen = _frozen(RCA, source="project", timeout=600)
    snapshot = build_ai_config_snapshot({"timeout_seconds": 5}, {RCA: frozen})
    assert snapshot["agent_timeout_overrides"] == {}


@pytest.mark.parametrize(
    "frozen",
    [
        None,
        {},
        {"source": "project", "agent_id": RCA},
        {"source": "project", "agent_id": RCA, "config": {"timeout_seconds": True}},
        {"source": "project", "agent_id": "agent.no_such_capability.v1", "config": {"timeout_seconds": 3}},
    ],
)
def test_malformed_frozen_configs_are_not_overrides(frozen):
    assert agent_timeout_override(frozen) is None  # type: ignore[arg-type]


# ── build_ai_config_snapshot ─────────────────────────────────────────────────


def test_snapshot_shape_and_provenance():
    snapshot = build_ai_config_snapshot(
        {
            "confidence_threshold": 95,
            "confidence_threshold_source": "ai_config",
            "timeout_seconds": 5,
            "timeout_seconds_source": "ai_config",
        }
    )
    assert set(snapshot) == {
        "schema", "confidence_threshold", "confidence_threshold_source",
        "llm_timeout_seconds", "llm_timeout_source", "agent_timeout_overrides",
        "origin", "frozen_at",
    }
    assert snapshot["schema"] == 1
    assert snapshot["confidence_threshold"] == 95
    assert snapshot["confidence_threshold_source"] == "ai_config"
    assert snapshot["llm_timeout_seconds"] == 5
    assert snapshot["llm_timeout_source"] == "ai_config"
    assert snapshot["origin"] == "pipeline_start"
    assert snapshot["frozen_at"].endswith("+00:00")


def test_snapshot_falls_back_to_env_defaults_for_junk(monkeypatch):
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 300)
    snapshot = build_ai_config_snapshot(
        {"confidence_threshold": "high", "timeout_seconds": None}, origin=ORIGIN_RESUMED_LEGACY
    )
    assert snapshot["confidence_threshold"] == int(settings.AI_CONFIDENCE_THRESHOLD)
    assert snapshot["confidence_threshold_source"] == "env_default"
    assert snapshot["llm_timeout_seconds"] == 300
    assert snapshot["llm_timeout_source"] == "env_default"
    assert snapshot["origin"] == "resumed_legacy"


def test_snapshot_timeout_source_is_inferred_when_the_resolver_gives_none(monkeypatch):
    """A config from an older resolver (no ``timeout_seconds_source``)."""
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 300)
    assert build_ai_config_snapshot({"timeout_seconds": 300})["llm_timeout_source"] == "env_default"
    assert build_ai_config_snapshot({"timeout_seconds": 5})["llm_timeout_source"] == "ai_config"


# ── thresholds ───────────────────────────────────────────────────────────────


def test_state_threshold_wins_over_the_scope():
    with pipeline_ai_scope(_snapshot(confidence_threshold=40)):
        assert state_confidence_threshold({"confidence_threshold": 95})[0] == 95
        assert state_confidence_threshold({}) == (40, "ai_config")
    assert state_confidence_threshold({"confidence_threshold": 101})[0] == int(settings.AI_CONFIDENCE_THRESHOLD)


def test_state_threshold_takes_its_source_from_the_state_snapshot():
    state = {
        "confidence_threshold": 80,
        "ai_config_snapshot": {"confidence_threshold": 80, "confidence_threshold_source": "ai_config"},
    }
    assert state_confidence_threshold(state) == (80, "ai_config")


@pytest.mark.asyncio
async def test_the_confidence_gate_reads_the_scope_first(monkeypatch):
    """Every check_confidence() inside a pipeline uses the FROZEN value, even
    after an admin changed the live setting."""
    from app.services import confidence_gate

    async def live_config(**_kwargs):
        return {"confidence_threshold": 40, "confidence_threshold_source": "ai_config"}

    monkeypatch.setattr("app.services.ai_config_resolver.get_effective_ai_config", live_config)

    with pipeline_ai_scope(_snapshot()):
        assert await confidence_gate.resolve_confidence_threshold() == (95, "ai_config")
        check = await confidence_gate.check_confidence(90)
    assert check == {"threshold": 95, "observed_confidence": 90, "passed": False, "source": "ai_config"}
    # Outside the pipeline the live value applies, as before.
    assert await confidence_gate.resolve_confidence_threshold() == (40, "ai_config")


def test_module_exports_the_contract_surface():
    for name in (
        "AIConfigSnapshot", "pipeline_ai_scope", "current_ai_scope", "build_ai_config_snapshot",
        "agent_timeout_override", "effective_llm_timeout", "current_confidence_threshold",
    ):
        assert hasattr(scope_mod, name), name
