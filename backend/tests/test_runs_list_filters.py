"""``GET /api/v1/runs`` gains the Runs toolbar's ``q`` / ``branch`` / ``recommendation`` (W07 FAIL-05).

Router: each value is parsed BEFORE any database access (one fixed 422 text per
parameter) and reaches ``list_project_runs`` on BOTH branches of the handler
(with and without ``project_id``); absent values reach it as "no filter".
Service: the predicates the toolbar adds to the SHARED filter list, compiled.
The behaviour against real rows (escapes, prefix, servable snapshot, parity
with the pill, ``total``) is ``tests/integration/test_runs_list_filters_postgres.py``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import TestRun, User, UserRole
from app.routers import runs as runs_router
from app.services.runs_service import run_list_toolbar_filters

MINE = uuid.uuid4()


class _NoDb:
    """A session that refuses every statement: proves a 422 is decided before the database."""

    async def execute(self, *args, **kwargs):
        raise AssertionError("the request reached the database")


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []

    async def _list(db, project_id, page, size, status=None, release_id=None, accessible_project_ids=None,
                    days=6, suite_name=None, *, q=None, branches=(), recommendation=None):
        calls.append({"project_id": project_id, "q": q, "branches": branches, "recommendation": recommendation,
                      "days": days})
        return [], 0, 0

    monkeypatch.setattr(runs_router, "list_project_runs", _list)
    monkeypatch.setattr(runs_router, "get_accessible_project_ids", AsyncMock(return_value={MINE}))
    app = FastAPI()
    app.include_router(runs_router.router)

    async def _db():
        yield _NoDb()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(), username="u", email="u@example.test", role=UserRole.QA_ENGINEER, is_active=True,
    )
    return app, calls


async def _get(app, params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get("/api/v1/runs", params=params)


@pytest.mark.parametrize("with_project", [False, True])
async def test_the_toolbar_reaches_the_service_on_both_branches(wired, with_project):
    app, calls = wired
    params = [("q", "  1234 "), ("branch", " main"), ("branch", "release/1.2"), ("branch", "main"),
              ("recommendation", "NO_GO")]
    if with_project:
        params.append(("project_id", str(MINE)))
    response = await _get(app, params)
    assert response.status_code == 200, response.text
    (call,) = calls
    assert (call["q"], call["branches"], call["recommendation"]) == ("1234", ("main", "release/1.2"), "NO_GO")
    assert call["project_id"] == (str(MINE) if with_project else None)


async def test_absent_or_blank_values_are_no_filter(wired):
    app, calls = wired
    assert (await _get(app, [("q", "   ")])).status_code == 200
    assert (await _get(app, [])).status_code == 200
    assert [(c["q"], c["branches"], c["recommendation"]) for c in calls] == [(None, (), None), (None, (), None)]
    assert all(c["days"] == 30 for c in calls), "the 30-day default is unchanged"


@pytest.mark.parametrize("params, detail", [
    ([("recommendation", "maybe")], "recommendation must be one of GO, CONDITIONAL_GO, NO_GO, none."),
    ([("recommendation", "go")], "recommendation must be one of GO, CONDITIONAL_GO, NO_GO, none."),
    ([("branch", f"b{i}") for i in range(21)], "branch: at most 20 names of 1-255 characters."),
    ([("branch", "   ")], "branch: at most 20 names of 1-255 characters."),
    ([("branch", "x" * 256)], "branch: at most 20 names of 1-255 characters."),
])
async def test_a_bad_value_is_a_422_before_any_database_access(wired, params, detail):
    app, calls = wired
    response = await _get(app, params)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert calls == []


async def test_q_is_capped_at_a_hundred_characters(wired):
    app, calls = wired
    assert (await _get(app, [("q", "x" * 101)])).status_code == 422
    assert (await _get(app, [("q", "x" * 100)])).status_code == 200
    assert len(calls) == 1


async def test_every_filter_value_is_accepted(wired):
    app, calls = wired
    for value in ("GO", "CONDITIONAL_GO", "NO_GO", "none"):
        assert (await _get(app, [("recommendation", value)])).status_code == 200
    assert [c["recommendation"] for c in calls] == ["GO", "CONDITIONAL_GO", "NO_GO", "none"]
    duplicates = [("branch", "main")] * 25  # 25 copies of one name are one name
    assert (await _get(app, duplicates)).status_code == 200


def _compiled(filters) -> tuple[str, dict]:
    compiled = select(TestRun.id).where(*filters).compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


def test_no_toolbar_value_adds_no_predicate():
    assert run_list_toolbar_filters() == []
    assert run_list_toolbar_filters(q=None, branches=(), recommendation=None) == []


def test_q_escapes_like_characters_and_prefixes_the_commit():
    sql, params = _compiled(run_list_toolbar_filters(q="50%_a\\b"))
    for column in ("build_number", "branch", "jenkins_job", "commit_hash"):
        assert f"test_runs.{column} ILIKE " in sql
    assert sql.count("ESCAPE '\\\\'") == 4 or sql.count("ESCAPE '\\'") == 4
    escaped = "50\\%\\_a\\\\b"
    assert sorted(params.values()) == sorted([f"%{escaped}%"] * 3 + [f"{escaped}%"]), "contains x3, commit prefix"


def test_branches_are_an_exact_in_list():
    sql, params = _compiled(run_list_toolbar_filters(branches=("main", "release/1.2")))
    assert "test_runs.branch IN (__[POSTCOMPILE_branch_1])" in sql
    assert params == {"branch_1": ["main", "release/1.2"]}


def test_a_recommendation_requires_a_servable_snapshot_and_none_is_its_complement():
    go, go_params = _compiled(run_list_toolbar_filters(recommendation="GO"))
    assert "test_runs.ai_recommendation = %(ai_recommendation_1)s" in go
    assert go_params["ai_recommendation_1"] == "GO"
    assert "EXISTS (SELECT run_intelligence_snapshots.id" in go
    assert "run_intelligence_snapshots.run_id = test_runs.id" in go
    assert 5 in go_params.values(), "schema_version >= CURRENT_SCHEMA_VERSION"
    none, _ = _compiled(run_list_toolbar_filters(recommendation="none"))
    assert "test_runs.ai_recommendation IS NULL OR NOT (EXISTS (SELECT run_intelligence_snapshots.id" in none
