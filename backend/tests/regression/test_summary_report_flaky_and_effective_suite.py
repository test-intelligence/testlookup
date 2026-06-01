"""Regression: summary report flaky-rate could exceed 100%, and the
top-failing-tests query ignored the effective-suite rule.

Bugs pinned (review/summary-report-service, 2026-06-01):

1. ``flaky_rate_pct`` = all-time ``flaky_count`` / windowed ``totals.total``.
   ``_count_flaky_tests`` has no time window, so for a short window (fewer
   unique tests in-window than the all-time flaky set) the ratio could read
   >100% on the report. Fix: clamp to <=100.

2. ``_top_failing_tests`` grouped/labelled by raw ``tc.suite_name``, unlike the
   per-suite breakdowns which coalesce ``tr.primary_suite_name`` for
   live_stream runs (feedback_effective_suite_query_pattern). Fix: group by the
   same effective-suite expression so a live_stream test's failures don't split
   across class-name vs session-label suites.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services import summary_report_service as svc  # noqa: E402


def _scalar(value):
    res = MagicMock()
    res.scalar = MagicMock(return_value=value)
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _one(value):
    res = MagicMock()
    res.one = MagicMock(return_value=value)
    return res


def _all(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


def _totals_row():
    return SimpleNamespace(
        runs=3, total=0, passed=0, failed=0, skipped=0, broken=0,
        avg_duration_ms=1000, latest=None,
        agg_total=0, agg_passed=0, agg_failed=0, agg_skipped=0, agg_broken=0,
    )


@pytest.mark.asyncio
async def test_flaky_rate_clamped_to_100():
    """All-time flaky_count (10) over a windowed unique total (2) must clamp
    to 100% instead of reporting 500%."""
    project_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar("My Project"),                                   # _resolve_project_name
        _one(_totals_row()),                                     # _window_totals run_stmt
        _one(SimpleNamespace(total=2, passed=1, failed=1,        # uniq_stmt → unique total=2
                             skipped=0, broken=0)),
        _all([]),                                                # _per_suite_breakdown_window
        _scalar(10),                                             # _count_flaky_tests → 10 (all-time)
        _all([]),                                                # _top_failing_tests
    ])

    result = await svc.build_summary_report(db, project_id, days=1, mode="window")

    assert result["flaky_test_count"] == 10           # raw count preserved
    assert result["flaky_rate_pct"] == 100.0          # clamped, not 500.0
    assert result["totals"]["total_test_cases"] == 2


@pytest.mark.asyncio
async def test_top_failing_query_uses_effective_suite():
    """_top_failing_tests must group by the effective-suite expression
    (coalesce live_stream primary_suite_name over per-event suite_name)."""
    captured = {}

    class _FakeDB:
        async def execute(self, stmt):
            captured["sql"] = str(stmt)
            return _all([])

    now = datetime.now(timezone.utc)
    await svc._top_failing_tests(
        _FakeDB(), uuid.uuid4(), now - timedelta(days=7), now, limit=10,
    )

    sql = captured["sql"].lower()
    assert "primary_suite_name" in sql, (
        "top-failing query must consider tr.primary_suite_name (effective-suite)"
    )
    assert "case" in sql and "coalesce" in sql, (
        "expected the live_stream CASE/COALESCE effective-suite expression"
    )
