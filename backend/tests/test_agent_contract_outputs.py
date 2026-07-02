"""
Behavioral coverage for AIQ-P1 structured agent contracts.

The static ratchet (``test_architectural_agent_contracts.py``) guarantees that
every analytic agent *calls* ``validate_agent_contract``. These tests exercise
the *runtime* wrapping for the agents whose contract wiring was added in
AIQ-P1 and that lack a dedicated behavioral test of the contracted output
(``LogIntelligenceAgent`` and ``RegressionWatchman``), so a changed source
file is not left without a matching test.

DB-free: the LogIntelligence tools are patched and the RegressionWatchman
output shape is validated directly against its contract model.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.agent_contracts import (
    RegressionWatchmanAgentOutput,
    validate_agent_contract,
)


def _tool_returning(value):
    """A stand-in for a langchain StructuredTool whose ainvoke returns value."""
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=value)
    return tool


def _tool_raising(exc):
    tool = MagicMock()
    tool.ainvoke = AsyncMock(side_effect=exc)
    return tool


@pytest.mark.asyncio
async def test_log_intelligence_investigate_wraps_contract_on_success():
    """A successful investigate() stamps a high-confidence contract with both
    evidence refs and preserves the original evidence keys (no behavior change).
    """
    from app.agents.log_intelligence_agent import LogIntelligenceAgent

    # Realistic tool outputs carrying the real signal the evidence graders read:
    # an error-bearing trace and a detected spike with a high ratio → strong
    # evidence → high confidence. (Grading now reflects the actual signal rather
    # than a fixed medium/80.)
    trace_payload = json.dumps({
        "causal_summary": "A -> B timeout",
        "trace_steps": [
            {"service": "A", "level": "ERROR", "message": "timeout"},
            {"service": "B", "level": "ERROR", "message": "downstream 500"},
            {"service": "B", "level": "FATAL", "message": "circuit open"},
        ],
    })
    anomaly_payload = json.dumps({
        "assessment": "spike at T0",
        "anomaly_detected": True,
        "levels": {"ERROR": {"ratio": 12.0}},
    })

    with patch(
        "app.agents.log_intelligence_agent.reconstruct_distributed_trace",
        new=_tool_returning(trace_payload),
    ), patch(
        "app.agents.log_intelligence_agent.detect_log_rate_anomaly",
        new=_tool_returning(anomaly_payload),
    ):
        result = await LogIntelligenceAgent().investigate(
            service_name="checkout",
            timestamp_utc="2026-06-12T00:00:00Z",
        )

    # Original keys preserved (superset, no behavior change).
    assert result["distributed_trace"]["causal_summary"] == "A -> B timeout"
    assert result["log_anomaly"]["assessment"] == "spike at T0"
    assert "log_summary" in result

    contract = result["agent_contracts"]["log_intelligence"]
    assert contract["fallback_used"] is False
    # Strong error-bearing trace (3 ERROR/FATAL steps) + a severe spike (12×) →
    # both refs strong → high aggregate confidence (no longer a fixed 80).
    assert contract["confidence_score"] >= 80
    assert contract["evidence_count"] == 2
    assert contract["decision_reason"] == "log_evidence_gathered"


@pytest.mark.asyncio
async def test_log_intelligence_investigate_marks_fallback_on_tool_error():
    """When both tools fail, the contract degrades to a zero-confidence
    fallback with no evidence refs but still never raises.
    """
    from app.agents.log_intelligence_agent import LogIntelligenceAgent

    with patch(
        "app.agents.log_intelligence_agent.reconstruct_distributed_trace",
        new=_tool_raising(RuntimeError("trace down")),
    ), patch(
        "app.agents.log_intelligence_agent.detect_log_rate_anomaly",
        new=_tool_raising(RuntimeError("anomaly down")),
    ):
        result = await LogIntelligenceAgent().investigate(
            service_name="checkout",
            timestamp_utc="2026-06-12T00:00:00Z",
        )

    contract = result["agent_contracts"]["log_intelligence"]
    assert contract["fallback_used"] is True
    assert contract["confidence_score"] == 0
    assert contract["evidence_count"] == 0
    assert contract["decision_reason"] == "partial_log_evidence"


def test_regression_watchman_output_shape_matches_contract():
    """The exact dict shape RegressionWatchman.run() returns validates against
    its contract model and preserves every workflow key.
    """
    classification = {
        "cluster-1": {"confidence": 90, "verdict": "regression"},
        "cluster-2": {"confidence": 70, "verdict": "flaky"},
    }
    payload = {
        "regression_classification": classification,
        "completed_stages": ["regression_watchman"],
        "errors": [],
        "current_stage": "defect_commander",
    }
    result = validate_agent_contract(
        RegressionWatchmanAgentOutput,
        payload,
        agent_name="regression_watchman",
        confidence=80,
        evidence_refs=[{"type": "cluster", "id": "cluster-1"}],
        decision_reason="Classified 2 failure clusters",
    )

    # Workflow keys preserved verbatim.
    assert result["regression_classification"] == classification
    assert result["completed_stages"] == ["regression_watchman"]
    assert result["current_stage"] == "defect_commander"

    contract = result["agent_contracts"]["regression_watchman"]
    assert contract["confidence_score"] == 80
    assert contract["evidence_count"] == 1
    assert contract["decision_reason"] == "Classified 2 failure clusters"


# ── CLEANUP-1: RegressionWatchman.run() success path must never raise ──────────
@pytest.mark.asyncio
async def test_regression_watchman_run_never_raises_on_malformed_classification():
    """run()'s SUCCESS branch derives confidence/evidence from the classification
    dict. A classification merged back from a partially-validated LLM payload can
    hold a non-dict value AND a 'confidence' that is a non-numeric string; the
    success path must coerce both defensively and still return a valid contract
    rather than raising into the graph node wrapper (which would fail the run).
    """
    from app.agents.regression_watchman import RegressionWatchman

    malformed = {
        "cluster-1": "not-a-dict",  # .get -> AttributeError if unguarded
        "cluster-2": {"confidence": "high", "verdict": "flaky"},  # int('high') -> ValueError
    }

    agent = RegressionWatchman()
    agent.mark_stage_running = AsyncMock()
    agent.mark_stage_done = AsyncMock()
    agent.broadcast_progress = AsyncMock()
    agent._classify = AsyncMock(return_value=malformed)

    state = {"pipeline_run_id": "pr-1", "project_id": "proj-1", "test_run_id": "tr-1"}
    result = await agent.run(state)  # must not raise

    assert result["regression_classification"] == malformed
    contract = result["agent_contracts"]["regression_watchman"]
    assert contract["fallback_used"] is False
    # both values coerced to 0 (non-dict skipped, 'high' -> 0) -> mean 0
    assert contract["confidence_score"] == 0
    assert contract["evidence_count"] == 2


def test_summarize_classification_defensive_coercion():
    """The guarded helper coerces every awkward value and never raises."""
    from app.agents.regression_watchman import RegressionWatchman

    conf, refs = RegressionWatchman._summarize_classification(
        {"a": {"confidence": 80}, "b": "str", "c": {"confidence": None}, "d": {}}
    )
    # confidences = [80 (a), 0 (c None), 0 (d missing)]; b skipped -> mean = 26
    assert conf == 26
    assert len(refs) == 4  # one ref per top-level key (b included as a cluster id)

    # Empty / non-dict input degrades to confidence 100, no evidence.
    assert RegressionWatchman._summarize_classification({}) == (100, [])
    assert RegressionWatchman._summarize_classification(None) == (100, [])

    # A float infinity confidence (reachable: json.loads accepts ``Infinity``)
    # raises OverflowError on int() — it must be coerced to 0, not escape.
    conf_inf, refs_inf = RegressionWatchman._summarize_classification(
        {"a": {"confidence": float("inf")}, "b": {"confidence": 90}}
    )
    assert conf_inf == 45  # [0 (inf), 90] -> mean 45, never raised
    assert len(refs_inf) == 2


# ── CLEANUP-2: undeclared top-level keys survive validation (parity w/ RunCompare)
def test_log_intelligence_contract_preserves_undeclared_key():
    from app.models.agent_contracts import (
        AgentContractMetadata,
        LogIntelligenceAgentOutput,
    )

    meta = AgentContractMetadata(
        agent_name="log_intelligence",
        confidence_score=80,
        evidence_count=2,
        decision_reason="ok",
    )
    out = LogIntelligenceAgentOutput.model_validate(
        {"contract": meta, "log_summary": "s", "undeclared_nested": {"k": "v"}}
    )
    assert out.model_dump()["undeclared_nested"] == {"k": "v"}


def test_regression_watchman_contract_preserves_undeclared_key():
    from app.models.agent_contracts import (
        AgentContractMetadata,
        RegressionWatchmanAgentOutput,
    )

    meta = AgentContractMetadata(
        agent_name="regression_watchman",
        confidence_score=80,
        evidence_count=1,
        decision_reason="ok",
    )
    out = RegressionWatchmanAgentOutput.model_validate(
        {"contract": meta, "extra_workflow_key": [1, 2, 3]}
    )
    assert out.model_dump()["extra_workflow_key"] == [1, 2, 3]
