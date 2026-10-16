"""``GET /runs/ai-verdicts`` reads stored state only, on the request's one connection (W07 FAIL-04).

The column it replaces asked ``GET /runs/{id}/intelligence`` per run, and a
cache miss there COMPUTES the run's intelligence and writes a snapshot on a
second session. Twenty-five of those at once on a worker's 2 + 1 pool is the
homelab outage of 2026-10-13/14 again. The batch read must never compute and
never open a second session.

The route runs on a pool of ONE connection with a 1 s checkout (the pattern of
``test_no_second_session_while_the_request_holds_one.py``); every compute and
write path is patched to RAISE: ``get_run_intelligence``, ``get_or_compute``,
``save_snapshot``, ``AsyncSessionLocal`` and ``BackgroundTasks.add_task``. And
``batch_verdict_inputs`` issues exactly two statements for 25 runs with
snapshots, one when none has one (the route's membership read is outside it).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql.elements import TextClause

import app.db.postgres as pg
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.routers import runs as runs_router
from app.services import intelligence_snapshot_service, run_intelligence_service, run_verdict_service

PROJECT = uuid.uuid4()


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


def _row(run_id, *, snapshot: bool) -> dict:
    return {
        "run_id": run_id, "project_id": PROJECT, "snapshot": snapshot, "stale": False,
        "generated_at": None, "analysed": snapshot,
        "ss_type": "object" if snapshot else None,
        "di_type": None, "di_truthy": False, "di_v_status": None, "di_rec": None,
        "ver_type": None, "ver_truthy": False, "ver_status": None,
        "att_type": None, "att_truthy": False, "att_status": None, "att_v_status": None,
        "rd_type": "object" if snapshot else None, "rd_truthy": snapshot,
        "rd_rec": '"GO"' if snapshot else None,
        "ia_truthy": snapshot, "ta_type": None, "ta_length": None,
    }


class _Session:
    """Takes the pool's connection on its first query and gives it back on close; counts statements."""

    def __init__(self, pool: _Pool, *, with_snapshots: bool) -> None:
        self._pool = pool
        self._connected = False
        self.with_snapshots = with_snapshots
        self.statements: list = []
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, statement, params=None, *args, **kwargs):
        if not self._connected:
            await self._pool.checkout()
            self._connected = True
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            ids = [uuid.UUID(i) for i in (params or {}).get("ids", [])]
            rows = [_row(i, snapshot=self.with_snapshots) for i in ids]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        return SimpleNamespace(all=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None))

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        if self._connected:
            self._connected = False
            self._pool.checkin()


def _forbidden(name):
    def _raise(*args, **kwargs):
        raise AssertionError(f"the batch read reached {name}")
    return _raise


@pytest.fixture
def harness(monkeypatch):
    pool = _Pool()
    sessions: list[_Session] = []
    state = {"with_snapshots": True}
    for module, name in (
        (run_intelligence_service, "get_run_intelligence"),
        (intelligence_snapshot_service, "get_run_intelligence"),
        (intelligence_snapshot_service, "get_or_compute"),
        (intelligence_snapshot_service, "save_snapshot"),
        (intelligence_snapshot_service, "get_cached_snapshot"),
    ):
        monkeypatch.setattr(module, name, _forbidden(f"{module.__name__}.{name}"), raising=False)
    monkeypatch.setattr(pg, "AsyncSessionLocal", _forbidden("AsyncSessionLocal"), raising=False)
    monkeypatch.setattr(BackgroundTasks, "add_task", _forbidden("BackgroundTasks.add_task"))

    async def membership(db, user):
        await db.execute(TextClause("SELECT 1"))  # the route's own read, outside the batch
        return {PROJECT}

    monkeypatch.setattr(runs_router, "get_accessible_project_ids", membership)
    counted: list[int] = []
    real_batch = run_verdict_service.batch_verdict_inputs

    async def counting_batch(db, ids, *, accessible):
        before = len(db.statements)
        out = await real_batch(db, ids, accessible=accessible)
        counted.append(len(db.statements) - before)
        return out

    monkeypatch.setattr(runs_router.run_verdict_service, "batch_verdict_inputs", counting_batch)

    app = FastAPI()
    app.include_router(runs_router.router)

    async def _get_db():
        session = _Session(pool, with_snapshots=state["with_snapshots"])
        sessions.append(session)
        try:
            yield session
        finally:
            await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(), username="qa", email="qa@example.test", role=UserRole.QA_ENGINEER, is_active=True,
    )
    return SimpleNamespace(app=app, pool=pool, sessions=sessions, state=state, counted=counted)


async def _call(harness, count: int):
    ids = [uuid.uuid4() for _ in range(count)]
    async with AsyncClient(transport=ASGITransport(app=harness.app, raise_app_exceptions=True),
                           base_url="http://t") as client:
        response = await asyncio.wait_for(
            client.get("/api/v1/runs/ai-verdicts", params=[("run_id", str(i)) for i in ids]), timeout=5,
        )
    return ids, response


async def test_twenty_five_runs_with_snapshots_on_one_connection_in_two_statements(harness):
    ids, response = await _call(harness, 25)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["run_id"] for item in body["items"]] == [str(i) for i in ids]
    assert body["items"][0]["verdict_inputs"]["release_decision"] == {"recommendation": "GO"}
    assert body["items"][0]["review"] == {"state": "pending_review"}
    assert harness.pool.max_held == 1, "the read never needed a second connection"
    assert len(harness.sessions) == 1
    assert harness.counted == [2]


async def test_runs_without_snapshots_take_one_statement(harness):
    harness.state["with_snapshots"] = False
    _, response = await _call(harness, 25)
    assert response.status_code == 200
    assert all(item["snapshot"] is False and item["verdict_inputs"] is None for item in response.json()["items"])
    assert harness.pool.max_held == 1
    assert harness.counted == [1]
