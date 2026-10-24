"""W12 SRCH-05 (D-4): a recorded search writes AFTER its response, on its own session.

* ``record=true`` schedules ONE ``BackgroundTasks`` job whose usage row holds
  exactly the ``search_query_events`` columns -- no query text, no user id;
* the row is committed on the writer's OWN short session, after the handler
  returned; a failing insert is counted ``failed`` and raises nothing;
* ``GET /search/global`` observes ``global_search_duration_seconds`` on every
  call and counts ``global_search_zero_results_total`` on an EXACT zero for a
  query with words only;
* through the ASGI app on a ONE-connection pool (the
  ``test_no_second_session_while_the_request_holds_one.py`` harness), both
  search routes with ``record=true`` never hold two connections: on FastAPI
  0.115 ``get_db`` has exited before the background job opens its session.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy import select

import app.db.postgres as pg
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import SearchQueryEvent, TestRun, User, UserRole

TERM = "zq-usage-sentinel checkout"
PROJECT = uuid.uuid4()
EVENT_COLUMNS = {
    "project_id", "q_hash", "q_length", "word_count", "filter_count", "entity_scope", "mode_requested",
    "mode_used", "fallback_reason", "result_count", "counts_exact", "latency_ms", "created_at",
}


def _sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _writes(store: str, outcome: str) -> float:
    return _sample("testlookup_search_after_response_writes_total", {"store": store, "outcome": outcome})


class _WriterSession:
    """The writer's own session: records what it was given and when it committed."""

    instances: list["_WriterSession"] = []

    def __init__(self, *, fail: bool = False) -> None:
        self.added: list = []
        self.committed = False
        self.fail = fail
        _WriterSession.instances.append(self)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        if self.fail:
            raise RuntimeError(f"insert failed for {TERM}")
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
def direct(monkeypatch):
    from app.routers import search as router

    _WriterSession.instances = []
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _WriterSession(), raising=False)
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr(router, "push_recent", AsyncMock(return_value=True))
    result = {"items": [], "total": 0, "counts_are_exact": True}
    global_search = AsyncMock(side_effect=lambda **kw: dict(result))
    monkeypatch.setattr("app.services.global_search_service.global_search", global_search)
    return router, result


async def _global(router, q=TERM, *, record=True, tasks=None, entity_types=None, page=1):
    return await router.global_search_endpoint(
        q=q, project_id=str(PROJECT), entity_types=entity_types, days=None, page=page, size=20,
        db=MagicMock(), current_user=MagicMock(id=uuid.uuid4()), record=record, background_tasks=tasks,
    )


async def test_record_schedules_one_job_whose_row_holds_exactly_the_columns(direct):
    router, _ = direct
    tasks = BackgroundTasks()
    await _global(router, f'{TERM} status:failed', tasks=tasks)
    assert len(tasks.tasks) == 1
    event = tasks.tasks[0].kwargs["event"]
    assert set(event) == EVENT_COLUMNS
    assert not {"q", "query", "text", "user_id"} & set(event)
    assert all(TERM not in str(value) and "checkout" not in str(value) for value in event.values())
    assert event["project_id"] == PROJECT and event["entity_scope"] == "all"
    assert (event["word_count"], event["filter_count"]) == (2, 1)
    assert event["q_length"] == len(f"{TERM} status:failed")
    assert event["mode_requested"] == event["mode_used"] == "keyword" and event["fallback_reason"] is None
    assert event["result_count"] == 0 and event["counts_exact"] is True and event["latency_ms"] >= 0
    # Nothing is written while the handler runs.
    assert _WriterSession.instances == []


async def test_the_commit_happens_on_the_writers_own_session_after_the_handler(direct):
    router, _ = direct
    tasks = BackgroundTasks()
    before = _writes("usage", "written")
    await _global(router, tasks=tasks)
    assert _WriterSession.instances == [], "the row was written before the response"
    await tasks()
    [session] = _WriterSession.instances
    assert session.committed is True
    [row] = session.added
    assert isinstance(row, SearchQueryEvent) and row.project_id == PROJECT
    assert _writes("usage", "written") == before + 1
    assert _writes("recent", "written") >= 1


async def test_a_failing_insert_is_counted_and_raises_nothing(direct, monkeypatch, caplog):
    router, _ = direct
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _WriterSession(fail=True), raising=False)
    monkeypatch.setattr(router, "push_recent", AsyncMock(return_value=False))
    tasks = BackgroundTasks()
    failed_usage, failed_recent = _writes("usage", "failed"), _writes("recent", "failed")
    await _global(router, tasks=tasks)
    await tasks()  # raises nothing
    assert _writes("usage", "failed") == failed_usage + 1
    assert _writes("recent", "failed") == failed_recent + 1
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "search_usage_write_failed error_type=RuntimeError" in logged
    assert "zq-usage-sentinel" not in logged


@pytest.mark.parametrize(
    ("kwargs", "scheduled"),
    [
        ({"record": False}, 0),
        ({"page": 2}, 0),
        ({"q": "   "}, 0),
        ({"tasks": None}, 0),  # a direct call: nothing to schedule on
    ],
)
async def test_nothing_is_scheduled_unless_recorded_on_page_one_with_words(direct, kwargs, scheduled):
    router, _ = direct
    tasks = BackgroundTasks()
    call = {"tasks": tasks, **kwargs}
    await _global(router, **call)
    assert len(tasks.tasks) == scheduled


async def test_the_duration_is_observed_on_every_call_and_an_exact_zero_counted(direct):
    router, result = direct
    duration = "testlookup_global_search_duration_seconds_count"
    zero = "testlookup_global_search_zero_results_total"
    all_before, one_before = _sample(duration, {"scope": "all"}), _sample(duration, {"scope": "one"})
    zero_all, zero_one = _sample(zero, {"scope": "all"}), _sample(zero, {"scope": "one"})

    await _global(router, record=False)                                   # words, exact zero -> counted
    await _global(router, q="", record=False)                             # a browse -> not counted
    result["counts_are_exact"] = False
    await _global(router, record=False)                                   # a lower bound -> not counted
    result.update(total=3, counts_are_exact=True)
    await _global(router, record=False, entity_types="test_case")         # rows found -> not counted
    result.update(total=0)
    await _global(router, record=False, entity_types="defect")            # one type, exact zero -> counted

    assert _sample(duration, {"scope": "all"}) == all_before + 3
    assert _sample(duration, {"scope": "one"}) == one_before + 2
    assert _sample(zero, {"scope": "all"}) == zero_all + 1
    assert _sample(zero, {"scope": "one"}) == zero_one + 1


async def test_the_test_result_route_records_what_answered(monkeypatch):
    from app.routers import search as router
    from app.services import semantic_search

    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr(router, "search_test_cases_query", AsyncMock(return_value=([{"id": 1}], 1, 1)))
    monkeypatch.setattr(semantic_search, "semantic_search", AsyncMock(side_effect=ConnectionError("down")))
    tasks = BackgroundTasks()
    await router.search_test_cases(
        q=TERM, project_id=str(PROJECT), status=None, days=None, search_type="semantic", page=1, size=20,
        db=MagicMock(), current_user=MagicMock(id=uuid.uuid4()), record=True, background_tasks=tasks,
    )
    [task] = tasks.tasks
    event, recent = task.kwargs["event"], task.kwargs["recent"]
    assert set(event) == EVENT_COLUMNS
    assert (event["entity_scope"], event["mode_requested"], event["mode_used"]) == ("tests", "semantic", "keyword")
    assert event["fallback_reason"] == "semantic_unavailable" and event["result_count"] == 1
    assert recent["mode"] == "keyword" and recent["scope"] == "tests" and recent["q"] == TERM


# ── One connection, the request's and then the writer's ─────────────────────


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


class _PoolSession:
    """Takes the pool's connection on its first statement or commit; gives it back on close."""

    commits: list["_PoolSession"] = []

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool
        self._connected = False
        self.added: list = []

    async def _connect(self):
        if not self._connected:
            await self._pool.checkout()
            self._connected = True

    async def execute(self, *args, **kwargs):
        await self._connect()
        return MagicMock()

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        await self._connect()
        _PoolSession.commits.append(self)

    async def close(self):
        if self._connected:
            self._connected = False
            self._pool.checkin()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


@pytest.fixture
def pooled(monkeypatch):
    from app.routers import search as router

    pool = _Pool()
    _PoolSession.commits = []
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _PoolSession(pool), raising=False)
    monkeypatch.setattr(router, "push_recent", AsyncMock(return_value=True))

    async def global_search(*, db, **kwargs):
        await db.execute(select(TestRun.id))  # the request now holds the one connection
        return {"items": [], "total": 1, "counts_are_exact": True}

    async def keyword(db, **kwargs):
        await db.execute(select(TestRun.id))
        return [], 0, 0

    monkeypatch.setattr("app.services.global_search_service.global_search", global_search)
    monkeypatch.setattr(router, "search_test_cases_query", keyword)

    app = FastAPI()
    app.include_router(router.router)

    async def _get_db():
        session = _PoolSession(pool)
        try:
            yield session
        finally:
            await session.close()

    admin = User(id=uuid.uuid4(), username="admin", email="admin@example.test", role=UserRole.ADMIN, is_active=True)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return app, pool


@pytest.mark.parametrize("path", ["/api/v1/search/global", "/api/v1/search"])
async def test_a_recorded_search_never_holds_two_connections(pooled, path):
    app, pool = pooled
    written = _writes("usage", "written")
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as client:
        response = await asyncio.wait_for(client.get(path, params={"q": "x", "record": "true"}), timeout=5)
    assert response.status_code == 200, response.text
    assert pool.max_held == 1
    assert len(_PoolSession.commits) == 1, "the usage row never got a connection"
    assert isinstance(_PoolSession.commits[0].added[0], SearchQueryEvent)
    assert _writes("usage", "written") == written + 1
