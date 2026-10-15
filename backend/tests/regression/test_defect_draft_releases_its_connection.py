"""The defect-draft request gives its connection back while the LLM writes.

``GET /deep-investigate/{run}/clusters/{cluster}/defect-candidate`` ran
``get_defect_candidate`` on the request's session: the reads, then the LLM
call with the connection still checked out, and the call's cost-cap check
took a second one. A worker has three, so a few drafts at once starved the
pool the way the run-intelligence GET did (#247; homelab, 2026-10-14).

``get_defect_candidate`` now closes a session with nothing pending before the
LLM call; the session reopens for the reads after it. The test drives the
real route and the real run guard on a pool of ONE connection, with an LLM
step that needs a connection of its own, as the cost-cap check does.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import Defect, User, UserRole
from app.routers import deep_investigation
from app.services import defect_promotion_service as dps

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


class _Pool:
    """One connection, with a bounded checkout like QueuePool's."""

    def __init__(self) -> None:
        self._slot = asyncio.Semaphore(1)
        self.held = 0
        self.max_held = 0

    async def checkout(self) -> None:
        await asyncio.wait_for(self._slot.acquire(), timeout=1.0)
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    def checkin(self) -> None:
        self.held -= 1
        self._slot.release()


class _Result:
    def __init__(self, stmt) -> None:
        self._entity = stmt.column_descriptions[0]["name"]

    def scalar_one_or_none(self):
        # select(TestRun) for the project; select(TestRun.project_id) and the
        # membership check want a scalar.
        return SimpleNamespace(project_id=PROJECT_ID) if self._entity == "TestRun" else PROJECT_ID

    def scalars(self):
        cluster = SimpleNamespace(cluster_id="cl_001", label="checkout total", size=2,
                                  representative_error="expected 84.15", member_test_ids=[])
        first = cluster if self._entity == "FailureCluster" else None
        return SimpleNamespace(first=lambda: first, all=lambda: [])


class _Session:
    """Takes the pool's connection on its first query, gives it back on close,
    and can be used again after closing, like an AsyncSession."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool
        self._connected = False
        self.new, self.dirty, self.deleted = [], [], []

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
def pool(monkeypatch):
    pool = _Pool()
    llm_calls: list = []

    async def llm(cluster, analyses):
        # The cost-cap check opens a session of its own during the call.
        async with _Session(pool) as cost_db:
            await cost_db.execute(select(Defect.id))
        llm_calls.append(cluster.cluster_id)
        return dps._deterministic_jira_content(cluster)

    async def duplicates(project_id, duplicate_hint, db):
        await db.execute(select(Defect.id))  # the request session, reopened after the call
        return None, False

    monkeypatch.setattr(dps, "_generate_jira_content", llm)
    monkeypatch.setattr(dps, "_resolve_defect_owner_from_memory", AsyncMock(return_value=None))
    monkeypatch.setattr(dps, "_find_duplicate_semantic", duplicates)
    pool.llm_calls = llm_calls
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

    member = User(id=uuid.uuid4(), username="qa", email="qa@example.test", role=UserRole.QA_ENGINEER, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: member
    return app


@pytest.mark.asyncio
async def test_the_llm_step_gets_a_connection_while_the_request_waits(app, pool):
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as client:
        response = await asyncio.wait_for(
            client.get(f"/api/v1/deep-investigate/{RUN_ID}/clusters/cl_001/defect-candidate"), timeout=5,
        )
    assert response.status_code == 200, response.text
    assert pool.llm_calls == ["cl_001"]
    assert pool.max_held == 1, "the request held its connection across the LLM call"


@pytest.mark.asyncio
async def test_a_session_with_pending_writes_keeps_its_connection():
    db = SimpleNamespace(new=[object()], dirty=[], deleted=[], close=AsyncMock())
    await dps._release_connection_for_llm(db)
    db.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_with_nothing_pending_gives_it_back():
    db = SimpleNamespace(new=[], dirty=[], deleted=[], close=AsyncMock())
    await dps._release_connection_for_llm(db)
    db.close.assert_awaited_once()
