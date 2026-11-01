"""W25e HYG-06 (E-2) T-B2 — ``routers/live.py`` paces every ``/ws/live`` socket (contract §1.2, §2.2 W-2 … W-5).

A real event loop and real timers; only the sockets are faked (they record ``send_text`` with the time it
landed). ``live_frames.FRAME_INTERVAL_MS`` is patched to 50 ms BEFORE a socket registers — the pacer reads it at
construction — so a test waits tens of milliseconds, not seconds. The first broadcast must still go out INLINE
(the guard ``test_timed_out_websocket_is_closed_so_the_client_can_reconnect`` relies on it); what falls inside an
interval is folded and flushed by the socket's timer, which ``disconnect`` cancels.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.routers import live
from app.routers.live import ConnectionManager, WsSession, _authenticate_ws
from app.streams import live_frames
from app.streams.live_frames import LIVE_BATCH_FRAME, LIVE_PROTOCOL_BATCH, LIVE_PROTOCOL_LEGACY, LIVE_RECONCILE_FRAME

INTERVAL_MS = 50
INTERVAL = INTERVAL_MS / 1000
# The pacer reads time.monotonic (a 15.6 ms tick on Windows); the fake socket stamps perf_counter.
TOLERANCE = 0.02
PROJECT = "proj-zz-pacing"
LEGACY_TYPES = {"live_run_started", "live_test_result", "live_warning", "live_run_complete", LIVE_RECONCILE_FRAME}


class _Socket:
    """Records each frame it received (perf_counter, parsed, raw); can turn slow after N sends."""

    def __init__(self, *, slow_after: int | None = None) -> None:
        self.sent: list[tuple[float, dict, str]] = []
        self.closed: tuple[int, str] | None = None
        self.cancelled = 0
        self.slow_after = slow_after

    async def send_text(self, payload: str) -> None:
        if self.slow_after is not None and len(self.sent) >= self.slow_after:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        self.sent.append((time.perf_counter(), json.loads(payload), payload))

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)

    def frames(self) -> list[dict]:
        return [frame for _t, frame, _raw in self.sent]


class _Feed:
    def __init__(self) -> None:
        self.n = 0

    def result(self, run: str = "run-a") -> dict:
        self.n += 1
        return {
            "type": "live_test_result",
            "run_id": run,
            "project_id": PROJECT,
            "passed": self.n,
            "failed": 0,
            "total": 100,
            "pass_rate": 100.0,
            "status": "running",
            "last_test": f"zz_case_{self.n}",
            "last_status": "PASSED",
            "sequence_id": f"{1_700_000_000_000 + self.n}-0",
        }


async def _until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


@pytest.fixture
async def manager(monkeypatch):
    monkeypatch.setattr(live_frames, "FRAME_INTERVAL_MS", INTERVAL_MS)
    mgr = ConnectionManager()
    yield mgr
    for ws, paced in list(mgr._paced.items()):  # cancel every timer the test left armed
        mgr.disconnect(paced.project_id, ws)


# ── W-2: inline first frame, then at most one frame per interval ───────────────────────────────────────


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
async def test_the_first_broadcast_is_sent_inline_as_todays_exact_json(manager, protocol):
    sock = _Socket()
    manager.register(PROJECT, sock, protocol=protocol)
    event = {"type": "live_run_started", "run_id": "run-a", "build_number": "b-zz", "sequence_id": "5-0"}

    await manager.broadcast(PROJECT, event)

    assert len(sock.sent) == 1, "sent inside broadcast, not by a timer"
    assert sock.sent[0][2] == json.dumps(event), "byte-identical to the pre-W25e frame"


async def test_40_broadcasts_inside_one_interval_give_exactly_two_sends(manager):
    batch_sock, legacy_sock = _Socket(), _Socket()
    manager.register(PROJECT, batch_sock, protocol=LIVE_PROTOCOL_BATCH)
    manager.register(PROJECT, legacy_sock)  # the two-argument form: protocol 1
    feed = _Feed()
    events = [feed.result() for _ in range(40)]

    started = time.perf_counter()
    for event in events:
        await manager.broadcast(PROJECT, event)
    assert time.perf_counter() - started < INTERVAL, "the 40 broadcasts must fall inside one interval"
    assert len(batch_sock.sent) == 1 and len(legacy_sock.sent) == 1

    assert await _until(lambda: len(batch_sock.sent) == 2 and len(legacy_sock.sent) == 2)
    await asyncio.sleep(4 * INTERVAL)

    assert len(batch_sock.sent) == 2, "the inline frame + ONE live_batch"
    first, batch = batch_sock.frames()
    assert first == events[0]
    assert batch["type"] == LIVE_BATCH_FRAME and batch["counts"]["events"] == 39
    (entry,) = batch["events"]
    assert entry["coalesced"] == 39 and len(entry["results"]) == 39
    assert batch["sequence_id"] == events[-1]["sequence_id"]
    assert batch_sock.sent[1][0] - batch_sock.sent[0][0] >= INTERVAL - TOLERANCE

    assert len(legacy_sock.sent) == 2
    legacy_first, legacy_folded = legacy_sock.frames()
    assert legacy_first == events[0]
    assert {f["type"] for f in legacy_sock.frames()} <= LEGACY_TYPES, "protocol 1 never sees live_batch"
    assert legacy_folded == {**events[-1], "coalesced": 39}
    assert legacy_sock.sent[1][0] - legacy_sock.sent[0][0] >= INTERVAL - TOLERANCE


async def test_a_protocol_1_socket_receives_at_most_one_legacy_frame_per_interval(manager):
    sock = _Socket()
    manager.register(PROJECT, sock)
    feed = _Feed()
    for step in range(30):  # ~15 events per interval across 3 runs, for ~0.3 s
        for run in ("run-a", "run-b", "run-c"):
            await manager.broadcast(PROJECT, feed.result(run))
        await asyncio.sleep(INTERVAL / 5)
    await _until(lambda: manager._paced[sock].pacer.due_in() is None)

    times = [t for t, _f, _r in sock.sent]
    assert len(times) >= 4
    assert all(b - a >= INTERVAL - TOLERANCE for a, b in zip(times, times[1:]))
    assert {f["type"] for f in sock.frames()} <= LEGACY_TYPES


# ── disconnect cancels; a dead socket on the flush path is closed 1013 ─────────────────────────────────


async def test_disconnect_cancels_the_pending_flush(manager):
    sock = _Socket()
    manager.register(PROJECT, sock, protocol=LIVE_PROTOCOL_BATCH)
    feed = _Feed()
    await manager.broadcast(PROJECT, feed.result())
    await manager.broadcast(PROJECT, feed.result())  # inside the interval: folded, timer armed
    handle = manager._paced[sock].flush_handle
    assert handle is not None

    manager.disconnect(PROJECT, sock)

    assert handle.cancelled()
    assert sock not in manager._paced
    await asyncio.sleep(4 * INTERVAL)
    assert len(sock.sent) == 1, "nothing is sent after disconnect"


async def test_disconnect_cancels_a_flush_send_in_flight(manager):
    sock = _Socket(slow_after=1)  # the inline frame lands; the flush's send hangs
    manager.register(PROJECT, sock, protocol=LIVE_PROTOCOL_BATCH)
    feed = _Feed()
    await manager.broadcast(PROJECT, feed.result())
    await manager.broadcast(PROJECT, feed.result())
    paced = manager._paced[sock]
    assert await _until(lambda: paced.sending and paced.flush_task is not None)
    task = paced.flush_task

    manager.disconnect(PROJECT, sock)

    assert await _until(task.done, timeout=1.0)
    assert task.cancelled() and sock.cancelled == 1
    assert len(sock.sent) == 1


async def test_a_socket_that_times_out_on_a_flush_is_closed_1013_and_leaves_the_channel(manager, monkeypatch):
    monkeypatch.setattr(settings, "WS_BROADCAST_TIMEOUT", 0.05)
    slow, healthy = _Socket(slow_after=1), _Socket()
    manager.register(PROJECT, slow, protocol=LIVE_PROTOCOL_BATCH)
    manager.register(PROJECT, healthy, protocol=LIVE_PROTOCOL_BATCH)
    feed = _Feed()
    await manager.broadcast(PROJECT, feed.result())  # inline: both land
    await manager.broadcast(PROJECT, feed.result())  # folded: the slow socket's FLUSH times out
    assert slow.closed is None, "the inline path was healthy; the failure is on the flush path"

    assert await _until(lambda: slow.closed is not None and slow not in manager._paced)
    assert slow.closed[0] == 1013
    assert slow not in manager._channels.get(PROJECT, set()) and slow not in manager._paced
    assert healthy in manager._channels[PROJECT] and manager.active_connections == 1
    assert await _until(lambda: len(healthy.sent) == 2)


# ── W-3: the handshake reads the protocol leniently ────────────────────────────────────────────────────


class _HandshakeSocket:
    def __init__(self, frame: str) -> None:
        self.frame = frame
        self.closed: list[tuple[int, str]] = []
        self.receive_calls = 0

    async def receive_text(self) -> str:
        self.receive_calls += 1
        return self.frame

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


_ABSENT = object()


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(2, LIVE_PROTOCOL_BATCH), (3, LIVE_PROTOCOL_BATCH), (_ABSENT, LIVE_PROTOCOL_LEGACY), ("2", LIVE_PROTOCOL_LEGACY),
     (True, LIVE_PROTOCOL_LEGACY), (1, LIVE_PROTOCOL_LEGACY), (2.0, LIVE_PROTOCOL_LEGACY), (None, LIVE_PROTOCOL_LEGACY)],
)
async def test_the_handshake_reads_the_protocol_leniently_and_never_closes_for_it(monkeypatch, requested, expected):
    user = type("U", (), {"id": uuid.uuid4(), "username": "zz-watcher"})()

    async def _ok(token, project_uuid):
        return user, 1_900_000_000.0, None

    monkeypatch.setattr(live, "_validate_token_and_membership", _ok)
    frame = {"type": "auth", "token": "a.jwt.token"}
    if requested is not _ABSENT:
        frame["protocol"] = requested
    ws = _HandshakeSocket(json.dumps(frame))

    session = await _authenticate_ws(ws, str(uuid.uuid4()))

    assert session is not None and session.protocol == expected
    assert ws.closed == [] and ws.receive_calls == 1


# ── W-4: the connected frame and the bootstrap replay ──────────────────────────────────────────────────


class _EndpointSocket:
    """Records ``send_json`` (control) and ``send_text`` (paced) frames; the client leaves at its first read."""

    def __init__(self, *, leave_at_first_json: bool = False) -> None:
        self.json_frames: list[dict] = []
        self.text_frames: list[dict] = []
        self.leave_at_first_json = leave_at_first_json

    async def send_json(self, payload: dict) -> None:
        self.json_frames.append(payload)
        if self.leave_at_first_json:
            raise WebSocketDisconnect(code=1001)

    async def send_text(self, payload: str) -> None:
        self.text_frames.append(json.loads(payload))

    async def receive_text(self) -> str:
        raise WebSocketDisconnect(code=1001)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


def _drive_endpoint(monkeypatch, session: WsSession) -> list[tuple]:
    recorded: list[tuple] = []

    async def _accept(project_id, websocket):
        return True

    async def _auth(websocket, project_id):
        return session

    async def _record(*args):
        recorded.append(args)
        return "short"

    monkeypatch.setattr(live.manager, "accept", _accept)
    monkeypatch.setattr(live, "_authenticate_ws", _auth)
    monkeypatch.setattr(live, "_record_ws_session", _record)
    return recorded


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
async def test_the_connected_frame_carries_the_protocol_and_the_frame_interval(monkeypatch, protocol):
    monkeypatch.setattr(live_frames, "FRAME_INTERVAL_MS", INTERVAL_MS)
    session = WsSession(user_id=uuid.uuid4(), user_name="zz-watcher", token_exp=0.0, protocol=protocol)
    recorded = _drive_endpoint(monkeypatch, session)
    ws = _EndpointSocket(leave_at_first_json=True)

    await live.live_updates(ws, str(uuid.uuid4()))

    (connected,) = ws.json_frames
    assert connected["type"] == "connected"
    assert connected["protocol"] == protocol and connected["frame_interval_ms"] == INTERVAL_MS
    assert len(recorded) == 1


async def test_the_bootstrap_replay_is_registered_with_the_protocol_and_paced(monkeypatch):
    monkeypatch.setattr(live_frames, "FRAME_INTERVAL_MS", INTERVAL_MS)
    project_id = str(uuid.uuid4())
    replay = [
        {"type": "live_run_started", "run_id": "run-a", "build_number": "b-1", "sequence_id": "2-0"},
        {"type": "live_test_result", "run_id": "run-a", "last_test": "zz_t", "last_status": "PASSED",
         "sequence_id": "3-0"},
        {"type": "live_run_complete", "run_id": "run-a", "sequence_id": "4-0"},
    ]
    monkeypatch.setattr("app.streams.live_fanout.latest_live_sequence", AsyncMock(return_value="4-0"))
    monkeypatch.setattr(
        "app.streams.live_fanout.replay_live_notifications", AsyncMock(return_value=(replay, False))
    )
    monkeypatch.setattr(
        "app.streams.live_run_state.RedisLiveRunState.get_all_active", AsyncMock(return_value=[])
    )
    registered: list[int] = []
    real_register = live.manager.register

    def _register(pid, websocket, protocol=LIVE_PROTOCOL_LEGACY):
        registered.append(protocol)
        real_register(pid, websocket, protocol=protocol)

    monkeypatch.setattr(live.manager, "register", _register)
    session = WsSession(
        user_id=uuid.uuid4(), user_name="zz-watcher", token_exp=0.0, last_event_id="1-0",
        protocol=LIVE_PROTOCOL_BATCH,
    )
    _drive_endpoint(monkeypatch, session)
    ws = _EndpointSocket()

    await live.live_updates(ws, project_id)

    assert registered == [LIVE_PROTOCOL_BATCH]
    assert [f["type"] for f in ws.json_frames] == ["connected"], "the replay no longer goes out as raw send_json frames"
    (batch,) = ws.text_frames
    assert batch["type"] == LIVE_BATCH_FRAME and batch["sequence_id"] == "4-0"
    assert [e["type"] for e in batch["events"]] == ["live_run_started", "live_test_result", "live_run_complete"]
    assert ws not in live.manager._paced and project_id not in live.manager._channels


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [(LIVE_PROTOCOL_BATCH, LIVE_BATCH_FRAME), (LIVE_PROTOCOL_LEGACY, LIVE_RECONCILE_FRAME)],
)
async def test_send_replay_of_30_events(manager, protocol, expected):
    sock = _Socket()
    manager.register(PROJECT, sock, protocol=protocol)
    feed = _Feed()
    events = [feed.result(f"run-{i}") for i in range(30)]

    await manager.send_replay(PROJECT, sock, events)

    assert len(sock.sent) == 1, "sent inline: the first frame after an idle interval"
    await asyncio.sleep(4 * INTERVAL)
    (frame,) = sock.frames()
    assert frame["type"] == expected
    if protocol == LIVE_PROTOCOL_BATCH:
        assert frame["counts"]["events"] == 30 and len(frame["events"]) == 30
    else:
        assert frame["dropped"] == 30 and frame["sequence_id"] == events[-1]["sequence_id"]


# ── W-5: /ws/events run_complete publishes the staged persist now ───────────────────────────────────────


@pytest.mark.parametrize("completes", [True, False])
async def test_ws_events_run_complete_publishes_the_staged_persist_after_after_commit(monkeypatch, completes):
    from app.db.mongo import Collections
    from app.services.ws_event_ingest import WsIngestOutcome

    project = str(uuid.uuid4())
    order: list[tuple[str, object]] = []
    publish_calls: list[tuple] = []

    async def _admit(_db, *, project_id, api_key_name, run_id, event, api_key=None):
        return WsIngestOutcome("sess-zz-1", cache_key="k", completes=completes, staged=True)

    async def _after_commit(outcome):
        order.append(("after_commit", outcome.session_id))

    async def _publish(db, *, run_id, **kwargs):
        order.append(("publish_now", run_id))
        publish_calls.append((db, run_id, kwargs))
        return "published"

    class _Db:
        async def commit(self):
            order.append(("commit", None))

    class _Coll:
        async def insert_one(self, _doc):
            return None

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.ws_event_ingest.ingest_one", _admit)
    monkeypatch.setattr("app.services.ws_event_ingest.after_commit", _after_commit)
    monkeypatch.setattr("app.worker.downstream_dispatch.publish_staged_now", _publish)
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    monkeypatch.setattr("app.services.ingestion_backpressure.enforce_redis_memory_backpressure", _noop)
    monkeypatch.setattr("app.services.ingestion_rate_limit.enforce_live_event_rate_limit", _noop)
    monkeypatch.setattr("app.services.live_event_authz.cached_streaming_project", AsyncMock(return_value=project))
    monkeypatch.setattr("app.services.live_event_authz.resolve_run_project", AsyncMock(return_value=project))
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: {Collections.LIVE_EXECUTION_EVENTS: _Coll()})
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", True)
    db = _Db()

    response = await live.ingest_live_event(
        run_id="run-zz-ws",
        event={"type": "run_complete" if completes else "test_result", "test_name": "zz_t", "status": "PASSED"},
        x_api_key="qai_zz_sentinel_key",
        x_webhook_secret=None,
        db=db,
    )

    assert response["session_id"] == "sess-zz-1"
    if completes:
        assert order == [("commit", None), ("after_commit", "sess-zz-1"), ("publish_now", "sess-zz-1")]
        (call,) = publish_calls
        assert call[0] is db and call[1] == "sess-zz-1" and call[2] == {}
    else:
        assert order == [("commit", None), ("after_commit", "sess-zz-1")]
        assert publish_calls == []
