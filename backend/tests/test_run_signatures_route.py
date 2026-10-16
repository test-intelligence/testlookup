"""``GET /api/v1/runs/failure-signatures`` (W07 FAIL-03): validation, authorization wiring, route order.

The grouping itself runs on PostgreSQL in
``tests/integration/test_run_signatures_postgres.py``. Here: 1-50 distinct ids
(else the one 422 text), the caller's accessible projects reach the service,
and the literal path is served above ``GET /{run_id}``.
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

URL = "/api/v1/runs/failure-signatures"


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    accessible = {"value": {uuid.uuid4()}}

    async def _batch(db, ids, *, accessible):
        calls.append({"ids": list(ids), "accessible": accessible})
        return {
            "items": [{
                "run_id": str(ids[0]), "failing_tests": 2, "read": 2, "capped": False,
                "dominant": "boom #",
                "signatures": [{"signature": "boom #", "label": "Boom 1", "count": 2, "test_count": 2,
                                "tests": ["test_a", "test_b"]}],
            }],
            "unavailable": [str(i) for i in ids[1:]],
            "max_ids": 50,
            "rows_per_run_cap": 500,
        }

    monkeypatch.setattr(runs_router.run_signature_service, "batch_run_signatures", _batch)
    monkeypatch.setattr(runs_router, "get_accessible_project_ids",
                        AsyncMock(side_effect=lambda db, user: accessible["value"]))
    app = FastAPI()
    app.include_router(runs_router.router)

    async def _db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(), username="u", email="u@example.test", role=UserRole.TESTER, is_active=True,
    )
    return app, calls, accessible


async def _get(app, params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(URL, params=params)


async def test_fifty_ids_reach_the_service_with_the_callers_projects(wired):
    app, calls, accessible = wired
    ids = [uuid.uuid4() for _ in range(50)]
    response = await _get(app, [("run_id", str(i)) for i in ids + ids[:3]])
    assert response.status_code == 200, response.text
    assert calls == [{"ids": ids, "accessible": accessible["value"]}]
    body = response.json()
    assert body["max_ids"] == 50 and body["rows_per_run_cap"] == 500
    assert body["items"][0]["signatures"][0]["tests"] == ["test_a", "test_b"]


async def test_fifty_one_ids_is_a_422_with_one_text(wired):
    app, calls, _ = wired
    response = await _get(app, [("run_id", str(uuid.uuid4())) for _ in range(51)])
    assert response.status_code == 422
    assert response.json() == {"detail": "run_id: between 1 and 50 run ids."}
    assert calls == []


async def test_a_malformed_or_missing_id_is_a_422(wired):
    app, calls, _ = wired
    assert (await _get(app, [("run_id", "nope")])).status_code == 422
    assert (await _get(app, [])).status_code == 422
    assert calls == []


async def test_an_admin_reaches_the_service_unrestricted(wired):
    app, calls, accessible = wired
    accessible["value"] = None
    assert (await _get(app, [("run_id", str(uuid.uuid4()))])).status_code == 200
    assert calls[0]["accessible"] is None


def test_the_literal_route_is_declared_above_the_run_id_route():
    paths = [getattr(route, "path", "") for route in runs_router.router.routes]
    assert paths.index("/api/v1/runs/failure-signatures") < paths.index("/api/v1/runs/{run_id}")
