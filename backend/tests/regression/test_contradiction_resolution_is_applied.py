"""Regression guard: a recorded contradiction resolution must be acted on.

The defect
----------
``report_refinement_agent`` detects cross-route disagreements and records a
``ResolutionStrategy`` for each. It then built the reconciled per-test record
with ``_primary_route(routes)``, a FIXED precedence (analysis > anomaly >
cluster) that never looked at the strategy.

FLAKY_VS_REGRESSION only fires when the test HAS an analysis entry
(``analyses[tid]["is_flaky"] is True``), so ``"analysis"`` is always in
``routes`` and always wins the precedence. Every contradiction labelled
**PREFER_ANOMALY** was therefore reconciled as ``primary_route="analysis"``
with ``is_flaky=True`` -- the exact opposite of its own label. A test the
anomaly/regression route had identified as a real regression was handed
downstream as flaky.

And it counted as a success. ``contradictions_resolved`` was derived from
``resolution != FLAG_FOR_REVIEW``; the agent only ever assigns PREFER_ANOMALY
or MERGE, so the count was structurally equal to the number of contradictions
found, ``unresolved_count`` was always 0, and the evidence ref's
``contribution`` was always 100. Three numbers that could not report a
problem, over a reconciliation that was doing the opposite of what it said.

What is guarded
---------------
1. The dangerous direction: a regression is not reconciled as flaky.
2. The counts are derived from what was APPLIED, not from what was labelled --
   so a strategy added tomorrow with no application branch is reported as
   unresolved instead of silently claiming success.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.regression

from app.agents.report_refinement_agent import ReportRefinementAgent  # noqa: E402
from app.models.agent_contracts import (  # noqa: E402
    Contradiction,
    ContradictionType,
    RefinedReport,
    ResolutionStrategy,
)


def _flaky_vs_regression(tid="t1", routes=("analysis", "anomaly")):
    return Contradiction(
        test_id=tid,
        type=ContradictionType.FLAKY_VS_REGRESSION,
        routes=list(routes),
        resolution=ResolutionStrategy.PREFER_ANOMALY,
        detail="flaky_analysis_vs_regression_signal",
    )


def test_a_regression_is_not_handed_downstream_as_flaky():
    contradiction = _flaky_vs_regression()
    record = ReportRefinementAgent._reconcile(
        "t1", ["analysis", "anomaly"], {"is_flaky": True}, contradiction,
    )
    assert record["is_flaky"] is False, (
        "the anomaly route identified a real regression and PREFER_ANOMALY was "
        "recorded, but the reconciled record still says flaky -- the label and "
        "the behaviour disagree, and the flaky one is the dangerous direction"
    )
    assert record["primary_route"] == "anomaly", record
    assert contradiction.applied is True


def test_the_strategy_is_marked_applied_only_when_it_could_act():
    """PREFER_ANOMALY with no anomaly route cannot be honoured."""
    contradiction = _flaky_vs_regression(routes=("analysis", "cluster"))
    record = ReportRefinementAgent._reconcile(
        "t1", ["analysis", "cluster"], {"is_flaky": True}, contradiction,
    )
    assert contradiction.applied is False, (
        "claimed to prefer the anomaly route on a test that has no anomaly route"
    )
    assert record["resolved_by"] is None


def test_merge_keeps_the_disagreement_visible():
    contradiction = Contradiction(
        test_id="t2",
        type=ContradictionType.CATEGORY_DISAGREEMENT,
        routes=["analysis", "anomaly"],
        resolution=ResolutionStrategy.MERGE,
        detail="analysis_category_vs_anomaly_route",
    )
    record = ReportRefinementAgent._reconcile(
        "t2", ["analysis", "anomaly"], {"failure_category": "ASSERTION"}, contradiction,
    )
    assert record["merged_routes"] == ["analysis", "anomaly"], record
    assert contradiction.applied is True


def test_a_test_with_no_contradiction_keeps_the_plain_precedence():
    record = ReportRefinementAgent._reconcile(
        "t3", ["analysis", "cluster"], {"is_flaky": True}, None,
    )
    assert record["primary_route"] == "analysis"
    assert record["is_flaky"] is True


def test_resolved_counts_what_was_applied_not_what_was_labelled():
    """The class: a strategy nothing acts on must read as UNRESOLVED.

    This is the guard that the old label-derived count could never provide --
    it returned `total` no matter what the reconciliation actually did.
    """
    labelled_only = _flaky_vs_regression(tid="t1")   # applied defaults to False
    acted_on = _flaky_vs_regression(tid="t2")
    acted_on.applied = True

    report = RefinedReport(contradictions=[labelled_only, acted_on])

    assert report.contradictions_resolved == 1, (
        "a contradiction that was merely labelled with a strategy is counted as "
        "resolved; that count is then structurally equal to the number of "
        "contradictions found and can never report a problem"
    )
    assert report.unresolved_count == 1, report.unresolved_count


def test_flag_for_review_is_never_counted_as_resolved():
    flagged = Contradiction(
        test_id="t9",
        type=ContradictionType.CATEGORY_DISAGREEMENT,
        routes=["analysis", "anomaly"],
        resolution=ResolutionStrategy.FLAG_FOR_REVIEW,
    )
    flagged.applied = True  # even if something wrongly marks it applied
    report = RefinedReport(contradictions=[flagged])
    assert report.contradictions_resolved == 0
    assert report.unresolved_count == 1
