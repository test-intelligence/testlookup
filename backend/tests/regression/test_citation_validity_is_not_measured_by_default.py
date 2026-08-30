"""Regression: `citation_validity` reported "pass" without measuring anything.

``_citation_metrics`` returns ``citation_validity: None`` when the report has
no material claims -- an empty report, or one whose claims did not parse. The
caller then evaluated::

    "fail" if invalid_claims or (citation_value is not None and citation_value < MIN) else "pass"

With no claims, ``invalid_claims`` is ``[]`` and the second operand
short-circuits to ``False``, so the whole expression yields **"pass"**. That is
a clean bill of health from a check that never ran -- and it is the one metric
this evaluator treats as a *hard* failure (the module docstring: "Citation/
reference failures and explicit policy contradictions are hard failures").

The two sibling metrics computed from the very same dict, ``groundedness`` and
``contradiction_rate``, already handled ``None`` by reporting
``not_evaluated`` and adding themselves to ``unavailable_metrics``. That
asymmetry is what makes this an oversight rather than a decision.

See [[feedback_absence_is_not_health]]: 0 entries meant "perfect" and "never
ran" alike.
"""
from __future__ import annotations

import pytest

from app.services.decision_report_eval_service import evaluate_decision_report_quality

pytestmark = pytest.mark.regression


def _check(result: dict, name: str) -> dict:
    for item in result["checks"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"no {name} check in {[c['name'] for c in result['checks']]}")


def _grounded_report() -> dict:
    """One material claim, correctly cited — the measurable baseline."""
    return {
        "schema_version": 1,
        "status": "complete",
        "claims": [{
            "claim_id": "claim-1",
            "kind": "recommendation",
            "claim": "Investigate the failing cluster before release.",
            "confidence": 0.9,
            "confidence_basis": "failure cluster evidence",
            "evidence": [{"evidence_id": "evidence-1"}],
        }],
        "quality_review": {"contradictions": []},
        "metric_snapshot": {"content_sha256": "metric-1"},
    }


class TestUnmeasurableReport:
    """A report with nothing to measure must not be reported as verified."""

    @pytest.mark.parametrize(
        "report,label",
        [
            ({"schema_version": 1, "status": "complete", "claims": []}, "empty claims list"),
            ({"schema_version": 1, "status": "complete"}, "no claims key at all"),
            ({}, "empty report"),
        ],
    )
    def test_citation_validity_is_not_evaluated_rather_than_passing(self, report, label):
        result = evaluate_decision_report_quality(report, authorized_evidence_ids={"evidence-1"})

        check = _check(result, "citation_validity")
        assert check["status"] == "not_evaluated", (
            f"{label}: citation_validity claimed {check['status']!r} having measured nothing"
        )

    def test_the_unmeasured_metric_names_itself(self):
        """`unavailable_metrics` is how a reader learns which checks are blind."""
        result = evaluate_decision_report_quality(
            {"schema_version": 1, "status": "complete", "claims": []},
            authorized_evidence_ids=set(),
        )
        assert "citation_validity" in result["unavailable_metrics"]

    def test_the_cycle_does_not_report_pass(self):
        """The aggregate reads not_evaluated as "warn", so an unmeasurable
        report can no longer present as a clean pass."""
        result = evaluate_decision_report_quality(
            {"schema_version": 1, "status": "complete", "claims": []},
            authorized_evidence_ids=set(),
        )
        assert result["status"] != "pass"

    def test_it_matches_how_its_siblings_already_behaved(self):
        """groundedness and contradiction_rate were already correct on the same
        input. Pinning all three together stops the next one from drifting."""
        result = evaluate_decision_report_quality(
            {"schema_version": 1, "status": "complete", "claims": []},
            authorized_evidence_ids=set(),
        )
        for name in ("citation_validity", "groundedness", "contradiction_rate"):
            assert _check(result, name)["status"] == "not_evaluated", name


class TestMeasurableReportStillJudged:
    """The fix must not turn the check off — that would trade one silence for
    another."""

    def test_a_correctly_cited_claim_still_passes(self):
        result = evaluate_decision_report_quality(
            _grounded_report(), authorized_evidence_ids={"evidence-1"}
        )
        check = _check(result, "citation_validity")
        assert check["status"] == "pass"
        assert "citation_validity" not in result["unavailable_metrics"]

    def test_a_claim_citing_unauthorized_evidence_still_fails(self):
        """The hard failure this metric exists for."""
        report = _grounded_report()
        report["claims"][0]["evidence"] = [{"evidence_id": "not-authorized"}]

        result = evaluate_decision_report_quality(
            report, authorized_evidence_ids={"evidence-1"}
        )
        check = _check(result, "citation_validity")
        assert check["status"] == "fail"
        assert check["detail"]["invalid_claim_ids"] == ["claim-1"]
        assert result["status"] == "fail"
