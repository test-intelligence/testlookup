"""No request holds its connection while it opens a second session.

A worker's pool is three connections (2 + 1). Four request paths needed two
at once:

* the run-summary GET, on a cache miss, saved the snapshot in a fresh session
  while its own still held one; so did the summary refresh POST and the
  export's recompute-and-cache (``intelligence_snapshot_service.get_or_compute``);
* the release override (``POST /release-readiness/{run}/override``) did all
  its work in a fresh ``AsyncSessionLocal()`` while the run guard held the
  request's session.

The cache writes now hand the request session's connection back first
(``app.db.postgres.release_read_connection``) and the override works on the
request's own session. Every test here runs on a pool of ONE connection with a
bounded checkout: a path that needs a second connection while it holds the
first waits for one that never frees, as on the homelab (2026-10-13/14).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, FastAPI, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.db.postgres as pg
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import TestRun, User, UserRole
from app.models.schemas import ReleaseCouncilResponse

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


class _Pool:
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
    def scalar_one_or_none(self):
        return PROJECT_ID  # run -> project lookups and the membership check

    def scalars(self):
        return SimpleNamespace(first=lambda: None, all=lambda: [])


class _Session:
    """Takes the pool's connection on its first query, gives it back on close,
    and opens again on the next query, like an AsyncSession."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool
        self._connected = False
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, *args, **kwargs):
        if not self._connected:
            await self._pool.checkout()
            self._connected = True
        return _Result()

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


async def _query(db) -> None:
    await db.execute(select(TestRun.id))


@pytest.fixture
def pool(monkeypatch):
    pool = _Pool()
    # The cache writes open their write session from here.
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _Session(pool), raising=False)
    return pool


@pytest.fixture
def summary(monkeypatch):
    """The run-summary router with its compute, cache and review stubbed to
    run queries on the sessions they are handed, recording the cache write."""
    from app.routers import run_intelligence as ri

    saved: list = []

    async def compute(run_id, db, mongo, include=None, report_version=None):
        await _query(db)
        return {"run": {"id": str(run_id)}, "provenance": {}}

    async def save(write_db, run_id, payload, fallback_used=False):
        await _query(write_db)
        saved.append(run_id)

    async def review(db, response, run_id, payload, **kwargs):
        await _query(db)  # the request session, reopened after the cache write
        return payload

    monkeypatch.setattr(ri, "get_cached_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(ri, "get_stale_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(ri, "get_run_intelligence", compute)
    monkeypatch.setattr(ri, "save_snapshot", save)
    monkeypatch.setattr(ri, "_with_review", review)
    monkeypatch.setattr(ri, "invalidate", AsyncMock(side_effect=lambda db, run_id: _query(db)))
    monkeypatch.setattr(ri, "get_mongo_db", lambda: object())
    return SimpleNamespace(router=ri, saved=saved)


@pytest.mark.asyncio
async def test_the_summary_cache_write_gets_a_connection(pool, summary):
    request_db = _Session(pool)
    await _query(request_db)  # the run guard's read: the request now holds the connection
    out = await asyncio.wait_for(summary.router.get_run_intelligence_endpoint(
        run_id=RUN_ID, response=Response(), background=BackgroundTasks(),
        include="", report_version=None, db=request_db, _=None,
    ), timeout=5)
    await request_db.close()
    assert out["run"]["id"] == str(RUN_ID)
    assert summary.saved == [RUN_ID], "the snapshot write never got a connection"
    assert pool.max_held == 1


@pytest.mark.asyncio
async def test_the_summary_refresh_write_gets_a_connection(pool, summary):
    request_db = _Session(pool)
    await _query(request_db)
    await asyncio.wait_for(summary.router.refresh_intelligence(
        run_id=RUN_ID, response=Response(), db=request_db, _=None,
    ), timeout=5)
    await request_db.close()
    assert summary.saved == [RUN_ID]
    assert pool.max_held == 1


@pytest.mark.asyncio
async def test_the_export_recompute_write_gets_a_connection(pool, monkeypatch):
    from app.services import intelligence_snapshot_service as snapshots

    saved: list = []

    async def compute(run_id, db, mongo, *args, **kwargs):
        await _query(db)
        return {"run": {"id": str(run_id)}, "provenance": {}}

    async def save(write_db, run_id, payload, fallback_used=False):
        await _query(write_db)
        saved.append(run_id)

    monkeypatch.setattr(snapshots, "get_cached_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(snapshots, "save_snapshot", save)
    monkeypatch.setattr("app.services.run_intelligence_service.get_run_intelligence", compute)
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: object())
    request_db = _Session(pool)
    await _query(request_db)
    payload = await asyncio.wait_for(snapshots.get_or_compute(request_db, RUN_ID), timeout=5)
    await _query(request_db)  # the caller keeps reading: the session reopens
    await request_db.close()
    assert payload["run"]["id"] == str(RUN_ID)
    assert saved == [RUN_ID]
    assert pool.max_held == 1


@pytest.mark.asyncio
async def test_a_session_with_pending_writes_keeps_its_connection():
    db = SimpleNamespace(new=[object()], dirty=[], deleted=[], close=AsyncMock())
    assert await pg.release_read_connection(db) is False
    db.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_release_override_runs_on_the_requests_session(pool, monkeypatch):
    from app.routers import release_readiness

    council = ReleaseCouncilResponse(
        run_id=str(RUN_ID), recommendation="GO", risk_score=40, original_recommendation="NO_GO",
        override_audit=[{"timestamp": "2026-10-15T00:00:00+00:00", "before_recommendation": "NO_GO",
                         "before_risk_score": 40, "after_recommendation": "GO", "reason": "accepted"}],
    )

    async def apply_override(*, run_id, override_recommendation, reason, actor, db):
        await _query(db)  # the real one reads and stages the decision on the session it is given
        return council

    monkeypatch.setattr(release_readiness, "apply_override", apply_override)
    monkeypatch.setattr(release_readiness, "record_activity", AsyncMock())
    # The old handler opened its own session here, on the same pool.
    monkeypatch.setattr(release_readiness, "AsyncSessionLocal", lambda: _Session(pool), raising=False)
    monkeypatch.setattr("app.services.intelligence_snapshot_service.stage_invalidate", AsyncMock())
    monkeypatch.setattr("app.services.release_decision_webhook.emit_release_decided", AsyncMock())

    app = FastAPI()
    app.include_router(release_readiness.router)

    async def _get_db():
        session = _Session(pool)
        try:
            yield session
        finally:
            await session.close()

    lead = User(id=uuid.uuid4(), username="lead", email="lead@example.test", role=UserRole.QA_LEAD, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: lead

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as client:
        response = await asyncio.wait_for(client.post(
            f"/api/v1/release-readiness/{RUN_ID}/override",
            json={"override_recommendation": "GO", "reason": "accepted"},
        ), timeout=5)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"] == "GO"
    assert pool.max_held == 1
