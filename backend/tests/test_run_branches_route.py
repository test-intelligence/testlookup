"""``GET /api/v1/runs/branches`` (W07 FAIL-05): the Runs toolbar's branch list.

Over HTTP with the service stubbed: a malformed ``project_id`` is 400, a
project the caller is not a member of is 403 (and the service is never
reached), ``days=0`` means all time (``days=None`` to the service), the default
window is 30 days, ``days`` outside 0-365 is 422, and the literal path is served
above ``GET /{run_id}``. The SQL (order, all time, ``total`` beyond the cap)
runs on PostgreSQL in ``tests/integration/test_runs_list_filters_postgres.py``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.routers import runs as runs_router

URL = "/api/v1/runs/branches"
MINE = uuid.uuid4()


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []

    async def _branches(db, project_id, *, days, limit=100):
        calls.append({"project_id": project_id, "days": days})
        return {"items": [{"branch": "main", "runs": 3, "last_run_at": "2026-10-15T00:00:00+00:00"}],
                "total": 1, "truncated": False, "window_days": days}

    monkeypatch.setattr(runs_router.runs_service, "list_run_branches", _branches)
    monkeypatch.setattr(runs_router, "get_accessible_project_ids", AsyncMock(return_value={MINE}))
    app = FastAPI()
    app.include_router(runs_router.router)

    async def _db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(), username="u", email="u@example.test", role=UserRole.VIEWER, is_active=True,
    )
    return app, calls


async def _get(app, params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(URL, params=params)


async def test_a_member_gets_the_window_and_the_default_is_thirty_days(wired):
    app, calls = wired
    response = await _get(app, {"project_id": str(MINE)})
    assert response.status_code == 200, response.text
    assert calls == [{"project_id": MINE, "days": 30}]
    assert response.json() == {
        "items": [{"branch": "main", "runs": 3, "last_run_at": "2026-10-15T00:00:00+00:00"}],
        "total": 1, "truncated": False, "window_days": 30,
    }


async def test_zero_days_is_all_time(wired):
    app, calls = wired
    response = await _get(app, {"project_id": str(MINE), "days": 0})
    assert response.status_code == 200
    assert calls == [{"project_id": MINE, "days": None}]
    assert response.json()["window_days"] is None


async def test_a_foreign_project_is_403_and_a_malformed_one_400(wired):
    app, calls = wired
    assert (await _get(app, {"project_id": str(uuid.uuid4())})).status_code == 403
    assert (await _get(app, {"project_id": "nope"})).status_code == 400
    assert (await _get(app, {})).status_code == 422
    assert (await _get(app, {"project_id": str(MINE), "days": 366})).status_code == 422
    assert calls == []


def test_the_literal_route_is_declared_above_the_run_id_route():
    paths = [getattr(route, "path", "") for route in runs_router.router.routes]
    assert paths.index("/api/v1/runs/branches") < paths.index("/api/v1/runs/{run_id}")
