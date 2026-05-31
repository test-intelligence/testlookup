"""Regression: live-consumer DLQ threshold must use the stream delivery count.

Bug (2026-05-30 multi-pass review, `live-stream-ingestion` audit, Major):
``LiveEventStreamConsumer._handle_message`` counted delivery attempts in an
in-process ``self._retry_counts`` dict. That dict was lost on restart and not
shared across uvicorn workers, so a poison message reclaimed by a different
consumer restarted its count at 0 — ``MAX_DELIVERY_ATTEMPTS`` was never
enforced consistently across the fleet and a bad message could loop forever.

Fix: derive the attempt count from Redis' own PEL ``times_delivered``
(``XPENDING``), which is shared across consumers and durable across restarts.
A message is DLQ'd once ``times_delivered >= MAX_DELIVERY_ATTEMPTS``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class _FakeRedis:
    def __init__(self, times_delivered: int):
        self._times = times_delivered
        self.xpending_calls = 0

    async def xpending_range(self, *args, **kwargs):
        self.xpending_calls += 1
        return [{"times_delivered": self._times}]


def _consumer_with_failing_process():
    from app.streams.live_consumer import LiveEventStreamConsumer

    c = LiveEventStreamConsumer()
    c._process = AsyncMock(side_effect=RuntimeError("processing failed"))
    c._ack = AsyncMock()
    c._move_to_dlq = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_below_threshold_left_pending_not_dlqd():
    from app.streams.live_consumer import MAX_DELIVERY_ATTEMPTS

    consumer = _consumer_with_failing_process()
    fake_redis = _FakeRedis(times_delivered=MAX_DELIVERY_ATTEMPTS - 1)

    with patch("app.streams.live_consumer.get_redis", return_value=fake_redis):
        await consumer._handle_message("1-0", {"payload": "{}", "run_id": "r1"})

    # Used the stream counter, not an in-process dict.
    assert fake_redis.xpending_calls == 1
    # Under the threshold → leave pending for XAUTOCLAIM; do NOT DLQ or ACK.
    consumer._move_to_dlq.assert_not_called()
    consumer._ack.assert_not_called()


@pytest.mark.asyncio
async def test_at_threshold_moves_to_dlq_and_acks():
    from app.streams.live_consumer import MAX_DELIVERY_ATTEMPTS

    consumer = _consumer_with_failing_process()
    fake_redis = _FakeRedis(times_delivered=MAX_DELIVERY_ATTEMPTS)

    with patch("app.streams.live_consumer.get_redis", return_value=fake_redis):
        await consumer._handle_message("1-0", {"payload": "{}", "run_id": "r1"})

    consumer._move_to_dlq.assert_awaited_once()
    consumer._ack.assert_awaited_once()


def test_retry_counts_dict_is_gone():
    """The in-process counter must not come back — it was the root cause."""
    from app.streams.live_consumer import LiveEventStreamConsumer

    assert not hasattr(LiveEventStreamConsumer(), "_retry_counts"), (
        "LiveEventStreamConsumer re-introduced the per-process _retry_counts "
        "dict; DLQ attempts must be tracked via the stream's times_delivered."
    )
