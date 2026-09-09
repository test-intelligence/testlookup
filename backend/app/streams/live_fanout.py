"""Ordered Redis-backed fan-out for live dashboard notifications.

The ingestion consumer group processes each source event once.  It appends the
resulting dashboard notification to a second Redis Stream.  Every API process
reads that stream with ``XREAD`` (without a consumer group), so every process
sees every notification and can deliver it to its own WebSocket and SSE
connections.  Stream IDs are exposed as sequence IDs for client de-duplication
and bounded reconnect replay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable, cast

from app.db.redis_client import get_redis
from app.streams import (
    CONSUMER_BLOCK_MS,
    LIVE_FANOUT_DEDUP_KEY,
    LIVE_FANOUT_MAXLEN,
    LIVE_FANOUT_PROJECT_MAXLEN,
    LIVE_FANOUT_PROJECT_STREAM_KEY,
    LIVE_FANOUT_STREAM,
)

logger = logging.getLogger("streams.live_fanout")

FanoutHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
REPLAY_LIMIT = 1_000
_project_locks: dict[str, asyncio.Lock] = {}
_DEDUP_TTL_SECONDS = 86_400
_MAX_PAYLOAD_BYTES = 65_536
_PUBLISH_SCRIPT = """
local existing = redis.call('GET', KEYS[3])
if existing then return existing end
local event_id = redis.call(
  'XADD', KEYS[2], 'MAXLEN', '~', ARGV[1], '*', 'payload', ARGV[3]
)
redis.call(
  'XADD', KEYS[1], 'MAXLEN', '~', ARGV[2], '*',
  'project_id', ARGV[4], 'payload', ARGV[3], 'sequence_id', event_id
)
redis.call('SET', KEYS[3], event_id, 'EX', ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[5])
redis.call('EXPIRE', KEYS[2], ARGV[5])
return event_id
"""


def _count_publish(result: str) -> None:
    try:
        from app.core.metrics import live_fanout_published_total
        live_fanout_published_total.labels(result=result).inc()
    except Exception:
        pass


def _count_replay(result: str) -> None:
    try:
        from app.core.metrics import live_fanout_replay_total
        live_fanout_replay_total.labels(result=result).inc()
    except Exception:
        pass


def _count_delivery(transport: str, result: str) -> None:
    try:
        from app.core.metrics import live_fanout_delivered_total
        live_fanout_delivered_total.labels(transport=transport, result=result).inc()
    except Exception:
        pass


def _set_subscriber_ready(value: int) -> None:
    try:
        from app.core.metrics import live_fanout_subscriber_ready
        live_fanout_subscriber_ready.set(value)
    except Exception:
        pass


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _stream_id(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", 1)
    return int(milliseconds), int(sequence)


def sequence_at_or_before(value: str, floor: str) -> bool:
    return _stream_id(value) <= _stream_id(floor)


def project_delivery_lock(project_id: str) -> asyncio.Lock:
    """Serialize one process's bootstrap and local delivery for a project."""
    return _project_locks.setdefault(project_id, asyncio.Lock())


def _decode_entry(message_id: Any, fields: dict[Any, Any]) -> tuple[str, dict[str, Any]]:
    project_id = _text(fields.get("project_id", fields.get(b"project_id", "")))
    raw_payload = fields.get("payload", fields.get(b"payload", "{}"))
    payload = json.loads(_text(raw_payload))
    sequence_id = fields.get("sequence_id", fields.get(b"sequence_id", message_id))
    payload["sequence_id"] = _text(sequence_id)
    return project_id, payload


async def publish_live_notification(
    project_id: str,
    payload: dict[str, Any],
    *,
    logical_event_id: str | None = None,
) -> str:
    """Durably append one notification before its source event is ACKed."""
    logical_event_id = logical_event_id or str(uuid.uuid4())
    encoded = json.dumps(payload)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("live fan-out payload exceeds 64 KiB")
    try:
        message_id = await get_redis().eval(
            _PUBLISH_SCRIPT,
            3,
            LIVE_FANOUT_STREAM,
            LIVE_FANOUT_PROJECT_STREAM_KEY.format(project_id=project_id),
            LIVE_FANOUT_DEDUP_KEY.format(event_id=logical_event_id),
            LIVE_FANOUT_PROJECT_MAXLEN,
            LIVE_FANOUT_MAXLEN,
            encoded,
            project_id,
            _DEDUP_TTL_SECONDS,
        )
    except Exception:
        _count_publish("error")
        raise
    _count_publish("success")
    return _text(message_id)


async def replay_live_notifications(
    project_id: str,
    after_id: str,
    *,
    through_id: str = "+",
    limit: int = REPLAY_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ordered replay and whether the cursor needs reconciliation."""
    redis = get_redis()
    stream = LIVE_FANOUT_PROJECT_STREAM_KEY.format(project_id=project_id)
    bounds = await redis.xrange(stream, min="-", max="+", count=1)
    if not bounds:
        _count_replay("gap" if after_id != "0-0" else "empty")
        return [], after_id != "0-0"
    first_id = _text(bounds[0][0])
    head_id = through_id
    if head_id == "+":
        latest = await redis.xrevrange(stream, max="+", min="-", count=1)
        head_id = _text(latest[0][0])
    if _stream_id(after_id) < _stream_id(first_id) or _stream_id(after_id) > _stream_id(head_id):
        _count_replay("gap")
        return [], True

    rows = await redis.xrange(
        stream,
        min=f"({after_id}",
        max=head_id,
        count=limit + 1,
    )
    if len(rows) > limit:
        _count_replay("overflow")
        return [], True
    events: list[dict[str, Any]] = []
    for message_id, fields in rows:
        raw_payload = fields.get("payload", fields.get(b"payload", "{}"))
        payload = json.loads(_text(raw_payload))
        payload["sequence_id"] = _text(message_id)
        events.append(payload)
    _count_replay("replayed")
    return events, False


async def latest_live_sequence(project_id: str) -> str:
    """Return the current fan-out high-water mark, or ``0-0`` when empty."""
    rows = await get_redis().xrevrange(
        LIVE_FANOUT_PROJECT_STREAM_KEY.format(project_id=project_id),
        max="+", min="-", count=1,
    )
    return _text(rows[0][0]) if rows else "0-0"


class LiveFanoutSubscriber:
    """Independent stream reader owned by one API process."""

    def __init__(self, handler: FanoutHandler | None = None) -> None:
        self._running = False
        self._cursor = "$"
        self._handler = handler or _deliver_locally
        self._reported_gap_head: str | None = None

    async def initialize(self) -> None:
        """Capture the global high-water mark before the API becomes ready."""
        rows = await get_redis().xrevrange(
            LIVE_FANOUT_STREAM, max="+", min="-", count=1,
        )
        self._cursor = _text(rows[0][0]) if rows else "0-0"
        _set_subscriber_ready(1)

    async def run(self) -> None:
        self._running = True
        logger.info("Live fan-out subscriber starting")
        while self._running:
            try:
                redis = get_redis()
                first = await redis.xrange(
                    LIVE_FANOUT_STREAM, min="-", max="+", count=1,
                )
                if first and self._cursor not in ("$", "0-0"):
                    first_id = _text(first[0][0])
                    if (
                        _stream_id(self._cursor) < _stream_id(first_id)
                        and self._reported_gap_head != first_id
                    ):
                        await _reconcile_locally()
                        self._reported_gap_head = first_id
                read_cursor = self._cursor
                rows = await redis.xread(
                    streams={LIVE_FANOUT_STREAM: read_cursor},
                    count=100,
                    block=CONSUMER_BLOCK_MS,
                )
                _set_subscriber_ready(1)
                if not rows:
                    continue
                # The bounded stream can trim between the head check above and
                # XREAD. Recheck before advancing the cursor or delivering the
                # retained suffix, otherwise this process can silently miss the
                # trimmed interval and never reconcile its connected clients.
                retained = await redis.xrange(
                    LIVE_FANOUT_STREAM, min="-", max="+", count=1,
                )
                if retained and read_cursor not in ("$", "0-0"):
                    retained_id = _text(retained[0][0])
                    if _stream_id(read_cursor) < _stream_id(retained_id):
                        if self._reported_gap_head != retained_id:
                            await _reconcile_locally()
                            self._reported_gap_head = retained_id
                entries = cast(list[tuple[Any, list[tuple[Any, dict[Any, Any]]]]], rows)[0][1]
                for message_id, fields in entries:
                    # Advance even for malformed entries so one poison record cannot
                    # permanently wedge every API process at the same cursor.
                    self._cursor = _text(message_id)
                    try:
                        project_id, payload = _decode_entry(message_id, fields)
                        if project_id:
                            await self._handler(project_id, payload)
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        logger.warning("Ignoring malformed live fan-out entry id=%s: %s", self._cursor, exc)
            except asyncio.CancelledError:
                self._running = False
                _set_subscriber_ready(0)
                raise
            except Exception as exc:  # Redis reconnect path
                _set_subscriber_ready(0)
                logger.error("Live fan-out read failed: %s; retrying", exc)
                await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False


async def _deliver_locally(project_id: str, payload: dict[str, Any]) -> None:
    """Deliver a Redis notification to connections owned by this process."""
    from app.routers.live import manager
    from app.routers.stream import push_to_sse

    async with project_delivery_lock(project_id):
        results = await asyncio.gather(
            manager.broadcast(project_id, payload),
            push_to_sse(project_id, payload),
            return_exceptions=True,
        )
        for transport, result in zip(("websocket", "sse"), results):
            _count_delivery(
                transport, "error" if isinstance(result, BaseException) else "success",
            )


async def _reconcile_locally() -> None:
    """Tell local clients when this process crossed a trimmed relay gap."""
    from app.routers.live import manager
    from app.routers.stream import _sse_subscribers, push_to_sse

    projects = set(manager._channels) | set(_sse_subscribers)
    await asyncio.gather(
        *(manager.broadcast(project_id, {"type": "reconcile_required"}) for project_id in projects),
        *(push_to_sse(project_id, {"type": "reconcile_required"}) for project_id in projects),
        return_exceptions=True,
    )
