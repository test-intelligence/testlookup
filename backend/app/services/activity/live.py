"""The activity live tail: publish a ledger row to Redis AFTER its commit (W21d ACT-02).

A row reaches the per-project stream ``ACTIVITY_STREAM_KEY`` only once the
transaction that wrote it has committed:

* **Outcome rows** share the caller's transaction. ``service.record`` calls
  :func:`stage` right after ``db.add(row)``; the row and its feed payload wait
  in ``session.info`` until an ``after_commit`` listener on that session keeps
  the rows that are still PERSISTENT (a rolled-back transaction or SAVEPOINT
  expunged the others) and schedules ONE :func:`publish_rows` task.
* **Attempt rows** commit on their own session; ``_write_attempt`` awaits
  :func:`publish_committed_attempt` after that commit.

The payload is ``query.serialize(row)``, the feed's item, serialized while the
row is still pending (every field is set python-side by ``_build_row``), so
the stream can never carry more than the feed does: no ``diff``, values
redacted at write. Redis down, slow or misconfigured never fails, delays
(> ``PUBLISH_TIMEOUT_SECONDS``) or rolls back the mutation: the publish is
logged once, counted and dropped. A dropped row is still in Postgres; a live
viewer sees it on the next page-0 read.

Commit-free (``STAGE_ONLY_SERVICES``): the only writes here go to Redis.
Never raises to a caller.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence

import structlog
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.db.redis_client import get_redis
from app.services.activity.live_vocab import (
    HEARTBEAT_SECONDS,
    MAX_PAYLOAD_BYTES,
    MAX_STREAM_SECONDS,
    MAX_STREAMS_PER_PROCESS,
    MAX_STREAMS_PER_USER,
    PUBLISH_TIMEOUT_SECONDS,
    REPLAY_LIMIT,
    RETRY_MILLISECONDS,
    SETTLE_TIMEOUT_SECONDS,
    STREAM_POLL_SECONDS,
)
from app.streams import (
    ACTIVITY_STREAM_KEY,
    ACTIVITY_STREAM_MAXLEN,
    ACTIVITY_STREAM_TTL_SECONDS,
)

_slog = structlog.get_logger("services.activity.live")

#: The test-wide switch. ``tests/conftest.py`` sets it False for every test, so
#: no existing test gains a listener, a task or a Redis call; the live tests
#: set it back to True inside the test.
PUBLISH_ENABLED: bool = True

_PENDING_KEY = "activity.live.pending"
_LISTENING_KEY = "activity.live.listening"

#: The scheduled publishes per event loop, held until done so a worker task
#: can settle them before its stragglers are cancelled (``loop_runner``).
_INFLIGHT: dict[asyncio.AbstractEventLoop, set[asyncio.Task[Any]]] = {}

#: Reclaim a slot this much past the longest stream's life: a generator that
#: never ran cannot leak a slot for ever.
_SLOT_RECLAIM_SECONDS = MAX_STREAM_SECONDS + 60


def _count(result: str, amount: int = 1) -> None:
    """``activity_live_published_total{result}``; telemetry never breaks a write.

    Incremented by NAME (the ``backend.metrics-are-emitted`` gate reads it).
    """
    if amount <= 0:
        return
    try:
        from app.core.metrics import activity_live_published_total

        activity_live_published_total.labels(result=result).inc(amount)
    except Exception:  # pragma: no cover - telemetry must never break a write
        pass


def _stream_key(project_id: Any) -> str:
    return ACTIVITY_STREAM_KEY.format(project_id=project_id)


def encode_payload(payload: dict) -> str:
    """The stored JSON: the feed response's own encoding (Starlette's
    ``JSONResponse.render``: no ASCII escaping, no spaces), so a frame's
    ``data`` is byte-for-byte the item as ``GET .../activity`` writes it."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


# ── Outcome rows: stage on the caller's session, publish after its commit ────


def _sync_session(db: Any) -> Optional[Session]:
    """The ORM ``Session`` behind ``db``; None for a fake or a mock session."""
    sync = getattr(db, "sync_session", None)
    if sync is None and isinstance(db, Session):
        sync = db
    if not isinstance(sync, Session):
        return None
    if not isinstance(getattr(sync, "info", None), dict):
        return None
    return sync


def stage(db: Any, row: Any) -> None:
    """Hold ``row`` for publication once ``db``'s transaction commits.

    Serializes NOW, while the row is pending and every attribute is the value
    ``_build_row`` set (no flush, no lazy load); installs the listeners ONCE
    per session. No-op when publishing is off or ``db`` is not a real session.
    Never touches Redis, never raises.
    """
    if not PUBLISH_ENABLED:
        return
    try:
        sync = _sync_session(db)
        if sync is None:
            return
        from app.services.activity.query import serialize

        payload = serialize(row)
        sync.info.setdefault(_PENDING_KEY, []).append((row, payload))
        if not sync.info.get(_LISTENING_KEY):
            event.listen(sync, "after_commit", _on_commit)
            event.listen(sync, "after_soft_rollback", _on_rollback)
            sync.info[_LISTENING_KEY] = True
    except Exception as exc:  # the live tail never fails the mutation
        _slog.warning("activity_live_stage_failed", error_type=type(exc).__name__)


def _on_commit(session: Session) -> None:
    """``after_commit``: schedule the publish of the rows that really committed.

    SQLAlchemy dispatches ``after_commit`` for a released SAVEPOINT too; the
    enclosing transaction can still roll back then, so only the ROOT commit
    publishes. A row that is no longer persistent was rolled back (its
    transaction or its SAVEPOINT expunged it): skipped, counted.
    """
    try:
        if session.in_nested_transaction():
            return
        pending = session.info.pop(_PENDING_KEY, None)
        if not pending:
            return
        payloads: list[dict] = []
        skipped = 0
        for row, payload in pending:
            try:
                persistent = bool(sa_inspect(row).persistent)
            except Exception:
                persistent = False
            if persistent:
                payloads.append(payload)
            else:
                skipped += 1
        _count("skipped_rolled_back", skipped)
        if payloads:
            _schedule(payloads)
    except Exception as exc:  # pragma: no cover - a listener must never raise
        _slog.warning("activity_live_commit_hook_failed", error_type=type(exc).__name__)


def _on_rollback(session: Session, previous_transaction: Any) -> None:
    """``after_soft_rollback``: the OUTERMOST rollback forgets what was staged.

    Hygiene only — correctness rests on the persistence check in
    :func:`_on_commit`, so a session reused after a rollback never leaks a row.
    """
    try:
        if getattr(previous_transaction, "parent", None) is None:
            dropped = session.info.pop(_PENDING_KEY, None) or []
            _count("skipped_rolled_back", len(dropped))
    except Exception:  # pragma: no cover - a listener must never raise
        pass


def _schedule(payloads: list[dict]) -> None:
    """One publish task on the running loop, held in ``_INFLIGHT`` until done."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _count("dropped_no_loop", len(payloads))
        _slog.warning(
            "activity_live_publish_dropped", count=len(payloads), error_type="no_running_loop"
        )
        return
    task = loop.create_task(publish_rows(payloads))
    _INFLIGHT.setdefault(loop, set()).add(task)

    def _done(done: "asyncio.Task[Any]") -> None:
        _forget(loop, done)

    task.add_done_callback(_done)


def _forget(loop: asyncio.AbstractEventLoop, task: "asyncio.Task[Any]") -> None:
    tasks = _INFLIGHT.get(loop)
    if tasks is None:
        return
    tasks.discard(task)
    if not tasks:
        _INFLIGHT.pop(loop, None)


def inflight_count(loop: asyncio.AbstractEventLoop) -> int:
    """Publishes scheduled on ``loop`` that have not finished yet."""
    return sum(1 for task in _INFLIGHT.get(loop, ()) if not task.done())


async def drain(timeout: float = SETTLE_TIMEOUT_SECONDS) -> None:
    """Wait (at most ``timeout``) for the running loop's in-flight publishes.

    The worker loop calls it before cancelling a task's stragglers: a run a
    worker finalizes commits as its LAST statement, and the publish that
    commit scheduled would otherwise be cancelled unsent. Never raises.
    """
    try:
        loop = asyncio.get_running_loop()
        tasks = [task for task in _INFLIGHT.get(loop, ()) if not task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
    except Exception as exc:  # pragma: no cover - the settle must never raise
        _slog.warning("activity_live_drain_failed", error_type=type(exc).__name__)


# ── The publish itself ───────────────────────────────────────────────────────


async def publish_rows(payloads: Sequence[dict]) -> int:
    """XADD each payload to its project's stream in ONE pipeline; never raises.

    ``MAXLEN ~ ACTIVITY_STREAM_MAXLEN`` per entry, then ``EXPIRE`` per key,
    bounded by ``PUBLISH_TIMEOUT_SECONDS``. A payload over
    ``MAX_PAYLOAD_BYTES`` is skipped (``dropped_oversize``). Any failure is ONE
    warning and ``dropped_error`` for every row of the batch. A failed publish
    is not retried: the row is in Postgres and the next page-0 read shows it.
    Returns the number published.
    """
    entries: list[tuple[str, str]] = []
    event_types: set[str] = set()
    for payload in payloads:
        try:
            data = encode_payload(payload)
            key = _stream_key(payload["project_id"])
        except Exception:
            _count("dropped_error")
            continue
        if len(data.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            _count("dropped_oversize")
            continue
        entries.append((key, data))
        event_types.add(str(payload.get("event_type")))
    if not entries:
        return 0
    try:
        await asyncio.wait_for(_xadd_all(entries), timeout=PUBLISH_TIMEOUT_SECONDS)
    except Exception as exc:
        _count("dropped_error", len(entries))
        _slog.warning(
            "activity_live_publish_dropped",
            count=len(entries),
            event_types=sorted(event_types),
            error_type=type(exc).__name__,
        )
        return 0
    _count("published", len(entries))
    return len(entries)


async def _xadd_all(entries: list[tuple[str, str]]) -> None:
    pipe = get_redis().pipeline(transaction=False)
    for key, data in entries:
        pipe.xadd(key, {"payload": data}, maxlen=ACTIVITY_STREAM_MAXLEN, approximate=True)
    for key in dict.fromkeys(key for key, _ in entries):
        pipe.expire(key, ACTIVITY_STREAM_TTL_SECONDS)
    await pipe.execute()


async def publish_committed_attempt(payload: dict) -> None:
    """An attempt row, after its own session committed. Bounded; never raises."""
    if not PUBLISH_ENABLED:
        return
    try:
        await publish_rows([payload])
    except Exception:  # pragma: no cover - publish_rows already swallows
        pass


# ── The stream route's side: caps and non-blocking reads ─────────────────────


class StreamSlots:
    """The in-process caps on open streams (per API process, §7.1 R-3).

    A slot carries its start time; one older than the longest stream's life
    plus a minute is reclaimed on the next acquire, so a generator that never
    ran cannot hold a slot for ever. One event loop per API process: no lock.
    """

    def __init__(
        self,
        per_user: int = MAX_STREAMS_PER_USER,
        per_process: int = MAX_STREAMS_PER_PROCESS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_user = per_user
        self.per_process = per_process
        self._clock = clock
        self._slots: dict[uuid.UUID, list[float]] = {}

    def _reclaim(self, now: float) -> None:
        for user_id in list(self._slots):
            alive = [t for t in self._slots[user_id] if now - t <= _SLOT_RECLAIM_SECONDS]
            if alive:
                self._slots[user_id] = alive
            else:
                del self._slots[user_id]

    def total(self) -> int:
        return sum(len(starts) for starts in self._slots.values())

    def held(self, user_id: uuid.UUID) -> int:
        return len(self._slots.get(user_id, ()))

    def acquire(self, user_id: uuid.UUID) -> Optional[str]:
        """None = granted; else the refusal code ``too_many_streams`` | ``server_busy``."""
        now = self._clock()
        self._reclaim(now)
        if self.held(user_id) >= self.per_user:
            return "too_many_streams"
        if self.total() >= self.per_process:
            return "server_busy"
        self._slots.setdefault(user_id, []).append(now)
        return None

    def release(self, user_id: uuid.UUID) -> None:
        starts = self._slots.get(user_id)
        if not starts:
            return
        starts.pop(0)
        if not starts:
            del self._slots[user_id]

    def clear(self) -> None:
        self._slots.clear()


SLOTS = StreamSlots()


class LiveUnavailable(Exception):
    """Redis could not be read: the live tail is unavailable (never "quiet")."""


def _id_key(stream_id: str) -> tuple[int, int]:
    ms, _, seq = stream_id.partition("-")
    return int(ms), int(seq or 0)


def id_after(left: str, right: str) -> bool:
    """True when stream id ``left`` is strictly newer than ``right``."""
    return _id_key(left) > _id_key(right)


async def stream_head(project_id: uuid.UUID) -> str:
    """The newest id of the project's stream, or ``0-0`` when it is empty."""
    try:
        newest = await get_redis().xrevrange(_stream_key(project_id), "+", "-", count=1)
    except Exception as exc:
        raise LiveUnavailable(type(exc).__name__) from exc
    return str(newest[0][0]) if newest else "0-0"


async def read_after(
    project_id: uuid.UUID, after_id: str, *, limit: int = REPLAY_LIMIT
) -> tuple[list[tuple[str, dict]], bool]:
    """The entries newer than ``after_id``, oldest first, and whether there is a gap.

    Two NON-blocking ``XRANGE`` calls in one round trip (no ``BLOCK``: no pooled
    connection is pinned): the entries after ``after_id`` (``COUNT limit + 1``)
    and the stream's first entry. A gap = more than ``limit`` entries behind, or
    ``after_id`` older than the first entry (trimmed away). ``0-0`` (the head of
    an empty stream) is never trimmed. Redis error → :class:`LiveUnavailable`.
    """
    key = _stream_key(project_id)
    try:
        pipe = get_redis().pipeline(transaction=False)
        pipe.xrange(key, f"({after_id}", "+", count=limit + 1)
        pipe.xrange(key, "-", "+", count=1)
        newer, first = await pipe.execute()
    except Exception as exc:
        raise LiveUnavailable(type(exc).__name__) from exc
    entries = [(str(entry_id), dict(fields)) for entry_id, fields in (newer or [])]
    gap = len(entries) > limit
    if not gap and after_id != "0-0" and first:
        gap = id_after(str(first[0][0]), after_id)
    return entries[:limit], gap


# ── The SSE frames ───────────────────────────────────────────────────────────

#: Indirections the stream tests patch (never the global ``asyncio.sleep``).
_clock = time.monotonic


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def sse_frame(event_name: str, data: str, frame_id: Optional[str] = None) -> str:
    """One ``text/event-stream`` frame. ``data`` is one line of JSON."""
    id_line = f"id: {frame_id}\n" if frame_id is not None else ""
    return f"event: {event_name}\n{id_line}data: {data}\n\n"


def _json(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"))


async def stream_frames(
    *,
    project_id: uuid.UUID,
    head: str,
    last_event_id: Optional[str],
    lifetime_seconds: int,
    deadline_reason: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    on_close: Callable[[], None],
) -> AsyncIterator[str]:
    """The stream's frames (contract §1.2): ``retry``, ``ready``, then a poll a second.

    Reads ONLY ``ACTIVITY_STREAM_KEY`` of the authorized ``project_id``; never
    touches Postgres. A frame's ``data`` is the payload string exactly as
    :func:`publish_rows` stored it. ``on_close`` (the slot release) runs when
    the generator ends, however it ends.
    """
    try:
        deadline = _clock() + lifetime_seconds
        yield f"retry: {RETRY_MILLISECONDS}\n\n"
        cursor, ready_id, opening, ended = await _opening_frames(
            project_id, head, last_event_id
        )
        ready = {
            "project_id": str(project_id),
            "last_event_id": ready_id,
            "heartbeat_seconds": HEARTBEAT_SECONDS,
            "max_seconds": lifetime_seconds,
        }
        yield sse_frame("ready", _json(ready), ready_id)
        for frame in opening:
            yield frame
        if ended:
            return
        last_frame_at = _clock()
        while True:
            await _sleep(STREAM_POLL_SECONDS)
            if await is_disconnected():
                return
            if _clock() >= deadline:
                yield sse_frame("bye", _json({"reason": deadline_reason}))
                return
            try:
                entries, gap = await read_after(project_id, cursor)
                if gap:
                    cursor = await stream_head(project_id)
            except LiveUnavailable:
                yield sse_frame("bye", _json({"reason": "unavailable"}))
                return
            if gap:
                yield sse_frame("refresh", _json({"reason": "overflow"}), cursor)
                last_frame_at = _clock()
                continue
            for entry_id, fields in entries:
                cursor = entry_id
                data = fields.get("payload")
                if isinstance(data, str):
                    yield sse_frame("activity", data, entry_id)
                    last_frame_at = _clock()
            if _clock() - last_frame_at >= HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"
                last_frame_at = _clock()
    finally:
        on_close()


async def _opening_frames(
    project_id: uuid.UUID, head: str, last_event_id: Optional[str]
) -> tuple[str, str, list[str], bool]:
    """(cursor, ready id, frames after ``ready``, stream ended) for a connect or a resume.

    A clean resume replays what the client missed; a resume too far behind,
    trimmed away, or ahead of the head (not an id this stream issued) is a
    ``refresh {"reason": "gap"}`` from the head. Redis failing here ends the
    stream the way a mid-stream failure does (``bye unavailable``).
    """
    if not last_event_id:
        return head, head, [], False
    gap = id_after(last_event_id, head)
    entries: list[tuple[str, dict]] = []
    if not gap:
        try:
            entries, gap = await read_after(project_id, last_event_id)
        except LiveUnavailable:
            return head, head, [sse_frame("bye", _json({"reason": "unavailable"}))], True
    if gap:
        return head, head, [sse_frame("refresh", _json({"reason": "gap"}), head)], False
    frames = [
        sse_frame("activity", fields["payload"], entry_id)
        for entry_id, fields in entries
        if isinstance(fields.get("payload"), str)
    ]
    cursor = entries[-1][0] if entries else last_event_id
    return cursor, last_event_id, frames, False
