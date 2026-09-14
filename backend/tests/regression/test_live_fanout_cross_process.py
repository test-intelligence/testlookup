"""H01: every API process must receive the ordered live notification stream."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.streams import LIVE_FANOUT_PROJECT_STREAM_KEY, LIVE_FANOUT_STREAM
from app.streams.live_fanout import (
    LiveFanoutSubscriber,
    publish_live_notification,
    replay_live_notifications,
)
from app.routers import stream as stream_router


class _FanoutRedis:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, str]]] = []
        self.project_rows: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.dedupe: dict[str, str] = {}
        self.changed = asyncio.Condition()

    async def eval(self, _script, _keys, global_stream, project_stream, dedupe_key, *_args):
        assert global_stream == LIVE_FANOUT_STREAM
        if dedupe_key in self.dedupe:
            return self.dedupe[dedupe_key]
        payload, project_id = _args[2], _args[3]
        project = self.project_rows.setdefault(project_stream, [])
        event_id = f"{len(project) + 1}-0"
        project.append((event_id, {"payload": payload}))
        global_id = f"{len(self.rows) + 1}-0"
        self.rows.append((global_id, {
            "project_id": project_id, "payload": payload, "sequence_id": event_id,
        }))
        self.dedupe[dedupe_key] = event_id
        async with self.changed:
            self.changed.notify_all()
        return event_id

    async def xread(self, streams, **_kwargs):
        cursor = streams[LIVE_FANOUT_STREAM]
        if cursor == "$":
            cursor = f"{len(self.rows)}-0"
        start = int(cursor.split("-")[0])
        async with self.changed:
            await self.changed.wait_for(lambda: len(self.rows) > start)
        return [(LIVE_FANOUT_STREAM, self.rows[start:])]

    async def xrange(self, stream, min, max, count):
        rows = self.rows if stream == LIVE_FANOUT_STREAM else self.project_rows.get(stream, [])
        if min == "-":
            return rows[:count]
        start = int(min.removeprefix("(").split("-")[0])
        end = len(rows) if max == "+" else int(max.split("-")[0])
        return rows[start:end][:count]

    async def xrevrange(self, stream, **_kwargs):
        rows = self.project_rows.get(stream, [])
        return rows[-1:] if rows else []


@pytest.mark.asyncio
async def test_two_process_subscribers_receive_identical_ordered_events():
    redis = _FanoutRedis()
    process_a: list[tuple[str, dict]] = []
    process_b: list[tuple[str, dict]] = []

    async def deliver_a(project_id, payload):
        process_a.append((project_id, payload))

    async def deliver_b(project_id, payload):
        process_b.append((project_id, payload))

    a = LiveFanoutSubscriber(deliver_a)
    b = LiveFanoutSubscriber(deliver_b)
    with patch("app.streams.live_fanout.get_redis", return_value=redis):
        await asyncio.gather(a.initialize(), b.initialize())
        tasks = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        await asyncio.sleep(0)
        for number in range(3):
            await publish_live_notification("project-a", {"type": "event", "number": number})
        for _ in range(20):
            if len(process_a) == len(process_b) == 3:
                break
            await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert process_a == process_b
    assert [row[1]["number"] for row in process_a] == [0, 1, 2]
    assert [row[1]["sequence_id"] for row in process_a] == ["1-0", "2-0", "3-0"]


@pytest.mark.asyncio
async def test_reconnect_replays_only_later_events_for_the_requested_project():
    redis = _FanoutRedis()
    with patch("app.streams.live_fanout.get_redis", return_value=redis):
        await publish_live_notification("project-a", {"type": "first"})
        await publish_live_notification("project-b", {"type": "other"})
        await publish_live_notification("project-a", {"type": "third"})
        events, reconcile = await replay_live_notifications("project-a", "1-0")

    assert reconcile is False
    assert events == [{"type": "third", "sequence_id": "2-0"}]


@pytest.mark.asyncio
async def test_retry_uses_one_logical_event_and_one_sequence_id():
    redis = _FanoutRedis()
    with patch("app.streams.live_fanout.get_redis", return_value=redis):
        first = await publish_live_notification(
            "project-a", {"type": "result"}, logical_event_id="source:7-0:0",
        )
        retry = await publish_live_notification(
            "project-a", {"type": "result"}, logical_event_id="source:7-0:0",
        )

    assert first == retry == "1-0"
    stream = LIVE_FANOUT_PROJECT_STREAM_KEY.format(project_id="project-a")
    assert len(redis.project_rows[stream]) == 1


@pytest.mark.asyncio
async def test_publish_failure_keeps_source_message_unacked():
    from app.streams.live_consumer import LiveEventStreamConsumer

    consumer = LiveEventStreamConsumer()
    with (
        patch.object(consumer, "_process", AsyncMock(side_effect=ConnectionError("redis unavailable"))),
        patch.object(consumer, "_delivery_count", AsyncMock(return_value=1)),
        patch.object(consumer, "_ack", AsyncMock()) as ack,
    ):
        await consumer._handle_message(
            "7-0",
            {"run_id": "run-1", "event_type": "run_start", "payload": json.dumps({})},
        )
        ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_trimmed_reconnect_cursor_requires_authoritative_reconciliation():
    redis = _FanoutRedis()
    stream = LIVE_FANOUT_PROJECT_STREAM_KEY.format(project_id="project-a")
    redis.project_rows[stream] = [
        ("5-0", {"payload": json.dumps({"type": "fifth"})}),
        ("6-0", {"payload": json.dumps({"type": "sixth"})}),
    ]
    with patch("app.streams.live_fanout.get_redis", return_value=redis):
        events, reconcile = await replay_live_notifications(
            "project-a", "2-0", through_id="6-0",
        )

    assert events == []
    assert reconcile is True


@pytest.mark.asyncio
async def test_oversized_notification_is_rejected_before_redis_write():
    redis = _FanoutRedis()
    with (
        patch("app.streams.live_fanout.get_redis", return_value=redis),
        pytest.raises(ValueError, match="64 KiB"),
    ):
        await publish_live_notification("project-a", {"value": "x" * 70_000})
    assert redis.rows == []


@pytest.mark.asyncio
async def test_slow_sse_subscriber_is_told_to_reconcile_instead_of_starved():
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"type": "old"})
    stream_router._sse_subscribers["project-a"] = {queue}
    try:
        await stream_router.push_to_sse(
            "project-a", {"type": "new", "sequence_id": "9-0"},
        )
        assert queue.get_nowait() == {"type": "reconcile_required"}
        assert queue in stream_router._sse_subscribers["project-a"]
    finally:
        stream_router._sse_subscribers.clear()


@pytest.mark.asyncio
async def test_sse_suppresses_relay_batch_already_covered_by_bootstrap():
    queue = asyncio.Queue(maxsize=2)
    stream_router._sse_subscribers["project-a"] = {queue}
    stream_router._sse_bootstrap_floors[queue] = "10-0"
    try:
        await stream_router.push_to_sse(
            "project-a", {"type": "duplicate", "sequence_id": "9-0"},
        )
        await stream_router.push_to_sse(
            "project-a", {"type": "live", "sequence_id": "11-0"},
        )
        assert queue.get_nowait()["type"] == "live"
        assert queue.empty()
    finally:
        stream_router._sse_subscribers.clear()
        stream_router._sse_bootstrap_floors.clear()


@pytest.mark.asyncio
async def test_relay_trim_gap_forces_local_reconciliation():
    redis = _FanoutRedis()
    redis.rows = [("5-0", {"project_id": "project-a", "payload": "{}"})]
    subscriber = LiveFanoutSubscriber()
    subscriber._cursor = "1-0"
    reconcile = AsyncMock()
    with (
        patch("app.streams.live_fanout.get_redis", return_value=redis),
        patch("app.streams.live_fanout._reconcile_locally", reconcile),
    ):
        task = asyncio.create_task(subscriber.run())
        for _ in range(10):
            if reconcile.await_count:
                break
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_trim_between_head_check_and_read_forces_reconciliation():
    class TrimDuringReadRedis(_FanoutRedis):
        def __init__(self):
            super().__init__()
            self.rows = [("1-0", {"project_id": "project-a", "payload": "{}"})]

        async def xread(self, streams, **_kwargs):
            assert streams[LIVE_FANOUT_STREAM] == "1-0"
            self.rows = [("5-0", {"project_id": "project-a", "payload": "{}"})]
            return [(LIVE_FANOUT_STREAM, self.rows)]

    redis = TrimDuringReadRedis()
    subscriber = LiveFanoutSubscriber()
    subscriber._cursor = "1-0"
    reconcile = AsyncMock()
    delivered = AsyncMock()
    subscriber._handler = delivered
    with (
        patch("app.streams.live_fanout.get_redis", return_value=redis),
        patch("app.streams.live_fanout._reconcile_locally", reconcile),
    ):
        task = asyncio.create_task(subscriber.run())
        for _ in range(10):
            if delivered.await_count:
                break
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    reconcile.assert_awaited_once()
    delivered.assert_awaited_once()


@pytest.mark.asyncio
async def test_leader_drains_every_pending_page_before_new_reads():
    from app.streams.live_consumer import LiveEventStreamConsumer

    class PagedPendingRedis:
        def __init__(self):
            self.starts = []

        async def xautoclaim(self, *_args, start_id, **_kwargs):
            self.starts.append(start_id)
            if len(self.starts) == 1:
                return ("51-0", [(f"{i}-0", {}) for i in range(1, 51)], [])
            if len(self.starts) == 2:
                return ("0-0", [(f"{i}-0", {}) for i in range(51, 76)], [])
            return ("0-0", [], [])

    redis = PagedPendingRedis()
    consumer = LiveEventStreamConsumer()
    handled = []

    async def handle(message_id, _data):
        handled.append(message_id)
        return True

    consumer._handle_message = handle
    consumer._hold_leadership = AsyncMock(return_value=True)
    with patch("app.streams.live_consumer.get_redis", return_value=redis):
        await consumer._reclaim_stale(min_idle_time=0, drain_all=True)

    assert redis.starts[:2] == ["0-0", "51-0"]
    assert handled == [f"{i}-0" for i in range(1, 76)]


@pytest.mark.asyncio
async def test_leadership_drain_failure_cannot_fall_through_to_new_reads():
    from app.streams.live_consumer import LiveEventStreamConsumer

    consumer = LiveEventStreamConsumer()
    consumer._just_acquired_leadership = True
    consumer._ensure_group = AsyncMock()
    consumer._hold_leadership = AsyncMock(return_value=True)
    consumer._reclaim_stale = AsyncMock(
        side_effect=[TimeoutError("claim timed out"), asyncio.CancelledError()],
    )
    consumer._read_new = AsyncMock()
    consumer._release_leadership = AsyncMock()

    with patch("app.streams.live_consumer.asyncio.sleep", AsyncMock()):
        await consumer.run()

    consumer._read_new.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_leadership_drain_propagates_xautoclaim_failure():
    from app.streams.live_consumer import LiveEventStreamConsumer

    redis = AsyncMock()
    redis.xautoclaim.side_effect = TimeoutError("claim timed out")
    consumer = LiveEventStreamConsumer()
    with (
        patch("app.streams.live_consumer.get_redis", return_value=redis),
        pytest.raises(TimeoutError, match="claim timed out"),
    ):
        await consumer._reclaim_stale(min_idle_time=0, drain_all=True)


@pytest.mark.asyncio
async def test_failed_batch_prefix_blocks_later_events_until_pending_drain():
    from app.streams.live_consumer import LiveEventStreamConsumer

    consumer = LiveEventStreamConsumer()
    consumer._must_drain_pending = False  # startup drain already completed
    consumer._last_reclaim = time.time()
    consumer._ensure_group = AsyncMock()
    consumer._hold_leadership = AsyncMock(return_value=True)
    consumer._read_new = AsyncMock(return_value=[("1-0", {}), ("2-0", {})])
    consumer._handle_message = AsyncMock(return_value=False)
    consumer._reclaim_stale = AsyncMock(side_effect=asyncio.CancelledError())
    consumer._release_leadership = AsyncMock()

    await consumer.run()

    consumer._read_new.assert_awaited_once()
    consumer._handle_message.assert_awaited_once_with("1-0", {})
    consumer._reclaim_stale.assert_awaited_once_with(
        min_idle_time=0, drain_all=True,
    )


@pytest.mark.asyncio
async def test_leadership_loss_during_pending_drain_is_not_success():
    from app.streams.live_consumer import LiveEventStreamConsumer

    redis = AsyncMock()
    redis.xautoclaim.return_value = ("0-0", [("1-0", {})], [])
    consumer = LiveEventStreamConsumer()
    consumer._hold_leadership = AsyncMock(return_value=False)
    consumer._handle_message = AsyncMock()

    with patch("app.streams.live_consumer.get_redis", return_value=redis):
        drained = await consumer._reclaim_stale(min_idle_time=0, drain_all=True)

    assert drained is False
    consumer._handle_message.assert_not_awaited()


def test_projector_identity_is_restart_unique_and_first_turn_drains_pending():
    import uuid

    from app.streams.live_consumer import (
        _CONSUMER_NAME,
        _LEADER_TTL_SECONDS,
        LiveEventStreamConsumer,
    )

    incarnation = _CONSUMER_NAME.rsplit(":", 1)[-1]
    assert uuid.UUID(hex=incarnation).hex == incarnation
    assert _LEADER_TTL_SECONDS <= 30
    assert LiveEventStreamConsumer()._must_drain_pending is True


@pytest.mark.asyncio
async def test_live_analysis_publication_is_at_least_once_without_preclaim():
    from app.streams.live_consumer import _queue_live_analysis

    task = MagicMock()
    with patch("app.worker.tasks.run_live_test_analysis", task):
        for _ in range(2):
            await _queue_live_analysis(
                "case-1", "failed test", "run-1", "project-a", "live-analysis:7-0:case-1",
            )

    assert task.apply_async.call_count == 2
    assert {
        call.kwargs["task_id"] for call in task.apply_async.call_args_list
    } == {"live-analysis:7-0:case-1"}


@pytest.mark.asyncio
async def test_timed_out_websocket_is_closed_so_the_client_can_reconnect(monkeypatch):
    from app.core.config import settings
    from app.routers.live import ConnectionManager

    class SlowSocket:
        def __init__(self):
            self.closed = None

        async def send_text(self, _payload):
            await asyncio.sleep(1)

        async def close(self, *, code, reason):
            self.closed = (code, reason)

    monkeypatch.setattr(settings, "WS_BROADCAST_TIMEOUT", 0.01)
    manager = ConnectionManager()
    socket = SlowSocket()
    manager.register("project-a", socket)
    await manager.broadcast("project-a", {"type": "event"})

    assert socket.closed is not None and socket.closed[0] == 1013
    assert manager.active_connections == 0
