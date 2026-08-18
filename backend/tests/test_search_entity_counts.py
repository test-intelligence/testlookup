"""Unit tests for GET /api/v1/search/entity-counts.

User-reported symptom: the /search page chips showed 0 for Tests, Runs,
Suites, Releases even when the DB had hundreds of rows. The page derives
chip + Index Health counts from ``response.entity_counts`` which is
empty until the user types a query — so a freshly-loaded page was
always misleading. This endpoint backfills the gap with project-scoped
COUNT(*) totals.

These tests pin the endpoint's behaviour against a mock session so the
SQL shape is verified without needing a live Postgres. The pre-query
behaviour itself (chips show these numbers, not 0) is asserted in
``frontend/src/pages/SearchPage.test.tsx``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


class _Result:
    """Mimics SQLAlchemy result row with ``.scalar_one()``."""
    def __init__(self, value: int):
        self._value = value

    def scalar_one(self):
        return self._value


def _db_with_counts(counts: list[int]):
    """Mock async session whose ``execute`` returns the next scalar in order.

    The endpoint runs 6 COUNT queries via asyncio.gather (test_runs,
    test_cases, suites, defects, flaky, releases — see the router). The
    counts list must be in that exact order; asyncio.gather preserves
    submission order on the result side but with an AsyncMock side_effect
    the order is by call order. To stay decoupled from internal ordering,
    we use a side_effect that returns based on a counter.
    """
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Result(n) for n in counts]),
    )
    return db


@pytest.mark.asyncio
async def test_entity_counts_project_scoped_returns_all_six_keys(monkeypatch):
    """Happy path: project-scoped totals come back with the exact keys
    the frontend expects."""
    from app.routers.search import get_entity_counts

    project_id = uuid.uuid4()
    # Order matches the endpoint's asyncio.gather call sequence:
    # test_runs, test_cases, suites, defects, flaky, releases
    db = _db_with_counts([20, 47, 5, 2, 1, 3])

    # resolve_project_scope is auth/membership glue we want to stub out
    # rather than exercise here — it's covered by the router-integration
    # tests under tests/integration/.
    async def fake_resolve(_db, _user, pid):
        return (pid, None)
    monkeypatch.setattr(
        "app.routers.search.resolve_project_scope", fake_resolve,
    )

    result = await get_entity_counts(
        project_id=str(project_id),
        db=db,  # type: ignore[arg-type]
        current_user=MagicMock(),
    )

    assert result == {
        "test_case":  47,
        "test_run":   20,
        "suite":      5,
        "defect":     2,
        "flaky_test": 1,
        "release":    3,
    }
    # Six COUNT queries — one per entity.
    assert db.execute.await_count == 6


@pytest.mark.asyncio
async def test_entity_counts_all_projects_admin_returns_active_totals(monkeypatch):
    """ADMIN sees all active projects, never soft-deleted project data."""
    from app.routers.search import get_entity_counts

    db = _db_with_counts([100, 250, 12, 8, 4, 7])

    async def fake_resolve(_db, _user, _pid):
        # None, None => admin / all-projects
        return (None, None)
    monkeypatch.setattr(
        "app.routers.search.resolve_project_scope", fake_resolve,
    )

    result = await get_entity_counts(
        project_id=None,
        db=db,  # type: ignore[arg-type]
        current_user=MagicMock(),
    )

    assert result["test_case"] == 250
    assert result["test_run"] == 100
    assert result["suite"] == 12

    statements = [call.args[0] for call in db.execute.await_args_list]
    assert len(statements) == 6
    for statement in statements:
        sql = str(statement).lower()
        assert "join projects" in sql
        assert "projects.is_active is true" in sql


@pytest.mark.asyncio
async def test_entity_counts_user_with_no_projects_gets_zeros(monkeypatch):
    """Caller with empty accessible-project set sees all zeros — the
    ``where(False)`` short-circuit ensures we never accidentally leak
    cross-tenant rows when the caller has no memberships."""
    from app.routers.search import get_entity_counts

    db = _db_with_counts([0, 0, 0, 0, 0, 0])

    async def fake_resolve(_db, _user, _pid):
        return (None, set())  # admin no, accessible projects = empty set
    monkeypatch.setattr(
        "app.routers.search.resolve_project_scope", fake_resolve,
    )

    result = await get_entity_counts(
        project_id=None,
        db=db,  # type: ignore[arg-type]
        current_user=MagicMock(),
    )

    assert result == {
        "test_case":  0,
        "test_run":   0,
        "suite":      0,
        "defect":     0,
        "flaky_test": 0,
        "release":    0,
    }


@pytest.mark.asyncio
async def test_entity_counts_handles_null_count_as_zero(monkeypatch):
    """SQLAlchemy COUNT(*) on an empty table returns 0, not None — but
    the endpoint defensively coerces ``None or 0`` to 0 anyway. Pin that
    so a future change to the query (e.g. using SUM instead of COUNT)
    doesn't regress into NoneType errors on the frontend."""
    from app.routers.search import get_entity_counts

    # Synthesise a NULL scalar result for one of the counts.
    class _NullResult:
        def scalar_one(self):
            return None

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _NullResult(),       # test_runs -> None
            _Result(0),
            _Result(0),
            _Result(0),
            _Result(0),
            _Result(0),
        ])
    )

    async def fake_resolve(_db, _user, _pid):
        return (None, None)
    monkeypatch.setattr(
        "app.routers.search.resolve_project_scope", fake_resolve,
    )

    result = await get_entity_counts(
        project_id=None,
        db=db,  # type: ignore[arg-type]
        current_user=MagicMock(),
    )

    assert result["test_run"] == 0
