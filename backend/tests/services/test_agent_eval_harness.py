"""Unit coverage for the report-quality eval harness (AIQ-P5).

Companion to the architectural gate (``tests/test_architectural_agent_eval_harness.py``).
This file exercises the harness pieces directly — ``AgentEvalSample.from_recorded_output``
extraction, each metric function in isolation, the sync adapter
``compute_agent_report_quality``, and the gate-rule projection
``evaluate_agent_report_quality_rules``.

Pure and DB-free: imports the harness + adapters and asserts on returned
values. No fixtures, sessions, or network.
"""
from __future__ import annotations

import pytest

from app.services.agent_eval_harness import (
    CONF_EVIDENCE_FLOOR,
    AgentEvalSample,
    score_accuracy,
    score_actionability,
    score_calibration,
    score_coherence,
    score_completeness,
)
from app.services.ai_eval_service import compute_agent_report_quality
from app.services.eval_gate_service import evaluate_agent_report_quality_rules


# ── AgentEvalSample.from_recorded_output extraction ──────────────────────────


def test_from_recorded_output_extracts_contract_block() -> None:
    """The per-agent ``agent_contracts`` block supplies confidence /
    evidence_count / decision_reason; payload-level verdict + recommended
    actions are read off the payload root."""
    payload = {
        "sample_id": "s1",
        "verdict": "Product_Bug",
        "recommended_actions": ["patch the handler"],
        "agent_contracts": {
            "AnalysisAgent": {
                "confidence_score": 80,
                "evidence_count": 4,
                "evidence_refs": [{"ref": "a"}, {"ref": "b"}],
                "decision_reason": "NPE in processRefund",
            },
        },
    }
    sample = AgentEvalSample.from_recorded_output(
        payload,
        agent_name="AnalysisAgent",
        ground_truth={"verdict": "product_bug", "is_flaky": False},
    )

    assert sample.sample_id == "s1"
    assert sample.agent_name == "AnalysisAgent"
    # Verdicts are lowercased by the validator.
    assert sample.verdict == "product_bug"
    assert sample.confidence_score == 80
    # An explicit evidence_count wins over the len(evidence_refs) derivation.
    assert sample.evidence_count == 4
    assert sample.decision_reason == "NPE in processRefund"
    assert sample.recommended_actions == ["patch the handler"]
    assert sample.ground_truth_verdict == "product_bug"
    assert sample.is_flaky_truth is False


def test_from_recorded_output_derives_evidence_count_from_refs() -> None:
    """When ``evidence_count`` is absent, it is derived from
    ``len(evidence_refs)``."""
    payload = {
        "verdict": "infrastructure",
        "agent_contracts": {
            "AnalysisAgent": {
                "confidence_score": 70,
                "evidence_refs": [{"ref": "a"}, {"ref": "b"}, {"ref": "c"}],
                "decision_reason": "pool exhausted",
            },
        },
    }
    sample = AgentEvalSample.from_recorded_output(
        payload,
        agent_name="AnalysisAgent",
        ground_truth={"verdict": "infrastructure"},
    )
    assert sample.evidence_count == 3


def test_from_recorded_output_defaults_when_contract_missing() -> None:
    """A missing agent_contracts block degrades every contract field to its
    default without raising; verdict / actions still come off the payload."""
    payload = {
        "sample_id": "s2",
        "verdict": "flaky",
        "recommended_actions": ["nothing to do"],
    }
    sample = AgentEvalSample.from_recorded_output(
        payload,
        agent_name="AnalysisAgent",
        ground_truth={"verdict": "flaky", "is_flaky": True},
    )
    assert sample.confidence_score == 0
    assert sample.evidence_count == 0
    assert sample.decision_reason == ""
    assert sample.verdict == "flaky"
    assert sample.recommended_actions == ["nothing to do"]
    assert sample.is_flaky_truth is True


# ── score_coherence ──────────────────────────────────────────────────────────


def test_score_coherence_empty_is_one() -> None:
    result = score_coherence([])
    assert result["score"] == 1.0
    assert result["total"] == 0


def test_score_coherence_flaky_at_zero_confidence_violates() -> None:
    result = score_coherence([
        AgentEvalSample(sample_id="f", verdict="flaky", confidence_score=0),
    ])
    assert result["score"] == 0.0
    assert result["violations"][0]["reasons"] == ["flaky_at_zero_confidence"]


def test_score_coherence_empty_reason_at_positive_confidence_violates() -> None:
    result = score_coherence([
        AgentEvalSample(
            sample_id="r", verdict="product_bug", confidence_score=50, decision_reason=""
        ),
    ])
    assert result["score"] == 0.0
    assert result["violations"][0]["reasons"] == ["missing_decision_reason"]


def test_score_coherence_clean_sample_passes() -> None:
    result = score_coherence([
        AgentEvalSample(
            sample_id="ok",
            verdict="product_bug",
            confidence_score=50,
            decision_reason="a real reason",
        ),
    ])
    assert result["score"] == 1.0
    assert result["violations"] == []


# ── score_completeness ───────────────────────────────────────────────────────


def test_score_completeness_empty_denominator_is_one() -> None:
    """No high-confidence samples -> empty denominator -> 1.0."""
    result = score_completeness([
        AgentEvalSample(verdict="flaky", confidence_score=CONF_EVIDENCE_FLOOR - 1),
    ])
    assert result["score"] == 1.0
    assert result["high_conf"] == 0


def test_score_completeness_high_conf_no_evidence_fails() -> None:
    result = score_completeness([
        AgentEvalSample(
            verdict="product_bug", confidence_score=CONF_EVIDENCE_FLOOR, evidence_count=0
        ),
    ])
    assert result["score"] == 0.0
    assert result["high_conf"] == 1
    assert result["with_evidence"] == 0


def test_score_completeness_high_conf_with_evidence_passes() -> None:
    result = score_completeness([
        AgentEvalSample(verdict="product_bug", confidence_score=90, evidence_count=2),
    ])
    assert result["score"] == 1.0


# ── score_actionability ──────────────────────────────────────────────────────


def test_score_actionability_empty_denominator_is_one() -> None:
    """All-flaky -> no non-flaky verdicts -> empty denominator -> 1.0."""
    result = score_actionability([
        AgentEvalSample(verdict="flaky", confidence_score=90),
    ])
    assert result["score"] == 1.0
    assert result["non_flaky"] == 0


def test_score_actionability_fix_token_in_reason_satisfies() -> None:
    """A non-flaky verdict with no recommended action still passes if the
    decision_reason carries a 'fix' token."""
    result = score_actionability([
        AgentEvalSample(
            verdict="product_bug",
            confidence_score=90,
            decision_reason="NPE; fix the null guard",
            recommended_actions=[],
        ),
    ])
    assert result["score"] == 1.0
    assert result["with_action"] == 1


def test_score_actionability_nonflaky_no_action_no_fix_fails() -> None:
    result = score_actionability([
        AgentEvalSample(
            verdict="product_bug",
            confidence_score=90,
            decision_reason="a bug exists somewhere",
            recommended_actions=[],
        ),
    ])
    assert result["score"] == 0.0
    assert result["non_flaky"] == 1
    assert result["with_action"] == 0


# ── score_accuracy ───────────────────────────────────────────────────────────


def test_score_accuracy_correct_over_total() -> None:
    samples = [
        AgentEvalSample(verdict="product_bug", ground_truth_verdict="product_bug"),
        AgentEvalSample(verdict="flaky", ground_truth_verdict="flaky"),
        AgentEvalSample(verdict="product_bug", ground_truth_verdict="infrastructure"),
    ]
    result = score_accuracy(samples)
    assert result["correct"] == 2
    assert result["total"] == 3
    assert result["score"] == pytest.approx(2 / 3)


def test_score_accuracy_empty_is_none() -> None:
    result = score_accuracy([])
    assert result["score"] is None
    assert result["total"] == 0


# ── score_calibration ────────────────────────────────────────────────────────


def test_score_calibration_empty_brier_ece_none() -> None:
    result = score_calibration([])
    assert result["brier"] is None
    assert result["ece"] is None
    assert result["n"] == 0
    assert result["bins"] == []


def test_score_calibration_bins_detail_shape() -> None:
    """Bin detail rows carry bin index / count / accuracy / confidence, and
    a conf=100 sample lands in the last bin (index 9), not an overflow bin."""
    samples = [
        AgentEvalSample(confidence_score=100, verdict="a", ground_truth_verdict="a"),
        AgentEvalSample(confidence_score=95, verdict="a", ground_truth_verdict="a"),
    ]
    result = score_calibration(samples)
    assert result["n"] == 2
    assert result["brier"] == pytest.approx(((0.0) ** 2 + (0.05) ** 2) / 2, abs=1e-4)
    bins = {b["bin"]: b for b in result["bins"]}
    assert 9 in bins
    assert bins[9]["count"] == 2
    assert bins[9]["accuracy"] == 1.0
    assert set(bins[9]) == {"bin", "count", "accuracy", "confidence"}


# ── compute_agent_report_quality (sync adapter) ──────────────────────────────


def test_compute_agent_report_quality_mixed_list_is_json_safe() -> None:
    """A mixed list (AgentEvalSample + dict + garbage str + None + int) yields a
    JSON-able dict; sample_count counts only the coercible items and the helper
    never raises."""
    import json

    mixed = [
        AgentEvalSample(confidence_score=50, verdict="a", ground_truth_verdict="a"),
        {"confidence_score": 60, "verdict": "b", "ground_truth_verdict": "b"},
        "garbage",
        None,
        7,
    ]
    report = compute_agent_report_quality(mixed)

    assert isinstance(report, dict)
    # Only the AgentEvalSample + the dict are coercible.
    assert report["sample_count"] == 2
    # JSON-serialisable (no None-keyed surprises, all leaves primitive).
    json.dumps(report)
    assert "per_metric_pass" in report
    assert "passed" in report


def test_compute_agent_report_quality_non_list_is_empty() -> None:
    """A non-list / None input coerces to an empty sample set -> sample_count 0,
    never raises."""
    for bad in (None, "nope", 123, {"not": "a list"}):
        report = compute_agent_report_quality(bad)
        assert isinstance(report, dict)
        assert report["sample_count"] == 0
        assert report["passed"] is False


# ── evaluate_agent_report_quality_rules (gate-rule projection) ───────────────


_METRIC_RULES = {
    "agent_coherence",
    "agent_completeness",
    "agent_actionability",
    "agent_accuracy",
    "agent_brier",
    "agent_ece",
}


def test_report_quality_rules_emit_six_metrics_plus_overall() -> None:
    """A full report dict projects to the 6 metric rules + the overall
    agent_report_quality rule."""
    report = {
        "coherence": 1.0,
        "completeness": 1.0,
        "actionability": 1.0,
        "accuracy": 0.9,
        "brier": 0.1,
        "ece": 0.05,
        "sample_count": 10,
        "per_metric_pass": {
            "coherence": True,
            "completeness": True,
            "actionability": True,
            "accuracy": True,
            "brier": True,
            "ece": True,
        },
        "passed": True,
    }
    rules = evaluate_agent_report_quality_rules(report)
    by_rule = {r["rule"]: r for r in rules}

    assert _METRIC_RULES <= set(by_rule)
    assert "agent_report_quality" in by_rule
    assert len(rules) == 7
    assert all(r["passed"] for r in rules)


def test_report_quality_rules_handle_degenerate_inputs() -> None:
    """Empty dict, None, and a report missing per_metric_pass must all return
    the 6 metric rules + overall and never raise (defaulting to failed)."""
    for bad in ({}, None, {"passed": True}):  # last: per_metric_pass missing
        rules = evaluate_agent_report_quality_rules(bad)
        by_rule = {r["rule"]: r for r in rules}
        assert _METRIC_RULES <= set(by_rule)
        assert "agent_report_quality" in by_rule
        # Missing per_metric_pass -> every metric rule defaults to failed.
        for metric in _METRIC_RULES:
            assert by_rule[metric]["passed"] is False


def test_report_quality_rules_wrong_agent_fails_accuracy_and_overall() -> None:
    """A wrong-agent report (accuracy below threshold) projects to a failed
    agent_accuracy rule and a failed overall rule."""
    from app.services.agent_eval_harness import evaluate_agent_outputs
    from app.services.golden_agent_outputs import get_wrong_agent_samples

    report = evaluate_agent_outputs(get_wrong_agent_samples()).model_dump(mode="json")
    rules = evaluate_agent_report_quality_rules(report)
    by_rule = {r["rule"]: r for r in rules}

    assert by_rule["agent_accuracy"]["passed"] is False
    assert by_rule["agent_report_quality"]["passed"] is False
