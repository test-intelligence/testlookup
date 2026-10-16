"""``GET /api/v1/runs/ai-verdicts`` (W07 FAIL-04): validation, authorization wiring, route order.

The batch read itself runs on PostgreSQL in
``tests/integration/test_run_ai_verdicts_postgres.py``; here the route is driven
over HTTP with the service stubbed:

* 1-25 DISTINCT ids (duplicates collapse, request order kept), else 422 with
  the one fixed text; a malformed id and a missing ``run_id`` are 422 too;
* the caller's accessible projects (``get_accessible_project_ids``: ``None`` for
  an admin) reach the service, which is what answers foreign ids as unavailable;
* the literal path is served by THIS handler, not swallowed by ``GET /{run_id}``.
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

URL = "/api/v1/runs/ai-verdicts"


def _user(role=UserRole.QA_ENGINEER) -> User:
    return User(id=uuid.uuid4(), username="u", email="u@example.test", role=role, is_active=True)


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    accessible = {"value": {uuid.uuid4()}}

    async def _batch(db, ids, *, accessible):
        calls.append({"ids": list(ids), "accessible": accessible})
        return {
            "items": [{"run_id": str(i), "snapshot": False, "stale": None, "generated_at": None,
                       "analysed": False, "verdict_inputs": None, "review": None} for i in ids[:1]],
            "unavailable": [str(i) for i in ids[1:]],
            "max_ids": 25,
        }

    monkeypatch.setattr(runs_router.run_verdict_service, "batch_verdict_inputs", _batch)
    monkeypatch.setattr(runs_router, "get_accessible_project_ids",
                        AsyncMock(side_effect=lambda db, user: accessible["value"]))
    app = FastAPI()
    app.include_router(runs_router.router)

    async def _db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _user
    return app, calls, accessible


async def _get(app, params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(URL, params=params)


async def test_twenty_five_ids_reach_the_service_in_request_order(wired):
    app, calls, accessible = wired
    ids = [uuid.uuid4() for _ in range(25)]
    response = await _get(app, [("run_id", str(i)) for i in ids])
    assert response.status_code == 200, response.text
    assert calls == [{"ids": ids, "accessible": accessible["value"]}]
    body = response.json()
    assert set(body) == {"items", "unavailable", "max_ids"}
    assert body["items"][0]["run_id"] == str(ids[0]) and body["unavailable"] == [str(i) for i in ids[1:]]


async def test_duplicates_collapse_before_the_count(wired):
    app, calls, _ = wired
    ids = [uuid.uuid4() for _ in range(25)]
    response = await _get(app, [("run_id", str(i)) for i in ids + ids[:5]])
    assert response.status_code == 200
    assert calls[0]["ids"] == ids


@pytest.mark.parametrize("count", [26, 0])
async def test_outside_one_to_twenty_five_is_a_422_with_one_text(wired, count):
    app, calls, _ = wired
    response = await _get(app, [("run_id", str(uuid.uuid4())) for _ in range(count)])
    assert response.status_code == 422
    if count:
        assert response.json() == {"detail": "run_id: between 1 and 25 run ids."}
    assert calls == []


async def test_a_malformed_id_is_a_422_and_reaches_nothing(wired):
    app, calls, _ = wired
    response = await _get(app, [("run_id", "not-a-uuid")])
    assert response.status_code == 422
    assert calls == []


async def test_an_admin_reaches_the_service_unrestricted(wired):
    app, calls, accessible = wired
    accessible["value"] = None
    response = await _get(app, [("run_id", str(uuid.uuid4()))])
    assert response.status_code == 200
    assert calls[0]["accessible"] is None


def test_the_literal_route_is_declared_above_the_run_id_route():
    paths = [getattr(route, "path", "") for route in runs_router.router.routes]
    assert paths.index("/api/v1/runs/ai-verdicts") < paths.index("/api/v1/runs/{run_id}")
