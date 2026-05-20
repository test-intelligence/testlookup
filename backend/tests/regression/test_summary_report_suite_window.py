"""Regression: /reports/summary missed suites whose per-test rows
hadn't landed in test_cases.

Bug pinned: live-stream Redis buffer eviction meant a run's
aggregates landed in ``test_runs.primary_suite_name`` but its
per-test rows never materialised in ``test_cases``. The original
summary-report SQL only read from ``test_cases`` and dropped these
suites entirely from the breakdown.

Fix: ``summary_report_service._per_suite_breakdown_window`` UNION-ALLs
``cases_agg`` (DISTINCT-fingerprint unique-test counts per effective
suite — the SAME definition /coverage uses) with ``runs_agg`` (run-level
fallback for runs with zero ``test_cases``).

Follow-up fix (2026-05-20): the fallback is now guarded by
``WHERE suite_name NOT IN (SELECT suite_name FROM cases_agg)`` so a suite
that has BOTH per-test rows AND live-stream-gap runs is counted ONCE
(by its distinct fingerprints), not summed twice. Before the guard,
RealisticTestNGSuite read 7570 (2423 unique + ~5147 missing-run
executions) instead of the 2423 unique tests /coverage reports.

What this file pins:

  * The compiled SQL contains BOTH the test_cases CTE and the
    test_runs.primary_suite_name fallback CTE — neither half can be
    removed without re-introducing the original "missing suites" bug.
  * The run-level fallback only fires for runs with NO ``test_cases``
    rows (``NOT EXISTS``) AND only for suites not already covered by the
    distinct-fingerprint pass (``NOT IN (SELECT suite_name FROM
    cases_agg)``) — both predicates are load-bearing against
    double-counting.
  * Suites are bucketed by the *effective* suite (run-level
    ``primary_suite_name`` for live_stream, per-event ``tc.suite_name``
    otherwise) so /reports/summary and /coverage agree.
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
        "Fallback must use NOT EXISTS so a run with per-test rows is never "
        "also counted via its run-level aggregate."
    )

    # …AND the fallback must skip suites already counted by the
    # distinct-fingerprint pass, or a suite with both per-test rows and
    # live-stream-gap runs gets double-counted (the 7570-vs-2423 bug).
    assert "NOT IN (SELECT suite_name FROM cases_agg)" in sql, (
        "Run-level fallback must exclude suites already in cases_agg so "
        "unique-test counts match /coverage instead of inflating."
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
    # Run-level fallback drops null/empty primary_suite_name.
    assert "primary_suite_name IS NOT NULL" in sql
    assert "TRIM(tr.primary_suite_name) <> ''" in sql
    # The test_cases pass drops rows with no resolvable effective suite
    # (replaced the old strict ``tc.suite_name IS NOT NULL`` check when the
    # query moved to effective-suite bucketing). The COALESCE picks
    # primary_suite_name for live_stream, else tc.suite_name.
    assert "effective_suite IS NOT NULL" in sql
    assert "tc.test_fingerprint IS NOT NULL" in sql
