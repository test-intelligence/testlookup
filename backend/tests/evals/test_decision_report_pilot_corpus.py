"""Representative local DecisionReport corpus for the report-level evaluator.

This is a structural/calibration fixture, not a substitute for approved pilot
labels. It proves that a grounded, actionable five-report corpus can produce a
passing evaluator cycle while malformed citations and policy contradictions
remain hard failures.
"""
from __future__ import annotations

from app.services.agent_eval_harness import AgentEvalSample
from app.services.decision_report_eval_service import evaluate_decision_report_quality


def _report(index: int, *, invalid: bool = False, contradiction: bool = False) -> dict:
    evidence_id = f"pilot-evidence-{index}"
    return {
        "schema_version": 1,
        "status": "complete",
        "claims": [{
            "claim_id": f"claim-{index}",
            "kind": "recommendation",
            "claim": "Investigate the failing test cluster before release.",
            "confidence": 0.9,
            "confidence_basis": "failure cluster and release evidence",
            "evidence": [{"evidence_id": "wrong-id" if invalid else evidence_id}],
        }],
        "quality_review": {
            "contradictions": ([{
                "type": "release_policy_contradiction",
                "detail": "release policy forbids publication",
            }] if contradiction else []),
        },
        "metric_snapshot": {"content_sha256": f"metric-{index}"},
    }


def _samples() -> list[AgentEvalSample]:
    return [AgentEvalSample(
        sample_id=f"pilot-{index}",
        agent_name="decision_report",
        verdict="regression",
        confidence_score=90,
        evidence_count=1,
        decision_reason="fix the failing cluster before release",
        recommended_actions=["triage the cluster"],
        ground_truth_verdict="regression",
    ) for index in range(5)]


def test_representative_report_corpus_passes_grounded_calibration_gate():
    reports = [_report(index) for index in range(5)]
    results = [
        evaluate_decision_report_quality(
            report,
            authorized_evidence_ids={f"pilot-evidence-{index}"},
            eval_samples=_samples(),
            feedback_summary={"sample_count": 5, "useful_count": 5, "partially_useful_count": 0},
        )
        for index, report in enumerate(reports)
    ]

    assert all(result["status"] == "pass" for result in results)
    assert all(result["metrics"]["citation_validity"] == 1.0 for result in results)
    assert all(result["checks"][-2]["status"] == "pass" for result in results)


def test_report_corpus_rejects_invalid_citation_and_policy_contradiction():
    invalid = evaluate_decision_report_quality(
        _report(1, invalid=True), authorized_evidence_ids={"pilot-evidence-1"}
    )
    contradiction = evaluate_decision_report_quality(
        _report(2, contradiction=True), authorized_evidence_ids={"pilot-evidence-2"}
    )

    assert invalid["status"] == "fail"
    assert any(item["name"] == "citation_validity" and item["status"] == "fail" for item in invalid["checks"])
    assert contradiction["status"] == "fail"
    assert any(item["name"] == "policy_contradictions" and item["status"] == "fail" for item in contradiction["checks"])
