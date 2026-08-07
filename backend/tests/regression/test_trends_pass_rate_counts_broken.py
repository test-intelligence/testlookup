"""The trend line must use the same pass-rate denominator as the headline.

Completes PR #464, which was **incomplete**. That PR fixed ``_period_stats``
(the dashboard headline) to count BROKEN in the pass-rate denominator, but
``get_trend_data`` in the *same module* computes its own daily pass rate in SQL
and still excluded BROKEN. The result, live, for one window::

    GET /metrics/summary  -> avg_pass_rate_7d = 81.0    (47 / 58)
    GET /metrics/trends   -> pass_rate        = 83.9    (47 / 56)

Same project, same 7 days, two numbers. Fixing one surface and not the other
did not just leave a bug -- it *created* a disagreement that did not exist
before, between a headline and the chart directly beneath it.

The trends query already SELECTed ``broken`` for display while omitting it from
its own denominator, which is the tell.

Worth naming the process failure: #464 shipped a guard asserting **both branches
of ``_period_stats``** used the shared helper. That guard was scoped to one
function, so it passed while a sibling function in the same file kept the bug.
The guard here is module-wide instead.

Skips remain excluded everywhere: a skipped test was never evaluated.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import metrics_service  # noqa: E402
from app.services.metrics_service import _evaluated  # noqa: E402


def _trend_sql() -> str:
    return inspect.getsource(metrics_service.get_trend_data)


def _denominator() -> str:
    """The pass-rate denominator ONLY.

    Every assertion below works on this, not on the whole query. Two earlier
    versions of these tests searched the full SQL and passed against the *buggy*
    source: one matched the ``broken`` display column
    (``COALESCE(SUM(tr.broken_tests), 0) AS broken``) rather than the
    denominator, and the other used a ``NULLIF(`` pattern with a space the real
    SQL does not contain. Both were assertions that could not fail.
    """
    collapsed = re.sub(r"\s+", " ", _trend_sql())
    m = re.search(r"NULLIF\((.*?), 0\s*\)", collapsed)
    assert m, "could not locate the pass-rate denominator — update this test"
    return m.group(1)


class TestTrendSqlCountsBroken:
    def test_denominator_includes_broken_tests(self):
        denom = _denominator()
        assert "broken_tests" in denom, (
            f"the trend pass-rate denominator omits broken_tests: {denom}"
        )

    def test_the_old_two_term_denominator_is_gone(self):
        """The exact shape that produced 83.9 while the headline said 81.0."""
        denom = _denominator()
        terms = set(re.findall(r"(passed|failed|broken|skipped)_tests", denom))
        assert terms == {"passed", "failed", "broken"}, (
            f"denominator terms are {sorted(terms)}; expected exactly "
            "passed+failed+broken (skips are never evaluated)"
        )

    def test_skips_are_still_excluded_from_the_denominator(self):
        denom = _denominator()
        assert "skipped_tests" not in denom, (
            f"a skipped test was never evaluated and must not dilute the rate; "
            f"denominator was: {denom}"
        )


class TestAgreesWithTheHeadlineFormula:
    """Arithmetic parity with ``_evaluated`` — the two must not drift again."""

    @pytest.mark.parametrize(
        "passed,failed,broken,expected",
        [
            (47, 9, 2, 81.0),    # the live homelab window: was 83.9
            (10, 0, 2, 83.3),    # the original #464 repro
            (12, 0, 0, 100.0),
            (0, 0, 5, 0.0),      # an all-broken day is 0%, not a flat 100%
        ],
    )
    def test_expected_daily_rate(self, passed, failed, broken, expected):
        denom = _evaluated(passed, failed, broken)
        rate = round(passed / denom * 100.0, 1) if denom else 0.0
        assert rate == expected

    def test_a_day_whose_only_failures_are_broken_is_not_100_percent(self):
        """Previously charted as a flat 100% — the trend line's version of the
        bug #464 fixed on the headline."""
        denom = _evaluated(10, 0, 2)
        assert round(10 / denom * 100.0, 1) < 100.0


def test_no_pass_rate_denominator_in_this_module_omits_broken():
    """Module-wide guard.

    #464's guard covered only ``_period_stats``, so this exact sibling bug
    survived it. Scan the whole module for any two-term pass-rate denominator.
    """
    src = inspect.getsource(metrics_service)
    collapsed = re.sub(r"\s+", " ", src)
    offenders = [
        pat for pat in (
            "sum_passed + sum_failed",
            "NULLIF( SUM(tr.passed_tests) + SUM(tr.failed_tests), 0)",
            "passed_tests) + SUM(tr.failed_tests), 0)",
        )
        if pat in collapsed
    ]
    assert not offenders, (
        f"metrics_service still computes a pass rate over passed+failed only: "
        f"{offenders}. Every denominator must be evaluated = passed+failed+broken."
    )
