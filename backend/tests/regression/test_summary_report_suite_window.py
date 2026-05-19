"""Regression: /reports/summary missed suites whose per-test rows
hadn't landed in test_cases.

Bug pinned: live-stream Redis buffer eviction meant a run's
aggregates landed in ``test_runs.primary_suite_name`` but its
per-test rows never materialised in ``test_cases``. The original
summary-report SQL only read from ``test_cases`` and dropped these
suites entirely from the breakdown.

Fix: ``summary_report_service._per_suite_breakdown_window`` UNION-ALLs
``cases_agg`` (per-test ground truth where it exists) with
``runs_agg`` (run-level fallback for runs with zero ``test_cases``).
Result re-aggregated so a suite spanning both sources sums once.

What this file pins:

  * The compiled SQL contains BOTH the test_cases CTE and the
    test_runs.primary_suite_name fallback CTE — neither half can be
    removed without re-introducing the bug.
  * The fallback only fires for runs with NO ``test_cases`` rows
    (the ``NOT EXISTS`` predicate is load-bearing — without it the
    fallback would double-count suites that have partial per-test
    data).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_suite_window_query_unions_test_cases_and_test_runs():
    """The query must read from both sources — pure ``test_cases``
    misses live-stream runs that lost their buffer; pure
    ``test_runs`` overcounts suites that have per-test data."""
    from app.services.summary_report_service import _per_suite_breakdown_window

    captured: list = []

    async def capture(stmt, params=None):
        captured.append((stmt, params))
        result = MagicMock()
        result.all = lambda: []
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)

    now = datetime.now(timezone.utc)
    await _per_suite_breakdown_window(
        db, uuid.uuid4(), start=now, end=now,
    )

    assert captured, "function should fire exactly one SQL statement"
    sql = str(captured[0][0])

    # Both CTEs must be present.
    assert "cases_agg" in sql, "missing test_cases aggregate CTE"
    assert "runs_agg" in sql, "missing test_runs primary_suite_name fallback CTE"

    # The fallback must filter to runs with no test_cases rows.
    assert "NOT EXISTS" in sql, (
        "Fallback must use NOT EXISTS so suites with partial per-test "
        "data don't get double-counted."
    )

    # UNION ALL of the two CTEs.
    assert "UNION ALL" in sql, "Two sources must be unioned (ALL, not deduped)"


@pytest.mark.asyncio
async def test_suite_window_filters_null_or_empty_suite_names():
    """Empty/whitespace ``primary_suite_name`` produces a meaningless
    "—" bucket. The SQL must filter these out."""
    from app.services.summary_report_service import _per_suite_breakdown_window

    captured: list = []

    async def capture(stmt, params=None):
        captured.append((stmt, params))
        result = MagicMock()
        result.all = lambda: []
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=capture)

    now = datetime.now(timezone.utc)
    await _per_suite_breakdown_window(
        db, uuid.uuid4(), start=now, end=now,
    )

    sql = str(captured[0][0])
    assert "primary_suite_name IS NOT NULL" in sql
    assert "TRIM(tr.primary_suite_name) <> ''" in sql
    assert "tc.suite_name IS NOT NULL" in sql
