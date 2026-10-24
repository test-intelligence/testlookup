"""W12 SRCH-03: a saved search round-trips through the UNEDITED ``routers/saved_views.py``.

Saved searches are ``saved_views`` rows (``page: "search"``, ``filters:
{kind: "search", q, scope, mode}``). Nothing in the saved-views router was
changed for them: the grown ``SAVED_VIEW_PAGES`` Literal and the
``kind: "search"`` validation (``schemas.py``) are the whole feature on the
server. This drives the real router through the ASGI app on a scripted
session: create -> listed under ``?page=search&project_id=`` -> a QA engineer
of ANOTHER project gets 403 reading it by id (the existing rule).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.models.postgres import SavedView, UserRole

PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()
OWNER = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER, username="owner")
OUTSIDER = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER, username="outsider")
MEMBERSHIPS = {OWNER.id: {PROJECT}, OUTSIDER.id: {OTHER_PROJECT}}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _ScriptedSession:
    """Keeps the added SavedView rows; every SELECT answers them (the router filters in SQL,
    which this records and the test reads)."""

    def __init__(self) -> None:
        self.rows: list[SavedView] = []
        self.statements: list = []
        self.commits = 0

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        for row in self.rows:
            row.id = row.id or uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        row.created_at = row.created_at or datetime(2026, 10, 24, tzinfo=timezone.utc)

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return _Result(self.rows)


@pytest.fixture
def world(monkeypatch):
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import saved_views

    async def accessible(db, user):
        return MEMBERSHIPS[user.id]

    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", accessible)
    recorded = AsyncMock()
    monkeypatch.setattr(saved_views, "record_activity", recorded)
    session = _ScriptedSession()
    caller = {"user": OWNER}
    app = FastAPI()
    app.include_router(saved_views.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_active_user] = lambda: caller["user"]
    return SimpleNamespace(app=app, session=session, caller=caller, recorded=recorded)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


SEARCH = {"kind": "search", "q": 'status:failed suite:"API Regression" checkout', "scope": "tests", "mode": "keyword"}


async def test_a_saved_search_round_trips_and_stays_inside_its_project(world):
    async with _client(world.app) as client:
        created = await client.post("/api/v1/saved-views", json={
            "name": "Failing checkout", "page": "search", "project_id": str(PROJECT),
            "filters": SEARCH, "is_shared": True,
        })
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["page"] == "search" and body["filters"] == SEARCH
        assert body["project_id"] == str(PROJECT) and body["is_shared"] is True
        assert world.session.commits == 1
        # A project-bound saved search rides the existing saved_view.* events.
        assert [c.kwargs["event_type"] for c in world.recorded.await_args_list] == [
            "saved_view.created", "saved_view.shared",
        ]

        listed = await client.get("/api/v1/saved-views", params={"page": "search", "project_id": str(PROJECT)})
        assert listed.status_code == 200, listed.text
        assert [v["id"] for v in listed.json()] == [body["id"]]
        where = str(world.session.statements[-1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
        )).lower()
        assert "saved_views.page = 'search'" in where
        assert f"saved_views.project_id = '{PROJECT}'" in where

        world.caller["user"] = OUTSIDER
        refused = await client.get(f"/api/v1/saved-views/{body['id']}")
        assert refused.status_code == 403
        assert refused.json()["detail"] == "You do not have access to this project"
        listed_elsewhere = await client.get(
            "/api/v1/saved-views", params={"page": "search", "project_id": str(PROJECT)},
        )
        assert listed_elsewhere.status_code == 403


async def test_a_search_saved_for_all_projects_cannot_be_shared(world):
    async with _client(world.app) as client:
        refused = await client.post("/api/v1/saved-views", json={
            "name": "Everywhere", "page": "search", "project_id": None, "filters": SEARCH, "is_shared": True,
        })
    assert refused.status_code == 422
    assert "A search saved for all projects cannot be shared: pin a project first." in refused.text
    assert world.session.rows == []
