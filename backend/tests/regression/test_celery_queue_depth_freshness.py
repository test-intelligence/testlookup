"""Each logical queue carries its own complete priority-read freshness."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import bootstrap
from app.core.metrics import celery_queue_depth_observed_timestamp_seconds, celery_queue_length
from app.services import celery_queue_metrics


def _depth(queue):
    return celery_queue_length.labels(queue_name=queue)._value.get()


def _observed(queue):
    return celery_queue_depth_observed_timestamp_seconds.labels(queue_name=queue)._value.get()


@pytest.mark.asyncio
async def test_successful_empty_and_positive_depths_advance_each_queue_observation(monkeypatch):
    monkeypatch.setattr(celery_queue_metrics, "read_celery_queue_depths",
                        AsyncMock(return_value={"agent_children":7,"default":0}))
    monkeypatch.setattr("time.time", lambda: 1234.0)
    assert await bootstrap._refresh_celery_queue_depths()
    assert _depth("agent_children") == 7 and _depth("default") == 0
    assert _observed("agent_children") == _observed("default") == 1234


@pytest.mark.asyncio
async def test_explicit_bad_bin_invalidates_only_its_logical_queue_then_recovers(monkeypatch):
    read = AsyncMock(side_effect=[{"agent_children":7,"default":3},
                                 {"agent_children":None,"default":2},
                                 {"agent_children":0,"default":0}])
    monkeypatch.setattr(celery_queue_metrics, "read_celery_queue_depths", read)
    clock = [100.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    assert await bootstrap._refresh_celery_queue_depths()
    clock[0] = 110.0
    assert not await bootstrap._refresh_celery_queue_depths()
    assert _depth("agent_children") == 7 and _observed("agent_children") == 0
    assert _depth("default") == 2 and _observed("default") == 110
    clock[0] = 120.0
    assert await bootstrap._refresh_celery_queue_depths()
    assert _depth("agent_children") == 0 and _observed("agent_children") == 120


@pytest.mark.asyncio
async def test_cancelled_or_unreturned_read_preserves_last_timestamp_for_freshness_expiry(monkeypatch):
    monkeypatch.setattr(celery_queue_metrics, "read_celery_queue_depths",
                        AsyncMock(return_value={"agent_children":5}))
    monkeypatch.setattr("time.time", lambda: 100.0)
    assert await bootstrap._refresh_celery_queue_depths()
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(celery_queue_metrics, "read_celery_queue_depths", blocked)
    task = asyncio.create_task(bootstrap._refresh_celery_queue_depths())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _depth("agent_children") == 5 and _observed("agent_children") == 100
    monkeypatch.setattr(celery_queue_metrics, "read_celery_queue_depths", AsyncMock(side_effect=RuntimeError("synthetic")))
    assert not await bootstrap._refresh_celery_queue_depths()
    assert _observed("agent_children") == 100
