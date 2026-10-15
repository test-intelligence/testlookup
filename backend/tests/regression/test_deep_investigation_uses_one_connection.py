"""The deep-investigation routes run on the request's own session.

``GET /deep-investigate/{run}/clusters`` and ``/findings`` (which the run
page's Analysis tab calls), and the trigger and promote POSTs, opened a fresh
``AsyncSessionLocal()`` while ``require_run_access`` -- and the auth lookup
before it -- held the request's ``get_db`` session. One request therefore
needed two pooled connections at once. A production worker has three (2 + 1),
so two or three of these requests in one worker each held one and waited for
another: the worker stalled for the 30 s pool timeout and answered 500
(homelab re-test of the Analysis tab, 2026-10-14).

The handlers now take ``db: AsyncSession = Depends(get_db)``, which FastAPI
resolves to the guard's session. The test drives the real routes and the real
guard on a pool of ONE connection: a handler that opens a second session waits
for a connection that never frees, exactly as on the homelab, and fails.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.routers import deep_investigation

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


class _OneConnectionPool:
    """A connection pool of size one with a bounded checkout, like QueuePool's."""

    def __init__(self, timeout: float = 1.0) -> None:
        self._slot = asyncio.Semaphore(1)
        self._timeout = timeout
        self.held = 0
        self.max_held = 0
        # Every checkout ATTEMPT. The AI-config resolver swallows a database
        # error into env defaults, so a second session that times out here
        # would not fail the request; counting attempts makes it visible.
        self.attempts = 0

    async def checkout(self) -> None:
        self.attempts += 1
        await asyncio.wait_for(self._slot.acquire(), timeout=self._timeout)
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    def checkin(self) -> None:
        self.held -= 1
        self._slot.release()


# W04 AIC-01: the trigger's gate reads the stored Settings > AI row through the
# request session (a cache miss on the effective config). Deep investigation
# on, mode llm: the gate lets the trigger through.
STORED_AI_CONFIG = {"deep_investigation_enabled": True, "analysis_mode": "llm"}


class _Result:
    def __init__(self, stmt) -> None:
        entity = stmt.column_descriptions[0]["name"] if hasattr(stmt, "column_descriptions") else ""
        # select(TestRun) wants a row; select(AppSetting) the stored AI config;
        # select(SecretRef) no secret; select(TestRun.project_id) / membership a scalar.
        if entity == "TestRun":
            self._scalar = SimpleNamespace(id=RUN_ID, project_id=PROJECT_ID, build_number="b-1")
        elif entity == "AppSetting":
            self._scalar = SimpleNamespace(key="ai_config", value=dict(STORED_AI_CONFIG))
        elif entity == "SecretRef":
            self._scalar = None
        else:
            self._scalar = PROJECT_ID

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: [], first=lambda: None)


class _Session:
    """Takes the pool's connection on its first query and gives it back on close."""

    def __init__(self, pool: _OneConnectionPool) -> None:
        self._pool = pool
        self._connected = False

    async def execute(self, stmt, *args, **kwargs):
        if not self._connected:
            await self._pool.checkout()
            self._connected = True
        return _Result(stmt)

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        if self._connected:
            self._connected = False
            self._pool.checkin()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


@pytest.fixture
def pool(monkeypatch, fake_redis):
    pool = _OneConnectionPool()
    # The old handlers built their own session here; on a one-connection pool
    # it must come from the same pool the request session uses. The resolver's
    # own fallback session (db=None) comes from the same pool too, so a gate
    # that forgot to pass the request session would stall here as well.
    monkeypatch.setattr(deep_investigation, "AsyncSessionLocal", lambda: _Session(pool), raising=False)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", lambda: _Session(pool))
    # An EMPTY in-memory Redis: the gate misses every cache and must read the
    # stored row through the request session (never a real Redis).
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    # What lies past the session code: the queue, the promotion.
    monkeypatch.setattr(
        deep_investigation.run_agent_pipeline, "apply_async", MagicMock(return_value=SimpleNamespace(id="task-1")),
    )
    monkeypatch.setattr(
        deep_investigation,
        "promote_cluster",
        AsyncMock(return_value={"defect_id": str(uuid.uuid4()), "cluster_id": "cl_1", "severity": "HIGH", "title": "t"}),
    )
    import app.services.cache_service as cache_service

    monkeypatch.setattr(cache_service, "bump_analytics_epoch", AsyncMock())
    return pool


@pytest.fixture
def app(pool):
    app = FastAPI()
    app.include_router(deep_investigation.router)

    async def _get_db():
        session = _Session(pool)
        try:
            yield session
        finally:
            await session.close()

    # Not an admin: the run guard queries the request session, as it does for
    # every member, so that session holds the pool's connection from here on.
    member = User(id=uuid.uuid4(), username="qa", email="qa@example.test", role=UserRole.QA_ENGINEER, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: member
    return app


ROUTES = [
    ("GET", f"/api/v1/deep-investigate/{RUN_ID}/clusters", None),
    ("GET", f"/api/v1/deep-investigate/{RUN_ID}/findings", None),
    ("POST", f"/api/v1/deep-investigate/{RUN_ID}", {"mode": "deep"}),
    ("POST", f"/api/v1/deep-investigate/{RUN_ID}/clusters/cl_1/promote", {"title": "t"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=["clusters", "findings", "trigger", "promote"])
async def test_the_route_needs_one_connection(app, pool, method, path, body):
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as client:
        response = await asyncio.wait_for(client.request(method, path, json=body), timeout=5)
    assert response.status_code == 200, response.text
    assert pool.max_held == 1
    assert pool.attempts == 1, "a second session was opened inside the handler"


@pytest.mark.asyncio
async def test_the_pool_double_refuses_a_second_session(pool):
    """The harness itself: a second session while the first holds the
    connection times out, so the route test above is not vacuously green."""
    first = _Session(pool)
    await first.execute(SimpleNamespace())
    with pytest.raises(asyncio.TimeoutError):
        await _Session(pool).execute(SimpleNamespace())
    await first.close()
