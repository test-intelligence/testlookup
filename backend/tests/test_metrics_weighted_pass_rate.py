"""Unit tests for ``metrics_service._period_stats`` weighted pass-rate (2026-05-14).

The user reported that /overview showed 26% pass-rate while /live showed 94%
for the same project. Root cause: ``_period_stats`` did ``AVG(TestRun.pass_rate)``
which averages run-level percentages equally regardless of run size, while
/live uses ``totalPassed / (totalPassed + totalFailed)`` (weighted by test
count). One tiny failing run could drag the dashboard headline far below
the user's lived experience.

The fix switches the no-suite branch to a WEIGHTED ratio rather than an
unweighted average of run-level percentages. These tests pin that contract.

UPDATED 2026-08-07: the denominator is now ``Σ(passed + failed + broken)`` via
``_evaluated()``. Omitting BROKEN let a run of 10 passed / 0 failed / 2 broken
report a **100% pass rate** (see
``tests/regression/test_pass_rate_counts_broken.py``). The *weighting* contract
these tests exist to protect is unchanged -- every scenario below has zero
broken tests, so every expected value is identical; the mocks simply had to
grow the new ``sum_broken`` column.

The suite-filtered branch already uses test-level COUNT and was unaffected
by the bug, but we test it here too so a future change can't regress.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _one_result(value):
    """Match ``result.one()`` shape — returns the row object directly."""
    res = MagicMock()
    res.one = MagicMock(return_value=value)
    return res


# ── No-suite branch (the bugfix path) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_period_stats_weighted_avg_no_skips_inflate():
    """Two runs in the period: one with 1 passed, 9 failed (10% pass rate);
    one with 90 passed, 10 failed (90% pass rate). Weighted = 91/110 = 82.7%.
    Simple AVG would say 50% — the bug we're fixing."""
    from app.services.metrics_service import _period_stats

    row = SimpleNamespace(
        total_runs=2,
        sum_passed=91,    # 1 + 90
        sum_broken=0, sum_failed=19,    # 9 + 10
        avg_duration_ms=1000,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_one_result(row))

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    # 91 / (91 + 19) = 91 / 110 ≈ 82.727...%
    assert result["pass_rate"] == pytest.approx(82.7272727, rel=1e-4)
    assert result["total_runs"] == 2


@pytest.mark.asyncio
async def test_period_stats_skipped_not_counted_in_denominator():
    """Skipped tests must not depress the pass rate — only passed+failed
    enters the denominator. /live uses the same formula."""
    from app.services.metrics_service import _period_stats

    # 90 passed, 10 failed → 90% even if there were 1000 skipped on top.
    row = SimpleNamespace(
        total_runs=1, sum_passed=90, sum_broken=0, sum_failed=10, avg_duration_ms=500,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_one_result(row))

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert result["pass_rate"] == pytest.approx(90.0, rel=1e-4)


@pytest.mark.asyncio
async def test_period_stats_zero_runs_returns_zero_rate():
    """No runs in the window → pass_rate must be 0.0 (not div-by-zero)."""
    from app.services.metrics_service import _period_stats

    row = SimpleNamespace(
        total_runs=0, sum_passed=0, sum_broken=0, sum_failed=0, avg_duration_ms=0,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_one_result(row))

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert result["pass_rate"] == 0.0
    assert result["total_runs"] == 0


@pytest.mark.asyncio
async def test_period_stats_all_passed_returns_100():
    """Sanity: an all-passing window returns exactly 100.0."""
    from app.services.metrics_service import _period_stats

    row = SimpleNamespace(
        total_runs=5, sum_passed=500, sum_broken=0, sum_failed=0, avg_duration_ms=200,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_one_result(row))

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert result["pass_rate"] == 100.0


@pytest.mark.asyncio
async def test_period_stats_handles_null_sum_columns():
    """``SUM(...)`` returns None on an empty set in some DB configurations;
    the function wraps it with ``coalesce(..., 0)`` so this shouldn't
    happen at SQL level, but the Python code defensively handles None too."""
    from app.services.metrics_service import _period_stats

    row = SimpleNamespace(
        total_runs=None, sum_passed=None, sum_broken=0, sum_failed=None, avg_duration_ms=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_one_result(row))

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert result["pass_rate"] == 0.0
    assert result["total_runs"] == 0
    assert result["avg_duration_ms"] == 0


# ── Suite-filtered branch ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_period_stats_suite_filtered_counts_per_test_rows():
    """F-080: suite numbers come from per-test rows bucketed by EFFECTIVE
    suite, not from whole-run aggregate columns.

    The old contract summed ``TestRun.passed_tests``/``total_tests`` for every
    run that *touched* the suite. Those columns are whole-run totals and cannot
    be suite-scoped, so a run containing three suites reported all three suites'
    tests under each of them — measured 60/60/60 where the truth was 25/20/15,
    with an identical pass rate for every suite. The selector looked like it
    worked and answered a different question.

    Three queries now: per-test counts, the no-rows-yet fallback, and the
    run-level count/duration.
    """
    from app.services.metrics_service import _period_stats

    cases = SimpleNamespace(total=25, passed=20, failed=5, broken=0)
    pending = SimpleNamespace(passed=0, failed=0, broken=0, total=0)
    runs = SimpleNamespace(total_runs=3, avg_duration_ms=750)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _one_result(cases), _one_result(pending), _one_result(runs),
    ])

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
        suite_name="smoke",
    )
    # 20 / (20 + 5) = 80.0%, over the SUITE's 25 executions — not the run's.
    assert result["pass_rate"] == pytest.approx(80.0, rel=1e-4)
    assert result["total_executions"] == 25
    assert result["total_runs"] == 3


@pytest.mark.asyncio
async def test_period_stats_suite_filter_still_counts_runs_without_per_test_rows():
    """The property the OLD implementation existed to protect, kept.

    Live-stream runs persist run aggregates before their per-test rows. An
    implementation that only counted ``test_cases`` returned 0 for a populated
    suite and blanked the dashboard — which is why the 2026-05-15 change moved
    to run aggregates in the first place. The fix must not reintroduce that: a
    run with NO per-test rows still contributes its run-level totals.
    """
    from app.services.metrics_service import _period_stats

    cases = SimpleNamespace(total=0, passed=0, failed=0, broken=0)
    pending = SimpleNamespace(passed=40, failed=10, broken=0, total=50)
    runs = SimpleNamespace(total_runs=1, avg_duration_ms=1200)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _one_result(cases), _one_result(pending), _one_result(runs),
    ])

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
        suite_name="smoke",
    )
    assert result["total_executions"] == 50, "mid-ingest live-stream run vanished"
    assert result["pass_rate"] == pytest.approx(80.0, rel=1e-4)
