"""Unit tests for the test-suites listing path used by /api/v1/suites.

Covers ``test_suite_service.list_test_suites`` in three shapes:

* All accessible projects (project_ids=None) → no WHERE clause.
* Empty project list → empty result without hitting the DB.
* Project-scoped result with per-suite canonical-case counts attached.

The endpoint thin layer adds project-access checks; those live in
test_authorization_guards.py. The data shape (test_case_count, ordering,
is_default first) is what powers the Suites page UI, so it's the part
worth pinning here.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


class _ScalarsAll:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _SuitesExecResult:
    """Mimics .scalars().all() for the TestSuite query."""

    def __init__(self, suites):
        self._suites = suites

    def scalars(self):
        return _ScalarsAll(self._suites)


class _CountsExecResult:
    """Mimics .all() for the (suite_id, count) group-by query."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _MissingSuitesResult:
    """Mimics .fetchall() for the test_runs.primary_suite_name backfill
    probe. An empty list signals "no live-stream gaps detected" so the
    handler skips the auto-backfill branch."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _suite(name: str, *, is_default: bool = False, project_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        name=name,
        description=None,
        is_default=is_default,
        tags=None,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_list_test_suites_empty_project_list_returns_empty():
    from app.services.test_suite_service import list_test_suites

    db = SimpleNamespace(execute=AsyncMock())
    result = await list_test_suites(db, project_ids=[])

    assert result == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_test_suites_returns_items_with_counts():
    from app.services.test_suite_service import list_test_suites

    project_id = uuid.uuid4()
    default_suite = _suite("All Tests", is_default=True, project_id=project_id)
    api_suite = _suite("auth-api", project_id=project_id)

    counts_rows = [
        (default_suite.id, 4),
        (api_suite.id, 10),
    ]

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _SuitesExecResult([default_suite, api_suite]),
            _MissingSuitesResult([]),  # no live-stream gap to backfill
            _CountsExecResult(counts_rows),
        ]),
        rollback=AsyncMock(),
    )

    result = await list_test_suites(db, project_ids=[project_id])

    assert len(result) == 2
    by_name = {item["name"]: item for item in result}
    assert by_name["All Tests"]["test_case_count"] == 4
    assert by_name["All Tests"]["is_default"] is True
    assert by_name["auth-api"]["test_case_count"] == 10
    assert by_name["auth-api"]["is_default"] is False


@pytest.mark.asyncio
async def test_list_test_suites_skips_count_query_when_disabled():
    from app.services.test_suite_service import list_test_suites

    project_id = uuid.uuid4()
    suite = _suite("alpha", project_id=project_id)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _SuitesExecResult([suite]),
            _MissingSuitesResult([]),  # no live-stream gap to backfill
        ]),
        rollback=AsyncMock(),
    )

    result = await list_test_suites(
        db, project_ids=[project_id], include_counts=False,
    )

    assert len(result) == 1
    assert result[0]["test_case_count"] == 0
    # Suites query + backfill probe; counts query is skipped by the flag.
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_list_test_suites_all_projects_when_project_ids_is_none():
    from app.services.test_suite_service import list_test_suites

    suite = _suite("cross-project")
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _SuitesExecResult([suite]),
            _CountsExecResult([(suite.id, 0)]),
        ])
    )

    result = await list_test_suites(db, project_ids=None)

    assert len(result) == 1
    assert result[0]["name"] == "cross-project"
    assert result[0]["test_case_count"] == 0


@pytest.mark.asyncio
async def test_list_test_suites_backfills_missing_suites_from_test_runs():
    """Regression: a suite name present on ``test_runs.primary_suite_name``
    but missing from the ``test_suites`` table must be auto-created and
    appear in the response. Without this fix the /suites page hides any
    suite stuck in the live-stream finalize_run gap (CLAUDE.md pitfall #15).
    """
    from app.services.test_suite_service import list_test_suites

    project_id = uuid.uuid4()
    existing_default = _suite("All Tests", is_default=True, project_id=project_id)

    # Simulate the gap: the backfill probe finds a suite name on
    # test_runs that has no matching test_suites row.
    gap_row = SimpleNamespace(
        project_id=project_id,
        suite_name="Realistic TestNG client examples",
    )

    execute_call_count = {"n": 0}

    async def fake_execute(*_args, **_kwargs):
        execute_call_count["n"] += 1
        # 1: suites query → returns one existing suite.
        # 2: missing-rows probe → one gap row to backfill.
        # 3: flush after db.add(...) — SimpleNamespace flush returns None;
        #    SQLAlchemy's session ``add`` is synchronous so this isn't a
        #    call into execute. But ``await db.flush()`` is. Mock both.
        # 4: counts query → returns counts for the existing + backfilled.
        if execute_call_count["n"] == 1:
            return _SuitesExecResult([existing_default])
        if execute_call_count["n"] == 2:
            return _MissingSuitesResult([gap_row])
        # Counts query (the only query after backfill that uses ``.all()``).
        return _CountsExecResult([(existing_default.id, 4)])

    # Track ``add``'d rows so we can verify the backfill happens and the
    # synthesized suite is returned to the caller.
    added: list = []

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=fake_execute),
        flush=AsyncMock(),
        rollback=AsyncMock(),
        add=lambda obj: added.append(obj),
    )

    result = await list_test_suites(db, project_ids=[project_id])

    # Backfilled suite landed in the result.
    by_name = {item["name"]: item for item in result}
    assert "Realistic TestNG client examples" in by_name, (
        "expected the live-stream gap suite to be backfilled and surfaced"
    )
    assert by_name["All Tests"]["is_default"] is True
    # The default suite should still sort first.
    assert result[0]["name"] == "All Tests"
    # Exactly one row was add()'d (the missing suite); not the existing one.
    assert len(added) == 1
    assert added[0].name == "Realistic TestNG client examples"
    assert added[0].project_id == project_id
    assert added[0].is_default is False
