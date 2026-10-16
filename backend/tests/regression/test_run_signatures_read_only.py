"""``GET /runs/failure-signatures`` reads on the request's one connection, in two statements (W07 FAIL-03).

Same harness as ``test_run_ai_verdicts_read_only.py``: a pool of ONE connection
with a 1 s checkout, ``AsyncSessionLocal`` / ``BackgroundTasks.add_task`` /
every intelligence compute and snapshot write patched to RAISE. The batch is
two statements on the request session (the visible-runs read, then the one
signature statement); one when none of the ids is readable.
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
from app.services import intelligence_snapshot_service, run_intelligence_service, run_signature_service

PROJECT = uuid.uuid4()


class _Pool:
    def __init__(self) -> None:
        self._slot = asyncio.Semaphore(1)
        self.max_held = 0
        self.held = 0

    async def checkout(self) -> None:
        await asyncio.wait_for(self._slot.acquire(), timeout=1.0)
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    def checkin(self) -> None:
        self.held -= 1
        self._slot.release()


class _Session:
    def __init__(self, pool: _Pool, readable: list) -> None:
        self._pool = pool
        self._connected = False
        self.readable = readable
        self.statements: list = []
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, statement, params=None, *args, **kwargs):
        if not self._connected:
            await self._pool.checkout()
            self._connected = True
        self.statements.append(statement)
        if isinstance(statement, TextClause) and "WITH ids AS" in statement.text:
            rows = [
                {"run_id": uuid.UUID(i), "failing": 3, "read_rows": 3, "sig": "boom #", "n": 3, "test_count": 3,
                 "label": "Boom 1", "tests": ["test_a", "test_b", "test_c"], "rn": 1}
                for i in params["ids"]
            ]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.readable)))

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
    state = {"readable": []}
    counted: list[int] = []
    for module, name in (
        (run_intelligence_service, "get_run_intelligence"),
        (intelligence_snapshot_service, "get_or_compute"),
        (intelligence_snapshot_service, "save_snapshot"),
    ):
        monkeypatch.setattr(module, name, _forbidden(f"{module.__name__}.{name}"), raising=False)
    monkeypatch.setattr(pg, "AsyncSessionLocal", _forbidden("AsyncSessionLocal"), raising=False)
    monkeypatch.setattr(BackgroundTasks, "add_task", _forbidden("BackgroundTasks.add_task"))

    async def membership(db, user):
        await db.execute(TextClause("SELECT 1"))
        return {PROJECT}

    monkeypatch.setattr(runs_router, "get_accessible_project_ids", membership)
    real_batch = run_signature_service.batch_run_signatures

    async def counting_batch(db, ids, *, accessible):
        before = len(db.statements)
        out = await real_batch(db, ids, accessible=accessible)
        counted.append(len(db.statements) - before)
        return out

    monkeypatch.setattr(runs_router.run_signature_service, "batch_run_signatures", counting_batch)
    app = FastAPI()
    app.include_router(runs_router.router)

    async def _get_db():
        session = _Session(pool, state["readable"])
        try:
            yield session
        finally:
            await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(), username="qa", email="qa@example.test", role=UserRole.QA_ENGINEER, is_active=True,
    )
    return SimpleNamespace(app=app, pool=pool, state=state, counted=counted)


async def _call(harness, ids):
    async with AsyncClient(transport=ASGITransport(app=harness.app, raise_app_exceptions=True),
                           base_url="http://t") as client:
        return await asyncio.wait_for(
            client.get("/api/v1/runs/failure-signatures", params=[("run_id", str(i)) for i in ids]), timeout=5,
        )


async def test_fifty_runs_on_one_connection_in_two_statements(harness):
    ids = [uuid.uuid4() for _ in range(50)]
    harness.state["readable"] = ids
    response = await _call(harness, ids)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["run_id"] for item in items] == [str(i) for i in ids]
    assert items[0]["dominant"] == "boom #" and items[0]["signatures"][0]["label"] == "Boom 1"
    assert harness.pool.max_held == 1
    assert harness.counted == [2]


async def test_no_readable_run_takes_one_statement(harness):
    ids = [uuid.uuid4() for _ in range(3)]
    response = await _call(harness, ids)
    assert response.status_code == 200
    assert response.json()["unavailable"] == [str(i) for i in ids]
    assert harness.counted == [1]
