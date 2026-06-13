"""
Behavioral coverage for the AIQ-P4 ReportRefinementAgent.run().

The static ratchet (``test_architectural_agent_contracts.py``) guarantees the
agent *calls* ``validate_agent_contract``; these tests exercise the *runtime*
reconciliation logic: multi-route deduplication, the deterministic primary-route
precedence (analysis > anomaly > cluster), cross-route contradiction detection
and resolution accounting, and the never-raise fallback contract.

DB-free: the three BaseAgent side-effect methods (``mark_stage_running``,
``mark_stage_done``, ``broadcast_progress``) are stubbed with ``AsyncMock`` —
exactly the idiom the existing agent ``run()`` tests use — so no database
session or outbound call is touched. The analytic logic under test is never
mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.report_refinement_agent import ReportRefinementAgent
from app.models.agent_contracts import Contradiction, ResolutionStrategy


def _agent() -> ReportRefinementAgent:
    """A ReportRefinementAgent with its DB/broadcast side-effects stubbed out."""
    agent = ReportRefinementAgent()
    agent.mark_stage_running = AsyncMock()  # type: ignore[method-assign]
    agent.mark_stage_done = AsyncMock()  # type: ignore[method-assign]
    agent.broadcast_progress = AsyncMock()  # type: ignore[method-assign]
    return agent


def _state(**overrides):
    state = {
        "pipeline_run_id": "run-refine-1",
        "project_id": "proj-1",
        "analyses": {},
        "anomalies": [],
        "cluster_map": {},
        "regression_tests": [],
    }
    state.update(overrides)
    return state


def _refined(result: dict) -> dict:
    return result["refined_report"]


def _contract(result: dict) -> dict:
    return result["agent_contracts"]["report_refinement"]


# ── Multi-route dedup + deterministic primary route ───────────────────────────


@pytest.mark.asyncio
async def test_multi_route_test_is_deduped_with_deterministic_primary_route():
    """A test that appears in >= 2 routes is deduped and gets the highest
    precedence primary route (analysis > anomaly > cluster).
    """
    # "t1" appears in analysis + anomaly + cluster (all three routes).
    state = _state(
        analyses={"t1": {"failure_category": "PRODUCT_BUG"}},
        anomalies=[{"test_ids": ["t1"]}],
        cluster_map={"t1": "cluster-A"},
    )

    result = await _agent().run(state)
    report = _refined(result)

    assert report["dedup_count"] >= 1
    assert "t1" in report["multi_route_test_ids"]

    reconciled = report["reconciled_tests"]
    assert "t1" in reconciled
    # analysis has the highest precedence among the three routes.
    assert reconciled["t1"]["primary_route"] == "analysis"
    assert set(reconciled["t1"]["routes"]) == {"analysis", "anomaly", "cluster"}

    contract = _contract(result)
    assert isinstance(contract["confidence_score"], int)
    assert 0 <= contract["confidence_score"] <= 100
    assert contract["decision_reason"]


@pytest.mark.asyncio
async def test_single_route_test_is_not_deduped():
    """A test present in exactly one route is not a multi-route dedup."""
    state = _state(analyses={"only": {"failure_category": "PRODUCT_BUG"}})

    result = await _agent().run(state)
    report = _refined(result)

    assert report["dedup_count"] == 0
    assert report["multi_route_test_ids"] == []
    assert report["reconciled_tests"] == {}


# ── Flaky-vs-regression contradiction + preserved resolution ──────────────────


@pytest.mark.asyncio
async def test_flaky_vs_regression_contradiction_preserves_resolution():
    """Analysis flags flaky AND a route treats the test as a regression/new
    anomaly -> exactly one FLAKY_VS_REGRESSION contradiction whose resolution is
    preserved (prefer_anomaly, NOT collapsed to flag_for_review) and counted as
    resolved.
    """
    # "x" is flaky per analysis, also in regression_tests AND an anomaly route
    # (so it is multi-route and triggers the contradiction).
    state = _state(
        analyses={"x": {"is_flaky": True, "failure_category": "FLAKY"}},
        anomalies=[{"test_ids": ["x"]}],
        regression_tests=["x"],
    )

    result = await _agent().run(state)
    report = _refined(result)

    contradictions = report["contradictions"]
    assert len(contradictions) == 1
    c = contradictions[0]
    assert c["test_id"] == "x"
    assert c["type"] == "flaky_vs_regression"
    # Resolution is a real strategy, preserved (not flag_for_review).
    assert c["resolution"] == "prefer_anomaly"
    assert c["resolution"] != "flag_for_review"

    # Resolution accounting is exact: resolved + unresolved == total.
    assert report["contradictions_resolved"] == 1
    assert report["unresolved_count"] == 0
    assert (
        report["contradictions_resolved"] + report["unresolved_count"]
        == len(contradictions)
    )


@pytest.mark.asyncio
async def test_completed_stage_and_next_stage_handoff():
    """The agent advances the pipeline to flaky_sentinel."""
    result = await _agent().run(_state())
    assert result["completed_stages"] == ["report_refinement"]
    assert result["current_stage"] == "flaky_sentinel"
    assert "report_refinement" in result["agent_contracts"]


# ── Never-raise contract on malformed / non-dict state ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", [None, [], "not-a-dict", 42])
async def test_non_dict_state_returns_fallback_contract(bad_state):
    """A non-dict state degrades to a deterministic fallback contract, no raise."""
    result = await _agent().run(bad_state)

    assert "refined_report" in result
    contract = _contract(result)
    assert contract["fallback_used"] is True
    assert contract["confidence_score"] == 0
    assert result["completed_stages"] == ["report_refinement"]
    assert result["current_stage"] == "flaky_sentinel"


@pytest.mark.asyncio
async def test_malformed_route_inputs_never_raise():
    """Anomalies missing test_ids and a None cluster_map must not raise — the
    malformed parts degrade to empty.
    """
    result = await _agent().run(
        _state(
            analyses={"a": {"failure_category": "PRODUCT_BUG"}},
            anomalies=[{"no_test_ids_here": True}, "not-a-dict"],
            cluster_map=None,  # not a dict -> coerced to {}
        )
    )
    report = _refined(result)
    # "a" only has the analysis route -> not multi-route.
    assert report["dedup_count"] == 0
    assert "report_refinement" in result["agent_contracts"]


# ── Enum round-trip guard (str(member) regression) ────────────────────────────


def test_resolution_strategy_enum_round_trips_to_value():
    """A Contradiction built with ResolutionStrategy.PREFER_ANOMALY must serialize
    to the enum *value* ("prefer_anomaly"), guarding against a str(member)
    regression that would emit "ResolutionStrategy.PREFER_ANOMALY".
    """
    c = Contradiction(
        test_id="t",
        resolution=ResolutionStrategy.PREFER_ANOMALY,
        routes=["analysis", "anomaly"],
    )
    dumped = c.model_dump(mode="json")
    assert dumped["resolution"] == "prefer_anomaly"
    assert dumped["resolution"] != "ResolutionStrategy.PREFER_ANOMALY"
