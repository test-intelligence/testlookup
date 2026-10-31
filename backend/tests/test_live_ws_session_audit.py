"""HYG-06 (a) — ``routers/live.py::_record_ws_session`` end to end: AT MOST ONE ``ws_session`` row per session.

Before W25a a live connection wrote ``ws_connect`` + ``ws_disconnect`` (two ``access_audit_logs`` rows per
connection). Now (contract §2.2 as revised by the orchestrator — append-only, ``backend.audit-write-discipline``
intact): a session's one row is INSERTed at the first disconnect where its accumulated connected time reaches
5 s; a continuation (a connection starting within 60 s of the last disconnect) of an audited session never
writes and is counted ``merged``; a session that never reaches 5 s writes nothing (``short``). No row is ever
updated. Without Redis every connection of 5 s or more is its own session; a DB error is ``audit_failed``;
neither raises into the socket.

Driven through a recording Redis and a recording session factory (``live.AsyncSessionLocal``) that REFUSES any
statement but the actor lookup, with the REAL ``log_access_change`` and the REAL
``testlookup_ws_connections_total`` counter read back. Times are built INSIDE each test; structlog is read
through a ``_slog`` recorder.
"""
from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app.core.metrics import ws_connections_total
from app.models.postgres import AccessAuditLog
from app.routers import live
from app.services import ws_audit_policy

WATCHER_NAME = "sentinel-watcher"  # non-hex: must never reach a log line


# ── Recording fakes ──────────────────────────────────────────────────────────────────────────────────────


class _RecordingRedis:
    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, dict, int | None]] = []
        self.broken = broken

    async def get(self, key):
        if self.broken:
            raise ConnectionError("redis is down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis is down")
        self.store[key] = value
        self.sets.append((key, json.loads(value), ex))


class _World:
    """The committed ``access_audit_logs`` rows plus a log of every session and every executed statement."""

    def __init__(self) -> None:
        self.inserts: list[dict] = []
        self.statements: list[object] = []  # anything executed: must stay empty (no UPDATE, ever)
        self.sessions = 0
        self.broken = False

    def factory(self):
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, world: _World) -> None:
        self.world = world
        self._pending: list[object] = []

    async def __aenter__(self):
        self.world.sessions += 1
        return self

    async def __aexit__(self, *exc):
        self._pending.clear()
        return False

    def _fail_if_broken(self) -> None:
        if self.world.broken:
            raise OperationalError("statement", {}, ConnectionError("db is down"))

    def add(self, obj) -> None:
        self._pending.append(obj)

    async def get(self, model, ident):
        self._fail_if_broken()
        return SimpleNamespace(id=ident, username=WATCHER_NAME)

    async def execute(self, stmt):
        self.world.statements.append(stmt)
        raise AssertionError(f"the session audit executed a statement: {stmt}")

    async def flush(self) -> None:
        self._fail_if_broken()

    async def commit(self) -> None:
        self._fail_if_broken()
        for obj in self._pending:
            assert isinstance(obj, AccessAuditLog)
            self.world.inserts.append({
                "action": obj.action, "actor_user_id": obj.actor_user_id, "actor_name": obj.actor_name,
                "project_id": obj.project_id, "after_value": obj.after_value,
            })
        self._pending.clear()


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def debug(self, event_name, **kwargs):
        self.events.append((event_name, kwargs))

    info = warning = error = debug


# ── Fixtures and helpers ─────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def world(monkeypatch) -> _World:
    w = _World()
    monkeypatch.setattr(live, "AsyncSessionLocal", w.factory)
    return w


@pytest.fixture
def redis(monkeypatch) -> _RecordingRedis:
    fake = _RecordingRedis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


@pytest.fixture
def slog(monkeypatch) -> _LogRecorder:
    recorder = _LogRecorder()
    monkeypatch.setattr(live, "_slog", recorder)
    return recorder


def _counts() -> dict[str, float]:
    return {o: ws_connections_total.labels(outcome=o)._value.get() for o in ws_audit_policy.WS_OUTCOMES}


def _delta(before: dict[str, float]) -> dict[str, float]:
    after = _counts()
    return {o: after[o] - before[o] for o in ws_audit_policy.WS_OUTCOMES}


def _ids() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


async def _connect(user_id, project_id, start, end, reason="client_disconnect") -> str:
    return await live._record_ws_session(user_id, project_id, start, end, reason)


def _key(user_id, project_id) -> str:
    return ws_audit_policy.SESSION_KEY.format(user_id=user_id, project_id=project_id)


# ── One session: one INSERT, and continuations that never write ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_four_connections_inside_sixty_seconds_are_one_insert_and_no_update(world, redis, slog):
    user_id, project_id = _ids()
    t0 = time.time() - 300
    before = _counts()

    outcomes = [
        await _connect(user_id, project_id, t0, t0 + 30, reason="token_expired"),
        await _connect(user_id, project_id, t0 + 31, t0 + 32),
        await _connect(user_id, project_id, t0 + 33, t0 + 34),
        await _connect(user_id, project_id, t0 + 35, t0 + 95),
    ]

    assert outcomes == ["audited", "merged", "merged", "merged"]
    assert world.statements == [], "no UPDATE (no statement at all) may run"
    assert world.sessions == 1, "a continuation opens no DB session"
    (row,) = world.inserts
    assert row["action"] == "ws_session"
    assert (row["actor_user_id"], row["actor_name"], row["project_id"]) == (user_id, WATCHER_NAME, project_id)
    assert row["after_value"] == {
        "channel": "live",
        "reason": "token_expired",
        "started_at": live._iso(t0),
        "ended_at": live._iso(t0 + 30),
        "session_started_at": live._iso(t0),
        "connected_s_at_write": 30,
        "reconnect_policy": "reconnects within 60 s are merged into this session",
    }
    assert "reconnects" not in row["after_value"] and "duration_s" not in row["after_value"]
    assert _delta(before) == {"audited": 1, "merged": 3, "short": 0, "audit_failed": 0}

    assert [k for k, _s, _ex in redis.sets] == [_key(user_id, project_id)] * 4
    assert {ex for _k, _s, ex in redis.sets} == {60}
    assert redis.sets[-1][1] == {
        "started_at": t0, "ended_at": t0 + 95, "connected_ms": 30_000 + 1000 + 1000 + 60_000,
        "connections": 4, "audited": True,
    }
    for _event, kwargs in slog.events:
        assert WATCHER_NAME not in json.dumps(kwargs, default=str)


@pytest.mark.asyncio
async def test_three_seconds_then_four_seconds_insert_once_at_the_second_disconnect(world, redis):
    user_id, project_id = _ids()
    t0 = time.time() - 120
    before = _counts()

    assert await _connect(user_id, project_id, t0, t0 + 3) == "short"
    assert world.sessions == 0 and world.inserts == []
    assert await _connect(user_id, project_id, t0 + 4, t0 + 8, reason="bootstrap_timeout") == "audited"

    (row,) = world.inserts
    assert row["after_value"]["started_at"] == live._iso(t0 + 4)
    assert row["after_value"]["ended_at"] == live._iso(t0 + 8)
    assert row["after_value"]["session_started_at"] == live._iso(t0)
    assert row["after_value"]["connected_s_at_write"] == 7  # 3 s + 4 s connected (the session spans 8 s)
    assert row["after_value"]["reason"] == "bootstrap_timeout"
    assert world.statements == []
    assert _delta(before) == {"audited": 1, "merged": 0, "short": 1, "audit_failed": 0}


@pytest.mark.asyncio
async def test_a_lone_three_second_connection_writes_nothing_and_counts_short(world, redis):
    user_id, project_id = _ids()
    t0 = time.time() - 10
    before = _counts()

    assert await _connect(user_id, project_id, t0, t0 + 3) == "short"

    assert world.sessions == 0, "a short connection must not open a DB session at all"
    assert world.inserts == [] and world.statements == []
    assert _delta(before) == {"audited": 0, "merged": 0, "short": 1, "audit_failed": 0}
    assert redis.sets[-1][1] == {
        "started_at": t0, "ended_at": t0 + 3, "connected_ms": 3000, "connections": 1, "audited": False,
    }


@pytest.mark.asyncio
async def test_a_continuation_never_writes_even_while_the_db_is_down(world, redis):
    user_id, project_id = _ids()
    t0 = time.time() - 120
    assert await _connect(user_id, project_id, t0, t0 + 30) == "audited"

    world.broken = True
    before = _counts()
    assert await _connect(user_id, project_id, t0 + 40, t0 + 41) == "merged"
    assert world.sessions == 1 and len(world.inserts) == 1
    assert _delta(before) == {"audited": 0, "merged": 1, "short": 0, "audit_failed": 0}


@pytest.mark.asyncio
async def test_a_connection_after_the_window_starts_a_new_session_with_its_own_row(world, redis):
    user_id, project_id = _ids()
    t0 = time.time() - 300
    assert await _connect(user_id, project_id, t0, t0 + 30) == "audited"
    assert await _connect(user_id, project_id, t0 + 90.001, t0 + 100) == "audited"
    assert len(world.inserts) == 2 and world.statements == []


# ── Degraded paths: never an exception into the socket ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_without_redis_each_long_connection_is_its_own_session_and_nothing_raises(world, monkeypatch, slog):
    down = _RecordingRedis(broken=True)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: down)
    user_id, project_id = _ids()
    t0 = time.time() - 300
    before = _counts()

    outcomes = [
        await _connect(user_id, project_id, t0, t0 + 30),
        await _connect(user_id, project_id, t0 + 31, t0 + 40),
        await _connect(user_id, project_id, t0 + 41, t0 + 43),
        await _connect(user_id, project_id, t0 + 44, t0 + 90),
    ]

    assert outcomes == ["audited", "audited", "short", "audited"]
    assert len(world.inserts) == 3 and world.statements == []
    assert _delta(before) == {"audited": 3, "merged": 0, "short": 1, "audit_failed": 0}
    assert {name for name, _kw in slog.events} == {"ws_session_state_unread", "ws_session_state_unkept"}
    assert all(kw == {"error_type": "ConnectionError"} for _name, kw in slog.events)


@pytest.mark.asyncio
async def test_a_db_error_is_counted_audit_failed_and_the_next_continuation_inserts(world, redis, slog):
    user_id, project_id = _ids()
    t0 = time.time() - 300
    before = _counts()

    world.broken = True
    assert await _connect(user_id, project_id, t0, t0 + 30) == "audit_failed"
    assert world.inserts == []
    assert slog.events == [("ws_session_audit_failed", {"error_type": "OperationalError"})]
    assert redis.sets[-1][1]["audited"] is False, "a failed INSERT leaves the session un-audited"

    world.broken = False
    assert await _connect(user_id, project_id, t0 + 40, t0 + 50) == "audited"
    (row,) = world.inserts
    assert row["after_value"]["connected_s_at_write"] == 40
    assert row["after_value"]["session_started_at"] == live._iso(t0)
    assert _delta(before) == {"audited": 1, "merged": 0, "short": 0, "audit_failed": 1}


@pytest.mark.asyncio
async def test_unreadable_redis_json_is_no_session(world, redis):
    user_id, project_id = _ids()
    redis.store[_key(user_id, project_id)] = "{not json"
    t0 = time.time() - 30
    assert await _connect(user_id, project_id, t0, t0 + 10) == "audited"
    assert len(world.inserts) == 1


@pytest.mark.asyncio
async def test_a_session_of_another_user_or_project_is_not_continued(world, redis):
    """The key is per user AND project: an audited session elsewhere never swallows this connection."""
    user_id, project_id = _ids()
    other_user, other_project = _ids()
    t0 = time.time() - 120
    assert await _connect(other_user, project_id, t0, t0 + 30) == "audited"
    assert await _connect(user_id, other_project, t0, t0 + 30) == "audited"
    assert await _connect(user_id, project_id, t0 + 31, t0 + 40) == "audited"
    assert len(world.inserts) == 3


# ── The handler: nothing at connect, one _record_ws_session in finally ──────────────────────────────────


class _ClosingSocket:
    """Accepted and authenticated; the client is gone before the welcome frame lands."""

    async def send_json(self, payload):
        from starlette.websockets import WebSocketDisconnect

        raise WebSocketDisconnect(code=1001)


def _drive_handler(monkeypatch, *, authenticated: bool):
    user_id = uuid.uuid4()
    calls: list[tuple] = []

    async def _accept(project_id, websocket):
        return True

    async def _auth(websocket, project_id):
        if not authenticated:
            return None
        return live.WsSession(user_id=user_id, user_name=WATCHER_NAME, token_exp=0.0)

    async def _spy(*args):
        calls.append(args)
        return "audited"

    monkeypatch.setattr(live.manager, "accept", _accept)
    monkeypatch.setattr(live, "_authenticate_ws", _auth)
    monkeypatch.setattr(live, "_record_ws_session", _spy)
    return user_id, calls


@pytest.mark.asyncio
async def test_the_handler_writes_nothing_at_connect_and_records_the_connection_once_at_the_end(world, monkeypatch):
    user_id, calls = _drive_handler(monkeypatch, authenticated=True)
    project_id = uuid.uuid4()
    lower = time.time()

    await live.live_updates(_ClosingSocket(), str(project_id))

    assert world.sessions == 0, "no audit session may open at connect"
    (args,) = calls
    assert args[0] == user_id and args[1] == project_id
    assert lower <= args[2] <= args[3] <= time.time()
    assert args[4] == "client_disconnect"
    assert not hasattr(live, "_audit_ws_event")


@pytest.mark.asyncio
async def test_an_unauthenticated_socket_records_nothing(world, monkeypatch):
    _user_id, calls = _drive_handler(monkeypatch, authenticated=False)

    await live.live_updates(_ClosingSocket(), str(uuid.uuid4()))

    assert calls == [] and world.sessions == 0
