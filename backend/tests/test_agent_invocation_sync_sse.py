"""E1.2 (slice 3): synchronous invocation and invocation progress over SSE.

* mode=sync waits for the run only on a sync-eligible agent: 200 with the result
  when it finished, 202 when it is still going, 503 + Retry-After when the
  process's sync slots are full. Any other agent asked for sync runs async.
  The slot is released however the request ends.
* stream tickets are single-use, short-lived, bound to one invocation, and
  stored only as a hash; redemption fails closed.
* the event stream sends one event per change and stops when the run finishes.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value, all=lambda: [] if self._value is None else [self._value])


def _view(status="in_progress", **overrides):
    base = {
        "id": uuid.uuid4(), "project_id": uuid.uuid4(), "agent_id": "agent.flaky_sentinel.v1",
        "test_run_id": uuid.uuid4(), "pipeline_run_id": uuid.uuid4(), "mode": "sync",
        "status": status, "attempt": 1, "max_attempts": 5, "next_retry_at": None, "error": None,
        "output": {"flaky": []} if status != "in_progress" else None,
        "requires_human_review": False,
        "review": {"state": "not_applicable", "message": "No report", "review_id": None, "reviewed_at": None},
        "created_at": datetime.now(timezone.utc), "links": {"self": "/api/v1/agents/invocations/x"},
    }
    base.update(overrides)
    return base


# -- sync invocation ---------------------------------------------------------------------


@pytest.fixture
def invoke(monkeypatch):
    pytest.importorskip("celery")
    from app.routers import agent_invoke as router
    from app.worker import tasks

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="42")
    db = MagicMock()
    # run lookup, then "latest invocation of this agent on this run" (none)
    db.execute = AsyncMock(side_effect=[_Result(run), _Result(None)])
    db.add = MagicMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock())
    from app.services import agent_config_resolver
    monkeypatch.setattr(router, "resolve_for_project", AsyncMock(
        side_effect=lambda _db, _project, agent, **_kw: agent_config_resolver.resolve(agent, global_ai_config={})
    ))
    monkeypatch.setattr(router, "record_activity", AsyncMock())
    monkeypatch.setattr(router, "ActorRef", SimpleNamespace(from_user=lambda _u: "actor"))
    monkeypatch.setattr(tasks.run_agent_invocation, "apply_async", MagicMock())
    monkeypatch.setattr(router, "SYNC_SLOTS", router._SyncSlots())

    async def _call(agent_id, stage_mode="sync"):
        body = router.AgentInvokeRequest(
            project_id=run.project_id,
            input={"agent_id": agent_id, "payload": {"test_run_id": str(run.id)}},
            mode=stage_mode,
        )
        response = Response()
        response.status_code = 202
        out = await router.invoke_agent(
            agent_id=agent_id, body=body, response=response, db=db, current_user=SimpleNamespace(id=uuid.uuid4()),
        )
        return out, response

    return SimpleNamespace(router=router, db=db, call=_call, run=run)


@pytest.mark.asyncio
async def test_sync_on_an_agent_that_is_not_sync_eligible_runs_async(invoke, monkeypatch):
    wait = AsyncMock()
    monkeypatch.setattr(invoke.router, "_wait_for_terminal", wait)

    out, response = await invoke.call("agent.summary.v1")

    assert response.status_code == 202
    assert invoke.db.add.call_args.args[0].mode == "async"
    wait.assert_not_awaited()
    assert out["status"] == "in_progress"


@pytest.mark.asyncio
async def test_sync_returns_200_with_the_result_when_the_run_finishes_in_time(invoke, monkeypatch):
    monkeypatch.setattr(invoke.router, "_wait_for_terminal", AsyncMock(return_value=_view("passed")))

    out, response = await invoke.call("agent.flaky_sentinel.v1")

    assert response.status_code == 200
    assert out["status"] == "passed" and out["output"] == {"flaky": []}
    assert invoke.db.add.call_args.args[0].mode == "sync"
    assert invoke.router.SYNC_SLOTS.in_flight == 0, "the slot is released"


@pytest.mark.asyncio
async def test_sync_answers_202_when_the_run_is_still_going_after_the_wait(invoke, monkeypatch):
    monkeypatch.setattr(invoke.router, "_wait_for_terminal", AsyncMock(return_value=_view("in_progress")))

    out, response = await invoke.call("agent.flaky_sentinel.v1")

    assert response.status_code == 202 and out["status"] == "in_progress"
    assert invoke.router.SYNC_SLOTS.in_flight == 0


@pytest.mark.asyncio
async def test_a_full_sync_pool_is_503_with_retry_after_and_creates_nothing(invoke, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_INVOKE_SYNC_CONCURRENCY", 1)
    assert invoke.router.SYNC_SLOTS.try_acquire()

    with pytest.raises(HTTPException) as exc:
        await invoke.call("agent.flaky_sentinel.v1")

    assert exc.value.status_code == 503 and exc.value.headers["Retry-After"]
    invoke.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_the_sync_slot_is_released_when_the_wait_raises(invoke, monkeypatch):
    monkeypatch.setattr(invoke.router, "_wait_for_terminal", AsyncMock(side_effect=RuntimeError("db gone")))

    with pytest.raises(RuntimeError):
        await invoke.call("agent.flaky_sentinel.v1")
    assert invoke.router.SYNC_SLOTS.in_flight == 0


@pytest.mark.asyncio
async def test_the_wait_polls_until_the_run_leaves_in_progress(monkeypatch):
    from app.routers import agent_invoke as router

    first = _view("in_progress")
    unchanged = dict(first)
    passed = {**first, "status": "passed", "output": {"flaky": []}}
    views = iter([first, unchanged, passed])
    view = AsyncMock(side_effect=lambda _db, _inv: next(views))
    monkeypatch.setattr(router, "_invocation_view", view)
    sleep = AsyncMock()

    out = await router._wait_for_terminal(None, object(), wait_seconds=60, sleep=sleep, clock=lambda: 0.0)

    assert out["status"] == "passed" and view.await_count == 3


@pytest.mark.asyncio
async def test_the_wait_gives_up_at_its_deadline(monkeypatch):
    from app.routers import agent_invoke as router

    monkeypatch.setattr(router, "_invocation_view", AsyncMock(return_value=_view("in_progress")))
    ticks = iter([0.0, 10.0])

    out = await router._wait_for_terminal(None, object(), wait_seconds=5, sleep=AsyncMock(), clock=lambda: next(ticks))

    assert out["status"] == "in_progress"


@pytest.mark.asyncio
async def test_a_zero_length_sync_wait_reads_once_without_sleeping(monkeypatch):
    from app.routers import agent_invoke as router

    view = AsyncMock(return_value=_view("in_progress"))
    sleep = AsyncMock()
    monkeypatch.setattr(router, "_invocation_view", view)

    out = await router._wait_for_terminal(
        None, object(), wait_seconds=0, sleep=sleep, clock=lambda: 10.0,
    )

    assert out["status"] == "in_progress"
    view.assert_awaited_once()
    sleep.assert_not_awaited()


# -- stream tickets --------------------------------------------------------------------------


class _Redis:
    def __init__(self):
        self.store: dict[str, tuple[str, int]] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = (value, ex)
        return True

    async def getdel(self, key):
        value = self.store.pop(key, None)
        return value[0] if value else None


@pytest.fixture
def redis(monkeypatch):
    from app.services import invocation_stream

    fake = _Redis()
    monkeypatch.setattr(invocation_stream, "get_redis", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_a_ticket_is_stored_only_as_a_hash_with_a_short_lifetime(redis):
    from app.services import invocation_stream

    invocation_id = uuid.uuid4()
    ticket, ttl = await invocation_stream.issue_stream_ticket(invocation_id, "user-1")

    (key, (payload, ex)), = redis.store.items()
    assert ticket not in key and hashlib.sha256(ticket.encode()).hexdigest() in key
    assert ex == ttl == 60
    assert json.loads(payload)["invocation_id"] == str(invocation_id)


@pytest.mark.asyncio
async def test_ticket_issue_regenerates_after_a_token_collision(redis, monkeypatch):
    from app.services import invocation_stream

    collision = "c" * 43
    replacement = "r" * 43
    redis.store[invocation_stream._key(collision)] = ("occupied", 60)
    tokens = iter((collision, replacement))
    monkeypatch.setattr(invocation_stream.secrets, "token_urlsafe", lambda _size: next(tokens))

    ticket, _ = await invocation_stream.issue_stream_ticket(uuid.uuid4(), "user-1")

    assert ticket == replacement
    assert invocation_stream._key(replacement) in redis.store


@pytest.mark.asyncio
async def test_a_ticket_opens_its_own_invocation_exactly_once(redis):
    from app.services import invocation_stream

    invocation_id = uuid.uuid4()
    ticket, _ = await invocation_stream.issue_stream_ticket(invocation_id, "user-1")

    assert await invocation_stream.redeem_stream_ticket(ticket, invocation_id) is True
    assert await invocation_stream.redeem_stream_ticket(ticket, invocation_id) is False


@pytest.mark.asyncio
async def test_a_ticket_does_not_open_another_invocation(redis):
    from app.services import invocation_stream

    ticket, _ = await invocation_stream.issue_stream_ticket(uuid.uuid4(), "user-1")
    assert await invocation_stream.redeem_stream_ticket(ticket, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_redemption_fails_closed_when_the_ticket_store_is_down(monkeypatch):
    from app.services import invocation_stream

    broken = SimpleNamespace(getdel=AsyncMock(side_effect=ConnectionError("redis down")))
    monkeypatch.setattr(invocation_stream, "get_redis", lambda: broken)
    assert await invocation_stream.redeem_stream_ticket("t" * 32, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_the_stream_guard_refuses_a_bad_ticket_with_401(redis):
    from app.routers.agent_invoke import require_invocation_stream_ticket

    check = require_invocation_stream_ticket()
    request = SimpleNamespace(path_params={"invocation_id": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as exc:
        await check(request=request, ticket="not-a-real-ticket-at-all")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_the_stream_guard_admits_a_live_ticket_for_its_invocation(redis):
    from app.routers.agent_invoke import require_invocation_stream_ticket
    from app.services import invocation_stream

    invocation_id = uuid.uuid4()
    ticket, _ = await invocation_stream.issue_stream_ticket(invocation_id, "user-1")
    request = SimpleNamespace(path_params={"invocation_id": str(invocation_id)})
    assert await require_invocation_stream_ticket()(request=request, ticket=ticket) == invocation_id


@pytest.mark.asyncio
async def test_issuing_a_ticket_answers_503_when_the_store_is_down(monkeypatch):
    from app.routers import agent_invoke as router

    monkeypatch.setattr(router, "_load_invocation_or_404", AsyncMock())
    monkeypatch.setattr(router.invocation_stream, "issue_stream_ticket", AsyncMock(side_effect=ConnectionError()))
    with pytest.raises(HTTPException) as exc:
        await router.issue_invocation_stream_ticket(
            invocation_id=uuid.uuid4(), db=None, current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert exc.value.status_code == 503


# -- event stream -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stream_sends_one_event_per_change_and_stops_when_the_run_finishes(monkeypatch):
    from app.routers import agent_invoke as router

    invocation = SimpleNamespace(id=uuid.uuid4())

    class _Session:
        async def execute(self, _stmt):
            return _Result(invocation)

    @asynccontextmanager
    async def _factory():
        yield _Session()

    first = _view("in_progress")
    unchanged = dict(first)
    passed = {**first, "status": "passed", "output": {"flaky": []}}
    views = iter([first, unchanged, passed])
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr(router, "_invocation_view", AsyncMock(side_effect=lambda _db, _inv: next(views)))
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    frames = [
        frame async for frame in router.invocation_event_stream(
            invocation.id, request, sleep=AsyncMock(), clock=lambda: 0.0,
        )
    ]

    events = [f for f in frames if f.startswith("event: invocation")]
    assert len(events) == 2, "an unchanged read is not re-sent"
    assert json.loads(events[-1].split("data: ", 1)[1])["status"] == "passed"


@pytest.mark.asyncio
async def test_the_stream_emits_when_an_observable_error_changes(monkeypatch):
    from app.routers import agent_invoke as router

    invocation = SimpleNamespace(id=uuid.uuid4())

    class _Session:
        async def execute(self, _stmt):
            return _Result(invocation)

    @asynccontextmanager
    async def _factory():
        yield _Session()

    views = iter([
        _view("in_progress", error=None),
        _view("in_progress", error="retry scheduled"),
        _view("failed", error="model unavailable"),
    ])
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr(router, "_invocation_view", AsyncMock(side_effect=lambda _db, _inv: next(views)))
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    frames = [
        frame async for frame in router.invocation_event_stream(
            invocation.id, request, sleep=AsyncMock(), clock=lambda: 0.0,
        )
    ]
    events = [frame for frame in frames if frame.startswith("event: invocation")]
    assert len(events) == 3
    assert json.loads(events[1].split("data: ", 1)[1])["error"] == "retry scheduled"


@pytest.mark.asyncio
async def test_the_stream_stops_when_the_client_disconnects(monkeypatch):
    from app.routers import agent_invoke as router

    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    frames = [f async for f in router.invocation_event_stream(uuid.uuid4(), request, sleep=AsyncMock())]
    assert frames == []


def test_the_events_route_is_guarded_by_the_ticket_and_the_ticket_route_by_access():
    import inspect

    from app.routers import agent_invoke as router

    events = inspect.signature(router.stream_invocation_events).parameters["_"].default
    assert "require_invocation_stream_ticket" in events.dependency.__qualname__
    ticket = inspect.signature(router.issue_invocation_stream_ticket).parameters["current_user"].default
    assert "require_invocation_access" in ticket.dependency.__qualname__


def test_the_eventsource_route_is_not_hidden_behind_the_global_header_auth():
    """The ticket is the stream credential; EventSource cannot add headers."""
    from fastapi.routing import APIRoute

    from app.bootstrap import PROTECTED_ROUTERS, PUBLIC_ROUTERS
    from app.routers import agent_invoke as router

    assert router.stream_router in PUBLIC_ROUTERS
    assert router.stream_router not in PROTECTED_ROUTERS
    routes = [route for route in router.stream_router.routes if isinstance(route, APIRoute)]
    assert [(route.path, route.methods) for route in routes] == [
        ("/api/v1/agents/invocations/{invocation_id}/events", {"GET"}),
    ]
    dependency_names = {
        getattr(dependency.call, "__qualname__", "")
        for dependency in routes[0].dependant.dependencies
    }
    assert any("require_invocation_stream_ticket" in name for name in dependency_names)
    assert not any("get_current_user_or_api_key" in name for name in dependency_names)
