"""Regression test for GET /api/v1/test-management/suites — test_count
must reflect unique test cases per suite, not per-execution rows.

User-reported bug: a suite with 10 logical tests run across 3 test runs
surfaced as ``test_count=30`` because the SQL counted rows from
``test_cases`` (one row per execution). The catalog-view label on the
/test-management Test Suites tab implies unique-test counts, so the
COUNT(*) was wrong.

Fix: the handler now uses ``DISTINCT ON (test_fingerprint, suite_name)``
ordered by run-creation-DESC so each logical test contributes exactly
one row (its most recent execution). ``passed_count`` /
``failed_count`` then read as "of the N unique tests in this suite,
how many last ran green/red" — the snapshot the page wants.

These tests pin:
  1. The SQL text uses DISTINCT ON over test_fingerprint (forces any
     future refactor to keep the dedup at the SQL layer instead of
     post-aggregating in Python).
  2. The handler exposes the merged row shape the frontend expects.
"""
from __future__ import annotations

import inspect
import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


# ── 1. Source-level regression guard ─────────────────────────────────────────


def test_suites_sql_uses_distinct_on_test_fingerprint():
    """If a future refactor reverts to ``COUNT(*) FROM test_cases``, the
    user-reported 3x-multiplier bug returns. Pin the SQL pattern so a
    regression fails CI loudly with a clear pointer rather than only
    showing up when an operator opens the page."""
    from app.routers import test_management_exports as mod

    src = inspect.getsource(mod.list_test_suites)
    assert re.search(
        r"DISTINCT\s+ON\s*\(\s*tc\.test_fingerprint",
        src,
        re.IGNORECASE,
    ), (
        "list_test_suites must dedupe by test_fingerprint before counting. "
        "The previous SQL did COUNT(*) over the test_cases × test_runs join, "
        "which multiplied the unique-test count by the number of runs."
    )
    # Defence in depth: explicitly forbid the broken pattern of grouping
    # by ``tc.suite_name`` straight off ``test_cases`` without the CTE.
    # The fixed form uses ``GROUP BY suite_name`` on the CTE output.
    assert "FROM latest_per_test" in src or "FROM (\n" in src, (
        "Expected a CTE/subquery wrapping the per-test dedup before the "
        "outer aggregation."
    )


# ── 2. Handler shape — mocked DB ─────────────────────────────────────────────


class _Result:
    """Mimics SQLAlchemy execute result for both .fetchall() rows."""
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _row(**kw):
    """Build a row that quacks like a SQLAlchemy named-tuple row."""
    return SimpleNamespace(**kw)


class _NoopSavepoint:
    """Minimal async-context-manager that quacks like ``db.begin_nested()``.

    The handler wraps the two fallback queries in ``async with
    db.begin_nested():`` to isolate SQL failures into a SAVEPOINT so they
    don't poison the outer transaction. The tests don't exercise real
    SQL, so the savepoint is a no-op here — just enter+exit cleanly.
    """
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Returning False lets exceptions propagate; the handler's
        # except clause then swallows them as the fallback rows-empty.
        return False


def _make_begin_nested():
    """Factory so each ``db = SimpleNamespace(...)`` block can attach
    its own callable returning a fresh ``_NoopSavepoint``."""
    return lambda: _NoopSavepoint()


@pytest.mark.asyncio
async def test_list_test_suites_returns_unique_counts_from_cte(monkeypatch):
    """Handler-level: feed the mocked DB the shape the new CTE returns
    (one row per suite_name, test_count == unique tests) and verify the
    response carries those numbers through without re-aggregating.

    The handler also calls ``list_suite_owners`` after the merge to
    bulk-resolve owner metadata — patched here to an empty dict so the
    test stays focused on the count math.
    """
    from app.routers.test_management_exports import list_test_suites

    project_id = uuid.uuid4()

    # Two suites: auth-api has 10 unique tests (9 last-passed, 1 last-
    # failed); orders-ui has 5 unique tests, all green. The fix ensures
    # the handler returns these as-is — historically a bad path would
    # multiply by run count again at the merge step.
    auto_rows = [
        _row(
            suite_name="auth-api",
            test_count=10, passed_count=9, failed_count=1,
            last_run_at=None, last_run_id=uuid.uuid4(),
        ),
        _row(
            suite_name="orders-ui",
            test_count=5, passed_count=5, failed_count=0,
            last_run_at=None, last_run_id=uuid.uuid4(),
        ),
    ]
    # No manual managed_test_cases rows for these suites — the merge
    # leg is exercised by other tests.
    manual_rows: list = []

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _Result(auto_rows),
            _Result([]),       # run-aggregate fallback — no live-stream gap rows
            _Result(manual_rows),
        ]),
        rollback=AsyncMock(),
        begin_nested=_make_begin_nested(),
    )

    # Patch the suite-owner bulk lookup so the handler doesn't hit
    # its third DB query (a separate code path covered elsewhere).
    async def _no_owners(_db, _project_id, _names):
        return {}
    import app.services.suite_review_service as _sros
    monkeypatch.setattr(_sros, "list_suite_owners", _no_owners)

    result = await list_test_suites(
        project_id=project_id,
        db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    by_name = {s["suite_name"]: s for s in result}
    assert by_name["auth-api"]["test_count"] == 10
    assert by_name["auth-api"]["passed_count"] == 9
    assert by_name["auth-api"]["failed_count"] == 1
    # pass_rate = 9/10 * 100 = 90.0 — the user-visible signal that
    # 1-of-10 tests last ran red, not 1-of-30.
    assert by_name["auth-api"]["pass_rate"] == 90.0
    assert by_name["orders-ui"]["test_count"] == 5
    assert by_name["orders-ui"]["failed_count"] == 0


@pytest.mark.asyncio
async def test_list_test_suites_merges_managed_cases_additively(monkeypatch):
    """A managed_test_cases row for the same suite_name adds to the
    automation count — those are authored tests catalogued by hand,
    distinct from automation-derived rows."""
    from app.routers.test_management_exports import list_test_suites

    project_id = uuid.uuid4()
    auto_rows = [_row(
        suite_name="auth-api",
        test_count=10, passed_count=9, failed_count=1,
        last_run_at=None, last_run_id=uuid.uuid4(),
    )]
    manual_rows = [_row(
        suite_name="auth-api",
        test_count=3, passed_count=0, failed_count=0,
        last_run_at=None,
    )]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _Result(auto_rows),
            _Result([]),       # run-aggregate fallback — no live-stream gap rows
            _Result(manual_rows),
        ]),
        rollback=AsyncMock(),
        begin_nested=_make_begin_nested(),
    )

    async def _no_owners(_db, _project_id, _names):
        return {}
    import app.services.suite_review_service as _sros
    monkeypatch.setattr(_sros, "list_suite_owners", _no_owners)

    result = await list_test_suites(
        project_id=project_id,
        db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    by_name = {s["suite_name"]: s for s in result}
    # 10 automation-derived + 3 manually authored = 13.
    assert by_name["auth-api"]["test_count"] == 13


@pytest.mark.asyncio
async def test_list_test_suites_surfaces_live_stream_gap_suites(monkeypatch):
    """Regression for user-reported "test suite not on /test-management".

    A suite (e.g. "Realistic TestNG client examples") whose runs landed
    ``test_runs.primary_suite_name`` aggregates but never persisted
    per-test ``test_cases`` rows (the live-stream Redis-buffer gap)
    used to be invisible on the Test Suites tab. The handler now unions
    a run-aggregate query as a fallback. Pin that the fallback row's
    counts come through with the run-level totals.
    """
    from app.routers.test_management_exports import list_test_suites

    project_id = uuid.uuid4()
    # auto_rows is empty — per-test data never landed for this suite.
    auto_rows: list = []
    # run-aggregate fallback row sourced from ``test_runs``.
    run_aggregate_rows = [_row(
        suite_name="Realistic TestNG client examples",
        test_count=12, passed_count=10, failed_count=2,
        last_run_at=None, last_run_id=uuid.uuid4(),
    )]
    manual_rows: list = []
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _Result(auto_rows),
            _Result(run_aggregate_rows),
            _Result(manual_rows),
        ]),
        rollback=AsyncMock(),
        begin_nested=_make_begin_nested(),
    )

    async def _no_owners(_db, _project_id, _names):
        return {}
    import app.services.suite_review_service as _sros
    monkeypatch.setattr(_sros, "list_suite_owners", _no_owners)

    result = await list_test_suites(
        project_id=project_id,
        db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    by_name = {s["suite_name"]: s for s in result}
    assert "Realistic TestNG client examples" in by_name, (
        "live-stream-gap suite must be surfaced via the run-aggregate fallback"
    )
    suite = by_name["Realistic TestNG client examples"]
    assert suite["test_count"] == 12
    assert suite["passed_count"] == 10
    assert suite["failed_count"] == 2
    # pass_rate = 10/12 * 100 = 83.3
    assert suite["pass_rate"] == 83.3


@pytest.mark.asyncio
async def test_list_test_suites_run_aggregate_does_not_double_count_existing(monkeypatch):
    """If a suite has both per-test rows AND a run-aggregate fallback
    row (rare but possible during a partial backfill), the authoritative
    per-test counts win. The fallback row is dropped to avoid doubling
    the test_count."""
    from app.routers.test_management_exports import list_test_suites

    project_id = uuid.uuid4()
    auto_rows = [_row(
        suite_name="auth-api", test_count=10, passed_count=9, failed_count=1,
        last_run_at=None, last_run_id=uuid.uuid4(),
    )]
    # A fallback row would double-count if blindly summed.
    run_aggregate_rows = [_row(
        suite_name="auth-api", test_count=10, passed_count=9, failed_count=1,
        last_run_at=None, last_run_id=uuid.uuid4(),
    )]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _Result(auto_rows),
            _Result(run_aggregate_rows),
            _Result([]),
        ]),
        rollback=AsyncMock(),
        begin_nested=_make_begin_nested(),
    )

    async def _no_owners(_db, _project_id, _names):
        return {}
    import app.services.suite_review_service as _sros
    monkeypatch.setattr(_sros, "list_suite_owners", _no_owners)

    result = await list_test_suites(
        project_id=project_id,
        db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    by_name = {s["suite_name"]: s for s in result}
    # Per-test rows win; the fallback row is dropped, not summed.
    assert by_name["auth-api"]["test_count"] == 10
