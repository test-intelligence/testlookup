"""W12 SRCH-03: suggested searches for one project (``GET /search/suggested``).

Three bounded statements on the request session -- the tests failing most in
the last 7 days, the newest open defects with a Jira key, the active release
-- each on an ACTIVE project, each a top-N (a failing test carries its own
count). The project is authorised FIRST by ``resolve_project_scope``.
The live-database proof is ``tests/integration/test_search_suggestions_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.models.postgres import UserRole
from app.services.search_suggestions import suggestions_for_project

PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _ScriptedDB:
    def __init__(self, *answers):
        self._answers = list(answers)
        self.statements = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return _Result(self._answers.pop(0))


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


async def test_three_bounded_statements_and_the_queries_they_build():
    db = _ScriptedDB(
        [SimpleNamespace(test_name="test_checkout total", failures=12), SimpleNamespace(test_name="test_login", failures=1)],
        [SimpleNamespace(jira_ticket_id="PAY-42", severity="high", resolution_status="OPEN"),
         SimpleNamespace(jira_ticket_id="PAY-41", severity=None, resolution_status="IN_PROGRESS")],
        ["2026.11 RC"],
    )
    body = await suggestions_for_project(db, PROJECT, now=NOW)

    assert len(db.statements) == 3
    failing_sql, defects_sql, release_sql = (_sql(s) for s in db.statements)
    since = (NOW - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    for fragment in (
        f"test_runs.project_id = '{PROJECT}'", "projects.is_active is true",
        f"test_runs.created_at >= '{since}", f"test_cases.created_at >= '{since}",
        "test_cases.status in ('failed', 'broken')", "group by test_cases.test_name",
        "order by count(*) desc, test_cases.test_name", "limit 5",
    ):
        assert fragment in failing_sql, fragment
    for fragment in (
        f"defects.project_id = '{PROJECT}'", "projects.is_active is true", "defects.jira_ticket_id is not null",
        "defects.resolution_status in ('open', 'in_progress')", "order by defects.created_at desc", "limit 5",
    ):
        assert fragment in defects_sql, fragment
    for fragment in (f"releases.project_id = '{PROJECT}'", "releases.is_active is true", "limit 1"):
        assert fragment in release_sql, fragment

    assert body["project_id"] == PROJECT and body["window_days"] == 7
    assert body["failing_tests"] == [
        {"kind": "failing_test", "label": "test_checkout total", "q": '"test_checkout total"', "scope": "tests",
         "detail": "12 failures in 7 days"},
        {"kind": "failing_test", "label": "test_login", "q": "test_login", "scope": "tests",
         "detail": "1 failure in 7 days"},
    ]
    assert body["open_defects"] == [
        {"kind": "open_defect", "label": "PAY-42", "q": "PAY-42", "scope": "defects", "detail": "Open · HIGH"},
        {"kind": "open_defect", "label": "PAY-41", "q": "PAY-41", "scope": "defects", "detail": "In progress"},
    ]
    assert body["active_release"] == {
        "kind": "active_release", "label": "2026.11 RC", "q": 'release:"2026.11 RC" status:failed',
        "scope": "tests", "detail": "Active release",
    }


async def test_nothing_to_suggest_is_three_empty_groups():
    body = await suggestions_for_project(_ScriptedDB([], [], []), PROJECT, now=NOW)
    assert body["failing_tests"] == [] and body["open_defects"] == [] and body["active_release"] is None


async def test_a_suggestion_query_is_parsed_like_typed_text():
    """The release suggestion's query must apply as the two filters it names."""
    from app.services.search_query_parser import parse_query

    body = await suggestions_for_project(_ScriptedDB([], [], ["2026.11 RC"]), PROJECT, now=NOW)
    parsed = parse_query(body["active_release"]["q"], now=NOW)
    assert parsed.releases == ("2026.11 RC",) and parsed.statuses == frozenset({"failed"})
    assert parsed.ignored == () and parsed.text == ""


# ── The route: the project is authorised first ──────────────────────────────


def _user(role=UserRole.QA_ENGINEER):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


async def test_a_non_member_naming_the_project_is_refused_before_any_read(monkeypatch):
    from app.routers import search as router

    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={uuid.uuid4()}))
    db = _ScriptedDB()
    with pytest.raises(HTTPException) as raised:
        await router.suggested_searches(project_id=str(PROJECT), db=db, current_user=_user())
    assert raised.value.status_code == 403
    assert raised.value.detail == "You do not have access to this project"
    assert db.statements == []


async def test_a_malformed_project_id_is_400(monkeypatch):
    from app.routers import search as router

    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=set()))
    with pytest.raises(HTTPException) as raised:
        await router.suggested_searches(project_id="not-a-uuid", db=_ScriptedDB(), current_user=_user())
    assert raised.value.status_code == 400 and raised.value.detail == "Invalid project ID"


async def test_an_empty_project_id_is_400_not_every_project(monkeypatch):
    from app.routers import search as router

    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as raised:
        await router.suggested_searches(project_id="", db=_ScriptedDB(), current_user=_user(UserRole.ADMIN))
    assert raised.value.status_code == 400


async def test_a_member_gets_the_suggestions(monkeypatch):
    from app.routers import search as router

    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={PROJECT}))
    body = await router.suggested_searches(
        project_id=str(PROJECT), db=_ScriptedDB([], [], []), current_user=_user(),
    )
    assert body["project_id"] == PROJECT


async def test_the_project_id_query_param_is_required():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import search as router

    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_active_user] = lambda: _user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/api/v1/search/suggested")
    assert response.status_code == 422
