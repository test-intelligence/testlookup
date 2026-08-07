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
async def test_period_stats_suite_filtered_reads_aggregates_from_test_runs():
    """Suite branch reads ``sum_passed / (sum_passed + sum_failed)`` from
    ``test_runs`` columns. Prior to 2026-05-15 it INNER-JOINed
    ``test_cases`` and read counts from there, which silently returned
    zero whenever live-stream runs had aggregates on the run row but no
    per-case rows persisted yet. Pin the new contract."""
    from app.services.metrics_service import _period_stats

    row = SimpleNamespace(
        total_runs=3,
        sum_passed=120,
        sum_broken=0, sum_failed=30,
        sum_total=150,
        avg_duration_ms=750,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_one_result(row))

    result = await _period_stats(
        db, project_id=None,
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
        suite_name="smoke",
    )
    # 120 / (120 + 30) = 80.0% — weighted across test_runs aggregates,
    # NOT joined to test_cases (which may be empty for live-stream runs).
    assert result["pass_rate"] == pytest.approx(80.0, rel=1e-4)
    assert result["total_runs"] == 3
