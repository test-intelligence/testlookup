"""The suite catalogue (``GET /test-management/suites``) keeps its populations apart.

D-60 / S-E-6 (product findings 2026-11-21): the catalogue's automation query
filed EVERY test of a multi-suite run under the run's dominant
``primary_suite_name`` (a "Path A / Path B" union), so the dominant suite's
``test_count``, ``passed_count``, ``failed_count`` and pass rate described the
whole run. It now groups by the shared effective suite
(``analytics_scope.effective_suite_sql``), the expression /coverage uses. The
row-level behaviour runs on real Postgres in
``tests/integration/test_test_management_suites_catalogue_postgres.py``; this
file pins the statement shape on a scripted session.

D-59: an authored (managed) case is counted as ``authored_count`` only. It
never enters ``test_count`` (the pass-rate denominator), and its creation time
is never a "last run".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

LAST_RUN = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _NoopSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def no_side_lookups(monkeypatch):
    import app.services.suite_history_service as history
    import app.services.suite_review_service as reviews

    async def _no_owners(_db, _project_id, _names):
        return {}

    async def _no_history(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(reviews, "list_suite_owners", _no_owners)
    monkeypatch.setattr(history, "compute_suite_history", _no_history)


def _session(auto_rows, manual_rows):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _Result(auto_rows),
            _Result([]),          # run-aggregate fallback
            _Result(manual_rows),
            _Result([]),          # first-class suites (TCL-07)
        ]),
        rollback=AsyncMock(),
        begin_nested=_NoopSavepoint,
    )


def _checkout():
    return SimpleNamespace(
        suite_name="checkout", test_count=10, passed_count=9, failed_count=1,
        last_run_at=LAST_RUN, last_run_id=uuid.uuid4(),
    )


async def _catalogue(db):
    from app.routers.test_management_exports import list_test_suites

    rows = await list_test_suites(
        project_id=uuid.uuid4(), db=db,
        current_user=SimpleNamespace(id=uuid.uuid4(), role="ADMIN"),
    )
    return {row["suite_name"]: row for row in rows}


def _sql(db, index: int) -> str:
    return " ".join(str(db.execute.await_args_list[index].args[0]).split())


@pytest.mark.asyncio
async def test_each_test_is_filed_under_its_own_effective_suite(no_side_lookups):
    """D-60 / S-E-6: the automation statement groups by the shared effective
    suite, with no second path that files a run's tests under its dominant
    suite, and its last run comes from the same rows."""
    from app.services.analytics_scope import effective_suite_sql

    db = _session([_checkout()], [])
    await _catalogue(db)
    auto_sql = _sql(db, 0)
    assert " ".join(effective_suite_sql().split()) in auto_sql
    assert "UNION ALL" not in auto_sql, "a second path files tests under the run's dominant suite"
    assert "OR EXISTS" not in auto_sql, "the last run must come from the suite's own tests"
    assert "array_agg(run_id ORDER BY run_created_at DESC" in auto_sql


@pytest.mark.asyncio
async def test_one_authored_case_leaves_the_run_statistics_unchanged(no_side_lookups):
    """D-59: adding one managed case leaves the pass rate, the test count and
    the last run as they were and sets ``authored_count`` to 1."""
    before = (await _catalogue(_session([_checkout()], [])))["checkout"]
    db = _session([_checkout()], [SimpleNamespace(suite_name="checkout", authored_count=1)])
    after = (await _catalogue(db))["checkout"]

    assert before["authored_count"] == 0
    assert after["authored_count"] == 1
    assert (after["test_count"], after["passed_count"], after["failed_count"]) == (10, 9, 1)
    assert after["pass_rate"] == before["pass_rate"] == 90.0
    assert after["last_run_at"] == before["last_run_at"] == LAST_RUN.isoformat()
    manual_sql = _sql(db, 2)
    assert "FROM managed_test_cases" in manual_sql
    assert "last_run_at" not in manual_sql, "an authored case's created_at is not a run"


@pytest.mark.asyncio
async def test_a_suite_of_authored_cases_only_has_not_run(no_side_lookups):
    """A manual-only suite: no tests ran, so no pass rate and no last run."""
    rows = await _catalogue(_session([], [SimpleNamespace(suite_name="payments", authored_count=3)]))
    payments = rows["payments"]
    assert payments["authored_count"] == 3
    assert payments["test_count"] == 0
    assert payments["pass_rate"] is None
    assert payments["last_run_at"] is None and payments["last_run_id"] is None
