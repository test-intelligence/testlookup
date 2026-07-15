"""Investigator node behaviour: offline determinism, budgets, cancel (AI-1).

Drives the hypothesis/synthesis agents' ``run()`` skeleton directly with
prepared state, stubbing the BaseAgent observability surface and the
row-persistence helpers (both open real DB sessions). Pins:

* offline-deterministic verdicts — with ``AI_OFFLINE_MODE`` (the default)
  NO LLM is ever constructed and ``confidence_basis`` stays
  ``heuristic_estimate``;
* the wall-clock budget path — a passed deadline yields an honest
  ``inconclusive`` leftover and the synthesis narrative carries a budget
  note while the investigation still completes;
* the cooperative cancel path — a set cancel flag skips work and produces
  no hypothesis;
* the LLM-budget gate — ``max_llm_calls=0`` blocks the weigh call even
  when an LLM would be reachable;
* graph topology — plan fans out to all five hypotheses, which fan into
  synthesis.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.investigator import hypotheses as hyp_mod  # noqa: E402
from app.agents.investigator import synthesis as syn_mod  # noqa: E402
from app.agents.investigator.hypotheses import (  # noqa: E402
    BASIS_HEURISTIC,
    InfraHypothesisAgent,
    KnownFlakyHypothesisAgent,
)
from app.agents.investigator.synthesis import SynthesisAgent  # noqa: E402
from tests.test_investigator_hypotheses import (  # noqa: E402
    all_flaky_bundle,
    pure_infra_bundle,
)


# ─────────────────────────── Stubs ───────────────────────────


@pytest.fixture
def quiet_agent(monkeypatch):
    """Stub the observability + persistence surface (real DB/Mongo I/O)."""
    persisted: list[dict] = []

    async def _noop(*args, **kwargs):
        return None

    async def _capture_persist(_investigation_id, hypothesis):
        persisted.append(hypothesis)

    async def _not_cancelled(_investigation_id):
        return False

    from app.agents.base import BaseAgent

    monkeypatch.setattr(BaseAgent, "mark_stage_running", _noop)
    monkeypatch.setattr(BaseAgent, "mark_stage_done", _noop)
    monkeypatch.setattr(BaseAgent, "log_decision", _noop)
    monkeypatch.setattr(BaseAgent, "broadcast_progress", _noop)
    monkeypatch.setattr(hyp_mod, "persist_hypothesis", _capture_persist)
    monkeypatch.setattr(hyp_mod, "is_cancel_requested", _not_cancelled)
    monkeypatch.setattr(syn_mod, "is_cancel_requested", _not_cancelled)
    return persisted


def _state(bundle, *, deadline_offset=300.0, max_llm_calls=30, cancelled=False):
    return {
        "investigation_id": "11111111-1111-1111-1111-111111111111",
        "pipeline_run_id": "22222222-2222-2222-2222-222222222222",
        "run_id": "33333333-3333-3333-3333-333333333333",
        "project_id": "44444444-4444-4444-4444-444444444444",
        "build_number": "b-100",
        "mode": "shadow",
        "triggered_by": "manual",
        "budget": {"max_llm_calls": max_llm_calls, "max_tokens": 60000, "max_seconds": 300},
        "deadline_ts": time.monotonic() + deadline_offset,
        "bundle": bundle,
        "cancelled": cancelled,
        "hypotheses": [],
        "spend_llm_calls": 0,
        "spend_tokens": 0,
        "spend_cost_usd": 0.0,
        "errors": [],
        "verdict": None,
        "model_info": None,
    }


# ─────────────────────── Offline determinism ───────────────────────


@pytest.mark.asyncio
async def test_offline_run_is_deterministic_and_never_touches_llm(
    quiet_agent, monkeypatch,
):
    """AI_OFFLINE_MODE (the default) → deterministic verdict, heuristic
    basis, and get_llm is NEVER constructed."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    def _explode(*args, **kwargs):  # pragma: no cover — must not be reached
        raise AssertionError("get_llm must not be called in offline mode")

    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(llm_factory, "get_llm", _explode)

    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle()))
    (hyp,) = delta["hypotheses"]
    assert hyp["id"] == "infra"
    assert hyp["status"] == "validated"
    assert hyp["confidence_basis"] == BASIS_HEURISTIC
    assert delta["spend_llm_calls"] == 0
    assert delta["spend_tokens"] == 0
    # Progress persisted onto the row as the node finished.
    assert quiet_agent and quiet_agent[0]["id"] == "infra"


@pytest.mark.asyncio
async def test_offline_synthesis_is_deterministic(quiet_agent, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    agent = SynthesisAgent()
    state = _state(all_flaky_bundle())
    state["hypotheses"] = [
        {"id": "known_flaky", "status": "validated", "confidence": 88,
         "summary": "flaky coverage 100%", "confidence_basis": BASIS_HEURISTIC},
        {"id": "regression", "status": "invalidated", "confidence": 60,
         "summary": "no core", "confidence_basis": BASIS_HEURISTIC},
    ]
    delta = await agent.run(state)
    verdict = delta["verdict"]
    assert verdict["primary_cause"] == "known_flaky"
    assert verdict["confidence"] == 88
    assert verdict["recommended_actions"]
    assert delta["model_info"] is None  # no LLM engaged offline
    assert delta["spend_llm_calls"] == 0


# ─────────────────────── LLM budget gate ───────────────────────


@pytest.mark.asyncio
async def test_llm_call_budget_zero_blocks_weighing(quiet_agent, monkeypatch):
    """max_llm_calls=0 blocks the weigh call even when NOT offline."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("budget of 0 must block the LLM call")

    import app.services.llm_factory as llm_factory

    monkeypatch.setattr(llm_factory, "get_llm", _explode)

    agent = KnownFlakyHypothesisAgent()
    delta = await agent.run(_state(all_flaky_bundle(), max_llm_calls=0))
    (hyp,) = delta["hypotheses"]
    assert hyp["confidence_basis"] == BASIS_HEURISTIC
    assert delta["spend_llm_calls"] == 0


# ─────────────────────── Budget exhaustion ───────────────────────


@pytest.mark.asyncio
async def test_deadline_exhaustion_yields_inconclusive_leftover(quiet_agent):
    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle(), deadline_offset=-1.0))
    (hyp,) = delta["hypotheses"]
    assert hyp["status"] == "inconclusive"
    assert "budget exhausted" in hyp["summary"].lower()
    assert hyp["confidence_basis"] == BASIS_HEURISTIC


@pytest.mark.asyncio
async def test_synthesis_notes_budget_exhaustion_in_narrative(quiet_agent, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    agent = SynthesisAgent()
    state = _state(pure_infra_bundle(), deadline_offset=-1.0)
    state["hypotheses"] = [
        {"id": "infra", "status": "validated", "confidence": 85,
         "summary": "infra dominates", "confidence_basis": BASIS_HEURISTIC},
        {"id": "commit", "status": "inconclusive", "confidence": 0,
         "summary": "Wall-clock budget exhausted before this hypothesis ran.",
         "confidence_basis": BASIS_HEURISTIC},
    ]
    delta = await agent.run(state)
    verdict = delta["verdict"]
    # Budget exhaustion is honest: verdict still lands (status completed at
    # the runner level) with the leftovers named in the narrative.
    assert verdict["primary_cause"] == "infra"
    assert "budget" in verdict["narrative"].lower()
    assert "commit" in verdict["narrative"]


# ─────────────────────── Cooperative cancel ───────────────────────


@pytest.mark.asyncio
async def test_cancel_flag_skips_hypothesis_work(quiet_agent, monkeypatch):
    async def _cancelled(_investigation_id):
        return True

    monkeypatch.setattr(hyp_mod, "is_cancel_requested", _cancelled)
    agent = InfraHypothesisAgent()
    delta = await agent.run(_state(pure_infra_bundle()))
    assert delta["hypotheses"] == []
    assert quiet_agent == []  # nothing persisted


@pytest.mark.asyncio
async def test_cancel_flag_skips_synthesis(quiet_agent, monkeypatch):
    async def _cancelled(_investigation_id):
        return True

    monkeypatch.setattr(syn_mod, "is_cancel_requested", _cancelled)
    agent = SynthesisAgent()
    state = _state(pure_infra_bundle())
    state["hypotheses"] = [
        {"id": "infra", "status": "validated", "confidence": 85, "summary": "x"},
    ]
    delta = await agent.run(state)
    assert delta["verdict"] is None


# ─────────────────────── Graph topology + finalize helpers ───────────────────


def test_graph_topology_fans_out_to_all_hypotheses():
    from app.agents.investigator.workflow import _build_graph

    graph = _build_graph()
    edges = {(e[0], e[1]) for e in graph.edges}
    for hyp in (
        "hypothesis_infra",
        "hypothesis_commit",
        "hypothesis_environment",
        "hypothesis_known_flaky",
        "hypothesis_regression",
    ):
        assert ("investigator_plan", hyp) in edges
        assert (hyp, "investigator_synthesis") in edges


def test_merge_hypotheses_keeps_pending_placeholders():
    from app.agents.investigator.workflow import _merge_hypotheses
    from app.services.agent_investigation_service import HYPOTHESIS_IDS

    seeded = [{"id": h, "status": "pending"} for h in HYPOTHESIS_IDS]
    produced = [{"id": "infra", "status": "validated", "confidence": 80}]
    merged = _merge_hypotheses(seeded, produced)
    assert [m["id"] for m in merged] == list(HYPOTHESIS_IDS)
    assert merged[0]["status"] == "validated"
    assert all(m["status"] == "pending" for m in merged[1:])


def test_verdict_summary_line_shapes():
    from app.agents.investigator.workflow import _verdict_summary_line

    assert "cancelled" in _verdict_summary_line(None, "cancelled")
    assert "failed" in _verdict_summary_line(None, "failed")
    line = _verdict_summary_line(
        {"primary_cause": "infra", "confidence": 85, "narrative": "n" * 500},
        "completed",
    )
    assert "primary_cause=infra" in line and len(line) < 300
