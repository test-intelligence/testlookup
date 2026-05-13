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
            _CountsExecResult(counts_rows),
        ])
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
        execute=AsyncMock(return_value=_SuitesExecResult([suite]))
    )

    result = await list_test_suites(
        db, project_ids=[project_id], include_counts=False,
    )

    assert len(result) == 1
    assert result[0]["test_case_count"] == 0
    assert db.execute.await_count == 1  # only the suites query, no counts


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
