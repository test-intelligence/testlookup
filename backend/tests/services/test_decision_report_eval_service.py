from app.services.decision_report_eval_service import evaluate_decision_report_quality


def _report(**overrides):
    report = {
        "metrics": {"total_tests": 2, "failed_tests": 1},
        "metric_snapshot": {"content_sha256": "m" * 64},
        "run_evidence_bundle_sha256": "b" * 64,
        "claims": [
            {
                "claim_id": "fact-1",
                "kind": "fact",
                "evidence": [{"type": "decision_evidence", "id": "b" * 64}],
            },
        ],
        "quality_review": {"contradictions": []},
    }
    report.update(overrides)
    return report


def test_clean_report_is_grounded_but_optional_metrics_are_explicitly_unavailable():
    result = evaluate_decision_report_quality(_report())

    assert result["status"] == "warn"
    assert result["metrics"]["citation_validity"] == 1.0
    assert result["metrics"]["groundedness"] == 1.0
    assert "calibration" in result["unavailable_metrics"]
    assert "utility" in result["unavailable_metrics"]


def test_invalid_material_citation_fails_closed():
    result = evaluate_decision_report_quality(_report(
        claims=[{
            "claim_id": "bad",
            "kind": "inference",
            "evidence": [{"type": "artifact", "id": "foreign"}],
        }],
    ))

    assert result["status"] == "fail"
    citation = next(item for item in result["checks"] if item["name"] == "citation_validity")
    assert citation["status"] == "fail"
    assert citation["detail"]["invalid_claim_ids"] == ["bad"]


def test_policy_contradiction_is_hard_failure_but_general_rate_is_visible():
    result = evaluate_decision_report_quality(_report(
        quality_review={"contradictions": [{"type": "release_policy_contradiction"}]},
    ))

    assert result["status"] == "fail"
    policy = next(item for item in result["checks"] if item["name"] == "policy_contradictions")
    assert policy["status"] == "fail"


def test_feedback_utility_is_measured_without_accepting_malformed_counts():
    result = evaluate_decision_report_quality(
        _report(),
        feedback_summary={"sample_count": 10, "useful_count": 8, "partially_useful_count": 1},
    )
    utility = next(item for item in result["checks"] if item["name"] == "utility")
    assert utility["status"] == "pass"
    assert result["metrics"]["utility_rate"] == 0.9

    malformed = evaluate_decision_report_quality(
        _report(),
        feedback_summary={"sample_count": 10, "useful_count": "8", "partially_useful_count": 1},
    )
    assert "utility" in malformed["unavailable_metrics"]


def test_action_governance_metrics_are_visible_and_warn_on_unresolved_rows():
    result = evaluate_decision_report_quality(
        _report(),
        action_summary={
            "action_count": 2,
            "terminal_count": 1,
            "unresolved_count": 1,
            "pending_review_count": 1,
        },
    )

    check = next(item for item in result["checks"] if item["name"] == "action_governance")
    assert check["status"] == "warn"
    assert result["metrics"]["action_count"] == 2
    assert result["metrics"]["action_resolution_rate"] == 0.5


def test_action_governance_rejects_inconsistent_summary():
    result = evaluate_decision_report_quality(
        _report(),
        action_summary={"action_count": 2, "terminal_count": 2, "unresolved_count": 2},
    )
    check = next(item for item in result["checks"] if item["name"] == "action_governance")
    assert check["status"] == "not_evaluated"
    assert "action_governance" in result["unavailable_metrics"]
