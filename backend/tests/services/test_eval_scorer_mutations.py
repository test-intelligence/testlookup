"""E9.8: prove every eval scorer rule notices its targeted mutation.

These probes are intentionally small.  Each row isolates one rule so a scorer
cannot silently become a no-op while a broader golden corpus still happens to
fail for some unrelated reason.
"""
from __future__ import annotations

import pytest

from app.services.agent_eval_harness import (
    CONF_EVIDENCE_FLOOR,
    MIN_SAMPLES,
    AgentEvalSample,
    evaluate_agent_outputs,
    score_accuracy,
    score_actionability,
    score_calibration,
    score_coherence,
    score_completeness,
)
from app.services.ai_eval_service import (
    compute_classification_metrics,
    compute_duplicate_detection_metrics,
    compute_kind_classification_metrics,
    compute_metrics_for_task_type,
    compute_release_decision_metrics,
    compute_root_cause_metrics,
)
from app.services.eval_verdict import EvalVerdict


def _sample(**changes: object) -> AgentEvalSample:
    values: dict[str, object] = {
        "sample_id": "probe",
        "verdict": "product_bug",
        "ground_truth_verdict": "product_bug",
        "confidence_score": 100,
        "evidence_count": 1,
        "decision_reason": "diagnosis with a fix",
        "recommended_actions": ["patch the defect"],
    }
    values.update(changes)
    return AgentEvalSample(**values)


def test_coherence_rejects_every_broken_rule_independently() -> None:
    # Normal construction clamps confidence, so bypass validation solely to
    # exercise the scorer's own defence-in-depth range rule.
    out_of_range = _sample().model_copy(update={"confidence_score": 101})
    cases = (
        (out_of_range, "confidence_out_of_range"),
        (_sample(verdict="flaky", confidence_score=0), "flaky_at_zero_confidence"),
        (_sample(confidence_score=1, decision_reason=""), "missing_decision_reason"),
    )

    for sample, reason in cases:
        result = score_coherence([sample])
        assert result["score"] == 0.0, reason
        assert result["violations"] == [{"sample_id": "probe", "reasons": [reason]}]


def test_completeness_pins_confidence_and_evidence_boundaries() -> None:
    broken = score_completeness([
        _sample(confidence_score=CONF_EVIDENCE_FLOOR, evidence_count=0)
    ])
    boundary_pass = score_completeness([
        _sample(confidence_score=CONF_EVIDENCE_FLOOR, evidence_count=1)
    ])

    assert broken == {"score": 0.0, "high_conf": 1, "with_evidence": 0}
    assert boundary_pass == {"score": 1.0, "high_conf": 1, "with_evidence": 1}


def test_actionability_pins_required_action_and_both_valid_paths() -> None:
    broken = score_actionability([
        _sample(decision_reason="diagnosis only", recommended_actions=[])
    ])
    explicit_action = score_actionability([
        _sample(decision_reason="diagnosis only", recommended_actions=["restart service"])
    ])
    fix_token = score_actionability([
        _sample(decision_reason="fix the null guard", recommended_actions=[])
    ])

    assert broken == {"score": 0.0, "non_flaky": 1, "with_action": 0}
    assert explicit_action["score"] == 1.0
    assert fix_token["score"] == 1.0


def test_accuracy_and_calibration_reject_a_confident_wrong_label() -> None:
    wrong = _sample(
        verdict="product_bug",
        ground_truth_verdict="infrastructure",
        confidence_score=90,
    )

    accuracy = score_accuracy([wrong])
    calibration = score_calibration([wrong])

    assert accuracy == {"score": 0.0, "correct": 0, "total": 1}
    assert calibration["brier"] == pytest.approx(0.81)
    assert calibration["ece"] == pytest.approx(0.9)


def test_calibration_keeps_full_confidence_in_the_last_bin() -> None:
    result = score_calibration([_sample(confidence_score=100)])
    assert result["bins"] == [
        {"bin": 9, "count": 1, "accuracy": 1.0, "confidence": 1.0}
    ]


def test_calibration_weights_bins_by_their_sample_share() -> None:
    result = score_calibration([
        _sample(confidence_score=10, ground_truth_verdict="infrastructure"),
        _sample(confidence_score=100),
        _sample(confidence_score=100),
    ])
    assert result["ece"] == pytest.approx(0.0333, abs=1e-4)


def test_report_cannot_pass_below_the_sample_floor() -> None:
    report = evaluate_agent_outputs([_sample()] * (MIN_SAMPLES - 1))
    assert all(report.per_metric_pass.values())
    assert report.passed is False
    assert report.verdict is EvalVerdict.INSUFFICIENT_SAMPLES


def test_classification_scorer_detects_false_labels_and_confusion_cells() -> None:
    metrics = compute_classification_metrics([
        {
            "input": {"failure_category": "A"},
            "expected_output": {"failure_category": "A", "correct": True},
        },
        {
            "input": {"failure_category": "A"},
            "expected_output": {"failure_category": "B", "correct": False},
        },
    ])

    assert metrics["correct"] == 1
    assert metrics["accuracy"] == 0.5
    assert metrics["precision"] == 0.25
    assert metrics["recall"] == 0.5
    assert metrics["f1_score"] == 0.3333


def test_kind_scorer_detects_cross_kind_labels() -> None:
    metrics = compute_kind_classification_metrics([
        {
            "input": {"failure_category": "PRODUCT_BUG"},
            "expected_output": {"failure_category": "PRODUCT_BUG"},
        },
        {
            "input": {"failure_category": "INFRASTRUCTURE"},
            "expected_output": {"failure_category": "PRODUCT_BUG"},
        },
        {
            "input": {"failure_category": "TEST_DATA"},
            "expected_output": {"failure_category": "AUTOMATION_DEFECT"},
        },
    ])

    assert metrics["computable"] is True
    # TEST_DATA and AUTOMATION_DEFECT share the test_code kind, so two of the
    # three pairs agree at the kind layer.
    assert metrics["overall"]["correct"] == 2
    assert metrics["overall"]["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["overall"]["precision"] == pytest.approx(2 / 3, abs=1e-4)


def test_root_cause_scorer_checks_presence_and_category_separately() -> None:
    metrics = compute_root_cause_metrics([
        {
            "input": {"root_cause_summary": "NPE", "failure_category": "PRODUCT_BUG"},
            "expected_output": {"has_root_cause": True, "category": "PRODUCT_BUG"},
        },
        {
            "input": {"failure_category": "PRODUCT_BUG"},
            "expected_output": {"has_root_cause": True, "category": "PRODUCT_BUG"},
        },
        {
            "input": {"root_cause_summary": "NPE", "failure_category": "PRODUCT_BUG"},
            "expected_output": {"has_root_cause": False, "category": "PRODUCT_BUG"},
        },
        {
            "input": {"root_cause_summary": "timeout", "failure_category": "PRODUCT_BUG"},
            "expected_output": {"has_root_cause": True, "category": "INFRASTRUCTURE"},
        },
        {
            "input": {
                "root_cause_summary": "connection reset",
                "failure_category": "INFRASTRUCTURE",
            },
            "expected_output": {"has_root_cause": True, "category": "INFRASTRUCTURE"},
        },
    ])

    assert metrics["correct"] == 2
    assert metrics["accuracy"] == 0.4


def test_duplicate_scorer_checks_overlap_threshold_and_component() -> None:
    metrics = compute_duplicate_detection_metrics([
        {
            "input": {
                "title_a": "timeout api",
                "title_b": "timeout api",
                "component_a": "svc",
                "component_b": "svc",
            },
            "expected_output": {"is_duplicate": True, "similarity_threshold": 0.7},
        },
        {
            "input": {
                "title_a": "timeout api",
                "title_b": "timeout api",
                "component_a": "svc-a",
                "component_b": "svc-b",
            },
            "expected_output": {"is_duplicate": False, "similarity_threshold": 0.7},
        },
        {
            "input": {
                "title_a": "timeout api",
                "title_b": "timeout database",
                "component_a": "svc",
                "component_b": "svc",
            },
            "expected_output": {"is_duplicate": False, "similarity_threshold": 0.7},
        },
    ])

    assert metrics["total"] == 3
    assert metrics["correct"] == 3
    assert metrics["accuracy"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_release_scorer_pins_both_decision_boundaries() -> None:
    metrics = compute_release_decision_metrics([
        {"input": {"risk_score": 19}, "expected_output": {"recommendation": "GO"}},
        {
            "input": {"risk_score": 20},
            "expected_output": {"recommendation": "CONDITIONAL_GO"},
        },
        {
            "input": {"risk_score": 54},
            "expected_output": {"recommendation": "CONDITIONAL_GO"},
        },
        {"input": {"risk_score": 55}, "expected_output": {"recommendation": "NO_GO"}},
    ])

    assert metrics["correct"] == 4
    assert metrics["accuracy"] == 1.0


def test_unknown_task_type_uses_classification_scorer() -> None:
    metrics = compute_metrics_for_task_type(
        "future_task",
        [
            {
                "input": {"failure_category": "PRODUCT_BUG", "risk_score": 80},
                "expected_output": {
                    "failure_category": "PRODUCT_BUG",
                    "correct": True,
                    "recommendation": "GO",
                },
            }
        ],
    )
    assert metrics["accuracy"] == 1.0
    assert "kind_metrics" in metrics
