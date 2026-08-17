"""Regression: the dashboard flaky-count heuristic must window recent runs and
treat BROKEN as a failure.

Bug (2026-07, B3): ``metrics_service._count_flaky_tests`` documented "last 10
runs" but its SQL had NO per-fingerprint window — it aggregated over all
``test_case_history`` with ``HAVING COUNT(*) >= 5`` — so a test that was flaky
months ago but stable since could never lose the flaky flag (the count only
grew). It also filtered ``status = 'FAILED'`` only, silently excluding BROKEN
(the canonical failed set is ``{FAILED, BROKEN}`` — see
``flaky_signals._FAILED_STATUSES``).

The repo has no live-Postgres test harness (integration conftest mocks the
session), so — like ``test_top_failing_query_uses_effective_suite`` — this pins
the SQL *shape* that makes the fix load-bearing. The full query was additionally
validated end-to-end against a real Postgres during development (window drops a
recovered test; BROKEN-only flakiness is counted).
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import metrics_service as svc  # noqa: E402


class _CaptureDB:
    """Captures the compiled SQL text of the single execute() call."""

    def __init__(self):
        self.sql = ""

    async def execute(self, query, params=None):
        self.sql = str(query)
        self.params = params

        class _R:
            def scalar(self_inner):
                return 0

        return _R()


@pytest.mark.asyncio
async def test_flaky_count_windows_recent_runs_per_fingerprint():
    db = _CaptureDB()
    await svc._count_flaky_tests(db, project_id=str(uuid.uuid4()))
    sql = db.sql.lower()

    # Per-fingerprint recency window via ROW_NUMBER — the core of the fix.
    assert "row_number() over" in sql
    assert "partition by tch.test_fingerprint" in sql
    # Persistence time is now a tie-breaker after natural build order; it must
    # remain present for duplicate/non-monotonic build identifiers.
    assert "tch.created_at desc" in sql
    # Only the most-recent N executions feed the ratio.
    assert f"rn <= {svc._FLAKY_WINDOW_RUNS}" in sql
    # Minimum sample size still enforced, now over the bounded window.
    assert f"count(*) >= {svc._FLAKY_MIN_RUNS}" in sql


@pytest.mark.asyncio
async def test_flaky_count_orders_history_by_natural_build_number():
    """The flip detector must follow CI run order, not async commit order."""
    db = _CaptureDB()
    await svc._count_flaky_tests(db, project_id=str(uuid.uuid4()))
    sql = " ".join(db.sql.lower().split())

    natural = (
        "case when tr.build_number ~ '[0-9]' then string_to_array( "
        "trim(regexp_replace(tr.build_number, '[^0-9]+', ' ', 'g')), ' ' "
        ")::bigint[] else null end"
    )
    assert natural in sql
    assert (
        f"order by {natural} desc nulls first, tr.build_number desc, "
        "tch.created_at desc, tch.id desc"
    ) in sql


@pytest.mark.asyncio
async def test_flaky_count_treats_broken_as_failure():
    db = _CaptureDB()
    await svc._count_flaky_tests(db, project_id=str(uuid.uuid4()))
    sql = db.sql.upper()

    # The failed-status set must include BOTH FAILED and BROKEN.
    assert "'FAILED', 'BROKEN'" in sql or "'FAILED','BROKEN'" in sql


@pytest.mark.asyncio
async def test_flaky_count_still_scopes_by_project_and_suite():
    """The window fix must not drop the existing project/suite scoping."""
    db = _CaptureDB()
    await svc._count_flaky_tests(db, project_id=str(uuid.uuid4()), suite_name="checkout")
    sql = db.sql.lower()
    assert "tr.project_id = :project_id" in sql
    assert "primary_suite_name" in sql  # effective-suite matching preserved
