"""
Wiring coverage for the AIQ-P4 deep-graph integration.

Verifies the deterministic facts the reviewers checked by inspecting topology
and node-function behavior rather than running a full DB-backed graph:

  * The deep graph's node + edge sets are identical whether
    ``AIQ_GAP_REFINEMENT_ENABLED`` is on or off — the flag gates *behavior*
    inside the nodes, never the topology.
  * The two new stages and the triage -> gap_detection -> report_refinement ->
    flaky_sentinel chain are present.
  * Flag OFF: the gap_detection_node / report_refinement_node early-return a
    skip delta (completed + skipped) and never run the underlying agent.
  * Flag ON: the nodes run the agent and populate gap_report / refined_report.
  * Vocabulary consistency: both DEEP_OPTIONAL_STAGES (workflow) and
    _DEEP_STAGES (agent_planner) list the two new stages.

DB-free: graph topology is pure structure; node functions are called directly
with a minimal state. The flag is toggled via monkeypatch on ``settings`` and
the agent singletons' DB/broadcast side-effects are stubbed with AsyncMock so
the ON path exercises the real analytic logic without touching a database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents import workflow as wf
from app.core.config import settings


def _topology(graph):
    """Return (node_set, edge_set) for a compiled LangGraph."""
    compiled = graph.compile().get_graph()
    nodes = frozenset(compiled.nodes)
    edges = frozenset((e.source, e.target) for e in compiled.edges)
    return nodes, edges


def _minimal_state(**overrides):
    state = {
        "pipeline_run_id": "",  # empty -> DB writes are no-ops / guarded
        "project_id": "proj-1",
        "failed_test_ids": ["a"],
        "analyses": {
            "a": {
                "failure_category": "PRODUCT_BUG",
                "evidence_references": [{"source": "log"}],
                "confidence_score": 90,
            }
        },
        "anomalies": [],
        "cluster_map": {},
        "regression_tests": [],
    }
    state.update(overrides)
    return state


# ── Topology is flag-invariant ────────────────────────────────────────────────


def test_deep_graph_topology_is_identical_regardless_of_flag(monkeypatch):
    """Node set + edge set must be equal whether the flag is off or on."""
    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", False)
    nodes_off, edges_off = _topology(wf._build_deep_graph())

    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", True)
    nodes_on, edges_on = _topology(wf._build_deep_graph())

    assert nodes_off == nodes_on
    assert edges_off == edges_on


def test_deep_graph_contains_new_nodes_and_chain():
    """Both new nodes plus the triage -> gap_detection -> report_refinement ->
    flaky_sentinel chain (and the no-triage summary -> gap_detection edge) are
    present in the deep graph.
    """
    nodes, edges = _topology(wf._build_deep_graph())

    assert "gap_detection" in nodes
    assert "report_refinement" in nodes
    assert "decision_report" in nodes
    assert "decision_report_critic" in nodes

    # Core AIQ-P4 chain.
    assert ("triage", "contract_validation") in edges
    assert ("contract_validation", "log_intelligence") in edges
    assert ("log_intelligence", "regression_watchman") in edges
    assert ("regression_watchman", "change_ownership") in edges
    assert ("change_ownership", "gap_detection") in edges
    assert ("gap_detection", "report_refinement") in edges
    assert ("report_refinement", "flaky_sentinel") in edges
    # No-triage deep route still feeds gap_detection.
    assert ("summary", "gap_detection") in edges or ("summary", "contract_validation") in edges
    assert ("release_risk", "decision_report") in edges
    assert ("decision_report", "decision_report_critic") in edges
    assert ("decision_report_critic", "__end__") in edges


# ── Flag OFF: nodes skip, agent never runs, no contract stamped ───────────────


@pytest.mark.asyncio
async def test_gap_detection_node_skips_when_flag_off(monkeypatch):
    """Flag off -> skip delta (completed + skipped include the stage), no
    gap_report populated, and the agent's validate_agent_contract is NOT called.
    """
    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", False)

    sentinel = AsyncMock(side_effect=AssertionError("agent must not run when flag off"))
    monkeypatch.setattr(wf._gap_detection, "run", sentinel)

    result = await wf.gap_detection_node(_minimal_state())

    assert "gap_detection" in result["completed_stages"]
    assert "gap_detection" in result["skipped_stages"]
    assert result["current_stage"] == "report_refinement"
    assert "gap_report" not in result
    assert "agent_contracts" not in result
    sentinel.assert_not_called()


@pytest.mark.asyncio
async def test_report_refinement_node_skips_when_flag_off(monkeypatch):
    """Flag off -> skip delta, no refined_report, agent never runs."""
    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", False)

    sentinel = AsyncMock(side_effect=AssertionError("agent must not run when flag off"))
    monkeypatch.setattr(wf._report_refinement, "run", sentinel)

    result = await wf.report_refinement_node(_minimal_state())

    assert "report_refinement" in result["completed_stages"]
    assert "report_refinement" in result["skipped_stages"]
    assert result["current_stage"] == "flaky_sentinel"
    assert "refined_report" not in result
    assert "agent_contracts" not in result
    sentinel.assert_not_called()


# ── Flag ON: nodes run the agent and populate the report ──────────────────────


@pytest.mark.asyncio
async def test_gap_detection_node_runs_agent_when_flag_on(monkeypatch):
    """Flag on -> the agent runs and populates gap_report (no skip marker)."""
    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", True)
    monkeypatch.setattr(wf._gap_detection, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(wf._gap_detection, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(wf._gap_detection, "broadcast_progress", AsyncMock())

    result = await wf.gap_detection_node(_minimal_state())

    assert "gap_report" in result
    assert "skipped_stages" not in result
    assert result["gap_report"]["analyzed_count"] == 1
    assert result["agent_contracts"]["gap_detection"]["fallback_used"] is False


@pytest.mark.asyncio
async def test_report_refinement_node_runs_agent_when_flag_on(monkeypatch):
    """Flag on -> the agent runs and populates refined_report (no skip marker)."""
    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", True)
    monkeypatch.setattr(wf._report_refinement, "mark_stage_running", AsyncMock())
    monkeypatch.setattr(wf._report_refinement, "mark_stage_done", AsyncMock())
    monkeypatch.setattr(wf._report_refinement, "broadcast_progress", AsyncMock())

    state = _minimal_state(
        analyses={"a": {"failure_category": "PRODUCT_BUG"}},
        anomalies=[{"test_ids": ["a"]}],
        cluster_map={"a": "cluster-1"},
    )
    result = await wf.report_refinement_node(state)

    assert "refined_report" in result
    assert "skipped_stages" not in result
    assert "a" in result["refined_report"]["multi_route_test_ids"]
    assert result["agent_contracts"]["report_refinement"]["fallback_used"] is False


# ── Vocabulary consistency across workflow + planner ──────────────────────────


def test_deep_optional_stages_include_new_stages():
    """workflow.DEEP_OPTIONAL_STAGES must include both AIQ-P4 stages."""
    assert "gap_detection" in wf.DEEP_OPTIONAL_STAGES
    assert "report_refinement" in wf.DEEP_OPTIONAL_STAGES
    assert "decision_report" in wf.DEEP_REQUIRED_STAGES
    assert "decision_report_critic" in wf.DEEP_REQUIRED_STAGES


def test_planner_deep_stages_include_new_stages():
    """agent_planner._DEEP_STAGES must include both AIQ-P4 stages."""
    from app.services.agent_planner import _DEEP_STAGES

    assert "gap_detection" in _DEEP_STAGES
    assert "report_refinement" in _DEEP_STAGES
    assert _DEEP_STAGES[-2:] == ("decision_report", "decision_report_critic")

@pytest.mark.asyncio
async def test_log_intelligence_node_skips_when_flag_off(monkeypatch):
    sentinel = AsyncMock(side_effect=AssertionError("agent must not run when flag off"))
    monkeypatch.setattr(wf, "_log_intelligence", sentinel)
    result = await wf.log_intelligence_node(_minimal_state(log_intelligence_enabled=False))
    assert result["log_findings"]["status"] == "not_enough_evidence"
    assert "log_intelligence" in result["skipped_stages"]
    sentinel.assert_not_called()
@pytest.mark.asyncio
async def test_log_intelligence_node_preserves_failed_status(monkeypatch):
    agent = MagicMock()
    agent.investigate = AsyncMock(return_value={
        "status": "failed",
        "distributed_trace": {"error": "RuntimeError"},
        "log_anomaly": {},
        "log_summary": "degraded",
    })
    monkeypatch.setattr(wf, "_log_intelligence", agent)
    state = _minimal_state(log_intelligence_enabled=True)
    state["analyses"] = {"tc-1": {"service_name": "payments", "timestamp_utc": "2026-06-12T00:00:00Z"}}
    result = await wf.log_intelligence_node(state)
    assert result["log_findings"]["status"] == "failed"
    agent.investigate.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_intelligence_node_scopes_and_bounds_cluster_contexts(monkeypatch):
    agent = MagicMock()
    agent.investigate = AsyncMock(side_effect=[
        {
            "status": "complete",
            "distributed_trace": {"causal_summary": f"cluster-{index}"},
            "log_anomaly": {"anomaly_detected": False},
            "log_summary": f"cluster-{index} summary",
            "evidence_refs": [{"source": "trace", "ref_id": str(index)}],
        }
        for index in range(5)
    ])
    monkeypatch.setattr(wf, "_log_intelligence", agent)
    analyses = {
        f"tc-{index}": {
            "service_name": f"service-{index}",
            "timestamp_utc": f"2026-06-12T00:0{index}:00Z",
        }
        for index in range(7)
    }
    state = _minimal_state(
        log_intelligence_enabled=True,
        analyses=analyses,
        failure_clusters=list(reversed([
            {"cluster_id": f"c{index}", "member_test_ids": [f"tc-{index}"]}
            for index in range(7)
        ])),
    )

    result = await wf.log_intelligence_node(state)

    assert agent.investigate.await_count == 5
    findings = result["log_findings"]
    assert findings["status"] == "complete"
    assert findings["cluster_count"] == 5
    assert [item["cluster_id"] for item in findings["cluster_findings"]] == [
        "c0", "c1", "c2", "c3", "c4"
    ]
    assert findings["distributed_trace"]["causal_summary"] == "cluster-0"
    assert len(findings["evidence_refs"]) == 5
@pytest.mark.asyncio
async def test_regression_watchman_node_preserves_contract(monkeypatch):
    agent = MagicMock()
    agent.run = AsyncMock(return_value={
        "status": "complete",
        "regression_classification": {"c1": {"classification": "new_regression"}},
        "agent_contracts": {"regression_watchman": {"schema_version": 1}},
        "errors": [],
    })
    monkeypatch.setattr(wf, "_regression_watchman", agent)
    state = _minimal_state(regression_watchman_enabled=True)
    state["failure_clusters"] = [{"cluster_id": "c1", "member_test_ids": ["tc-1"]}]
    result = await wf.regression_watchman_node(state)
    assert result["regression_classification"]["c1"]["classification"] == "new_regression"
    agent.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkpoint_wrapper_honors_frozen_planner_selection(monkeypatch):
    """Stable graph topology must not execute an explicitly unplanned stage."""
    called = False

    async def original_node(_state):
        nonlocal called
        called = True
        return {"completed_stages": ["contract_validation"]}

    skipped = AsyncMock()
    monkeypatch.setattr(wf, "_write_stage_skipped", skipped)
    monkeypatch.setattr(wf, "emit_event", AsyncMock())
    wrapper = wf._make_checkpointed_node(original_node, "contract_validation")

    result = await wrapper({
        "pipeline_run_id": "pipeline-1",
        "initial_workflow_plan": {
            "stages": [{
                "stage": "contract_validation",
                "planned": False,
                "rationale": "feature flag disabled",
            }],
        },
    })

    assert called is False
    assert result["skipped_stages"] == ["contract_validation"]
    assert result["execution_path"] == wf.ExecutionPath.CONDITIONAL_SKIP
    skipped.assert_awaited_once()
    assert skipped.await_args.kwargs["stop_reason"] == "planner_not_selected"


@pytest.mark.asyncio
async def test_checkpoint_wrapper_executes_selected_stage(monkeypatch):
    called = False

    async def original_node(_state):
        nonlocal called
        called = True
        return {"completed_stages": ["contract_validation"]}

    monkeypatch.setattr(wf, "_checkpoint_stage", AsyncMock())
    wrapper = wf._make_checkpointed_node(original_node, "contract_validation")
    result = await wrapper({
        "pipeline_run_id": "",
        "initial_workflow_plan": {
            "stages": [{"stage": "contract_validation", "planned": True}],
        },
    })

    assert called is True
    assert result["completed_stages"] == ["contract_validation"]
