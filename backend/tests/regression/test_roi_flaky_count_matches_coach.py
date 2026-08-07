"""The ROI ``flaky_tests_identified`` figure must match the coach headline.

Completes F-010, which was fixed **incompletely**. That fix corrected the coach's
``total_flaky`` response field but left ``value_metrics_service`` counting rows:

    flaky_stmt = select(sa_func.count(FlakyCoachResult.id))

So on 30 days of backdated history the same project reported::

    flaky coach   total_flaky              = 1     (one genuinely alternating test)
    ROI           flaky_tests_identified   = 5     (every row in the table)

Exposed by seeding **multi-day** data — the previous 5-run, single-timestamp
fixture could not produce a coach table with a mix of oscillating and
persistently-broken entries, so the divergence had nowhere to show.

Why the two numbers legitimately differ in *source*: the coach table
deliberately KEEPS persistent regressions, which carry a downgraded
recommendation and "treat as a regression, not a flake" advice (FLK-P1). Their
presence as rows is correct; counting them as *flaky* is not — and unlike a UI
disagreement, this one is an ROI figure that gets quoted.

Fix: both surfaces call ``test_health_coach_service.history_is_intermittent``,
extracted from the coach so a third definition cannot appear.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.services.test_health_coach_service import (  # noqa: E402
    MANUAL_TRIAGE_HISTORY_SENTINEL,
    history_is_intermittent,
)


class TestTheSharedPredicate:
    @pytest.mark.parametrize(
        "history,expected,why",
        [
            (["FAILED", "PASSED", "FAILED", "PASSED"], True, "alternates"),
            (["PASSED", "PASSED", "FAILED", "FAILED"], False, "broke once, stayed broken"),
            (["FAILED", "FAILED", "PASSED", "PASSED"], False, "got fixed — one transition"),
            (["PASSED"] * 6, False, "never failed"),
            (["FAILED"] * 6, False, "never passed"),
            (["PASSED", "BROKEN", "PASSED", "BROKEN"], True, "BROKEN counts as a failure"),
            ([], False, "no history"),
            (None, False, "null history must not raise"),
        ],
    )
    def test_classification(self, history, expected, why):
        assert history_is_intermittent(history) is expected, why

    def test_manual_triage_always_counts(self):
        """A human calling a test flaky outranks the heuristic."""
        assert history_is_intermittent([MANUAL_TRIAGE_HISTORY_SENTINEL]) is True


class TestBothSurfacesUseIt:
    def test_roi_uses_the_shared_predicate(self):
        from app.services import value_metrics_service

        src = inspect.getsource(value_metrics_service)
        assert "history_is_intermittent" in src, (
            "ROI still counts every FlakyCoachResult row, so flaky_tests_identified "
            "disagrees with the coach headline"
        )

    def test_roi_no_longer_bare_counts_the_table(self):
        from app.services import value_metrics_service

        src = inspect.getsource(value_metrics_service)
        assert "flaky_stmt = select(sa_func.count(FlakyCoachResult.id))" not in src, (
            "the bare COUNT(*) that produced the 5-vs-1 disagreement is back"
        )

    def test_coach_uses_the_shared_predicate(self):
        from app.services import test_health_coach_service as coach

        src = inspect.getsource(coach.get_flaky_coach)
        assert "history_is_intermittent" in src, (
            "the coach headline no longer shares the predicate with ROI"
        )

    def test_the_quarantine_count_is_deliberately_separate(self):
        """``quarantine_recommended`` counts QUARANTINE rows and must stay that
        way — it is a different question from 'is this flaky'."""
        from app.services import value_metrics_service

        src = inspect.getsource(value_metrics_service)
        assert 'quarantine_recommendation == "QUARANTINE"' in src


def test_the_two_numbers_agree_on_a_mixed_table():
    """The measured case: 5 rows, 1 oscillating.

    Mirrors the live 30-day probe — one alternating test, one regression that
    broke and stayed broken, two dip tests with a single transition, one infra
    window that recovered.
    """
    rows = [
        ["FAILED", "PASSED"] * 5,                       # alternates → flaky
        ["PASSED"] * 12 + ["FAILED"] * 12,              # regression → not flaky
        ["PASSED"] * 12 + ["FAILED"] * 9,               # dip → one transition
        ["PASSED"] * 20 + ["FAILED"] * 4,               # dip → one transition
        ["PASSED"] * 10 + ["BROKEN"] * 5 + ["PASSED"] * 5,  # infra, recovered → flaky
    ]
    counted = sum(1 for h in rows if history_is_intermittent(h))
    assert counted == 2, (
        f"expected the 2 oscillating rows to count as flaky, got {counted}; "
        "a bare row count would say 5"
    )
