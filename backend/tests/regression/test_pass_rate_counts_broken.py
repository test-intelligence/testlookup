"""A run with BROKEN tests must never report a 100% pass rate.

Found by exploratory testing against the live homelab (2026-08-07), on a
throwaway project ingested specifically to isolate this.

    run: 10 PASSED, 0 FAILED, 2 BROKEN, total 12

    dashboard  /metrics/summary   avg_pass_rate_7d = 100.0%   <-- false
    coverage   /analytics/coverage avg_pass_rate    =  83.3%

Two of twelve tests did not pass, and the headline said 100%. That is not a
denominator preference -- it is a false statement, and it propagates:
``_compute_readiness()`` and the release-gate pass-rate bands consume this exact
number, so a policy of "pass_rate >= 95 => GREEN" would return GREEN while a
sixth of the suite was broken by infrastructure.

Root cause: ``_period_stats`` computed ``passed / (passed + failed)`` at both
call sites, silently dropping BROKEN from the denominator.

This contradicted the codebase's own definition in three other places --
``analysis_report_service`` ("evaluated = passed + failed + broken; skips don't
count"), ``ingestion`` (``executed = passed + failed + broken``) and
``run_status`` -- and contradicted ``metrics_service`` itself fifteen lines
below the bug, where the flaky heuristic documents FAILED *or* BROKEN as "the
canonical failed set used everywhere else".

Skips stay excluded: a skipped test was never evaluated.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from app.services.metrics_service import _evaluated  # noqa: E402


def _pass_rate(passed: int, failed: int, broken: int) -> float:
    denom = _evaluated(passed, failed, broken)
    return (passed / denom * 100.0) if denom else 0.0


class TestTheHomelabRepro:
    def test_broken_tests_prevent_a_100_percent_claim(self):
        """10 passed / 0 failed / 2 broken previously reported 100%."""
        rate = _pass_rate(passed=10, failed=0, broken=2)
        assert rate < 100.0, (
            "a run with broken tests reported a 100% pass rate; two of twelve "
            "tests did not pass"
        )
        assert round(rate, 1) == 83.3, "should agree with Coverage on the same run"

    def test_it_now_agrees_with_the_coverage_page(self):
        """Coverage counts passed/total; with no skips the two must match."""
        passed, failed, broken = 10, 0, 2
        coverage_rate = passed / (passed + failed + broken) * 100.0
        assert round(_pass_rate(passed, failed, broken), 1) == round(coverage_rate, 1)


class TestBrokenIsCountedLikeAFailure:
    @pytest.mark.parametrize(
        "passed,failed,broken,expected",
        [
            (10, 0, 2, 83.3),    # the repro
            (10, 2, 0, 83.3),    # same shape, broken<->failed: identical rate
            (0, 0, 5, 0.0),      # everything broken is 0%, not undefined-as-100
            (8, 1, 1, 80.0),
            (12, 0, 0, 100.0),   # a genuinely clean run still reads 100%
        ],
    )
    def test_broken_and_failed_move_the_rate_identically(self, passed, failed, broken, expected):
        assert round(_pass_rate(passed, failed, broken), 1) == expected

    def test_a_fully_broken_run_is_not_reported_as_perfect(self):
        """The nastiest case: zero failures only because nothing ran cleanly."""
        assert _pass_rate(passed=0, failed=0, broken=12) == 0.0


class TestSkipsStayOutOfTheDenominator:
    def test_evaluated_excludes_skips_by_construction(self):
        """``_evaluated`` takes no skip argument -- skips cannot leak in."""
        assert _evaluated(10, 1, 1) == 12

    def test_skipping_a_test_does_not_change_the_rate(self):
        """A suite of 12 where 2 are skipped scores on the 10 evaluated."""
        assert round(_pass_rate(passed=9, failed=1, broken=0), 1) == 90.0


class TestDegenerate:
    def test_no_evaluated_tests_yields_zero_not_a_crash(self):
        assert _pass_rate(0, 0, 0) == 0.0
        assert _evaluated(0, 0, 0) == 0


def test_both_call_sites_use_the_shared_helper():
    """Pins the fix at both branches of ``_period_stats``.

    The suite-filtered branch and the unfiltered branch each computed the
    denominator independently; fixing only one would leave the false 100%
    reachable through a suite filter.
    """
    import inspect

    from app.services import metrics_service

    src = inspect.getsource(metrics_service._period_stats)
    assert src.count("_evaluated(") == 2, (
        "both branches of _period_stats must derive the denominator from "
        "_evaluated(); a hand-rolled 'passed + failed' reintroduces the bug"
    )
    assert "sum_passed + sum_failed" not in src, (
        "found a hand-rolled denominator that omits BROKEN"
    )
