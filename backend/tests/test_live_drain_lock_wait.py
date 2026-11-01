"""W25e E-1 F-4 T-A4 — the close-time drain waits for a peer drainer instead of failing.

Since W25e a closed run's ``persist_live_session`` is published the moment the
close commits, so it can meet the 30 s ``drain-active-live-sessions`` beat
holding the run's drain lock mid-chunk (``drain_run_buffer`` then returns
``drained: 0, lock_held: False``). The close-time drain polls for up to
``DRAIN_LOCK_WAIT_SECONDS``; past it, with evidence still in the stream, it
raises ``LiveEvidenceDrainBusy``, which the outbox treats as a deferral that
spends no execution attempt (a timeout is not a failure). The crashed-consumer
case (lock FREE, entries left) is still the plain RuntimeError.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _LengthsRedis:
    def __init__(self, *, stream: int = 0, legacy: int = 0) -> None:
        self.stream = stream
        self.legacy = legacy

    async def xlen(self, _key):
        return self.stream

    async def llen(self, _key):
        return self.legacy


class _FakeTime:
    """A monotonic clock that only the recorded sleeps advance."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _wire(monkeypatch, *, redis, drain):
    from app.db import redis_client
    from app.services import live_session_drainer
    from app.worker import tasks

    fake = _FakeTime()
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    monkeypatch.setattr(live_session_drainer, "drain_run_buffer", drain)
    # The module's own names only: the event loop keeps the real clock / sleep.
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(tasks, "asyncio", SimpleNamespace(sleep=fake.sleep))
    return fake


async def _drain():
    from app.worker import tasks

    return await tasks._drain_live_evidence_before_finalize(
        run_id="zz-run-q",
        project_id=str(uuid.uuid4()),
        build_number="7",
        suite_name=None,
    )


_PEER_HOLDS_LOCK = {"drained": 0, "lock_held": False, "run_uuid": None}


@pytest.mark.asyncio
async def test_the_close_drain_waits_for_a_peer_drainer_then_drains_and_returns(monkeypatch):
    from app.worker import tasks

    drain = AsyncMock(
        side_effect=[
            dict(_PEER_HOLDS_LOCK),
            dict(_PEER_HOLDS_LOCK),
            dict(_PEER_HOLDS_LOCK),
            {"drained": 7, "lock_held": True},
            {"drained": 0, "lock_held": True},
        ]
    )
    fake = _wire(monkeypatch, redis=_LengthsRedis(stream=0), drain=drain)

    assert await _drain() == 7
    assert fake.sleeps == [tasks.DRAIN_LOCK_POLL_SECONDS] * 3
    assert drain.await_count == 5


@pytest.mark.asyncio
async def test_a_peer_holding_the_lock_past_the_budget_defers_without_failure(monkeypatch):
    from app.worker import tasks

    drain = AsyncMock(return_value=dict(_PEER_HOLDS_LOCK))
    fake = _wire(monkeypatch, redis=_LengthsRedis(stream=3), drain=drain)

    with pytest.raises(tasks.LiveEvidenceDrainBusy) as raised:
        await _drain()

    assert raised.value.defer_downstream_without_failure is True
    assert isinstance(raised.value, RuntimeError)
    expected_polls = int(tasks.DRAIN_LOCK_WAIT_SECONDS / tasks.DRAIN_LOCK_POLL_SECONDS)
    assert len(fake.sleeps) == expected_polls == 60
    assert sum(fake.sleeps) == pytest.approx(tasks.DRAIN_LOCK_WAIT_SECONDS)


@pytest.mark.asyncio
async def test_a_peer_past_the_budget_with_nothing_left_finalizes(monkeypatch):
    """The deferral is only for evidence that would otherwise be stranded."""
    drain = AsyncMock(return_value=dict(_PEER_HOLDS_LOCK))
    _wire(monkeypatch, redis=_LengthsRedis(stream=0, legacy=0), drain=drain)

    assert await _drain() == 0


@pytest.mark.asyncio
async def test_lock_free_with_entries_left_is_still_the_crashed_consumer_error(monkeypatch):
    from app.worker import tasks

    drain = AsyncMock(return_value={"drained": 0, "lock_held": True})
    fake = _wire(monkeypatch, redis=_LengthsRedis(stream=2), drain=drain)

    with pytest.raises(RuntimeError, match="2 uncommitted") as raised:
        await _drain()

    assert not isinstance(raised.value, tasks.LiveEvidenceDrainBusy)
    assert not getattr(raised.value, "defer_downstream_without_failure", False)
    assert fake.sleeps == []


def test_a_busy_drain_returns_the_persist_to_the_outbox_without_spending_an_attempt(
    monkeypatch,
):
    """Through ``DownstreamTrackedTask`` and the REAL ``self.retry`` (an eager,
    worker-shaped request raises ``Retry`` without publishing anything)."""
    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    calls: list[tuple] = []

    def _run_async(value):
        return asyncio.run(value) if asyncio.iscoroutine(value) else value

    monkeypatch.setattr(tasks, "_run_async", _run_async)
    monkeypatch.setattr(
        tasks,
        "_drain_live_evidence_before_finalize",
        AsyncMock(side_effect=tasks.LiveEvidenceDrainBusy("zz-busy-q")),
    )
    monkeypatch.setattr(
        service, "begin_downstream_execution", lambda **_k: calls.append(("begin",)) or True
    )
    monkeypatch.setattr(
        service, "complete_downstream_execution", lambda **_k: calls.append(("complete",)) or True
    )
    monkeypatch.setattr(
        service,
        "fail_downstream_execution",
        lambda **kwargs: calls.append(("fail", kwargs["error_code"])) or True,
    )
    monkeypatch.setattr(
        service,
        "defer_downstream_execution",
        lambda **kwargs: calls.append(("defer", kwargs["reason"])) or True,
    )

    task = tasks.persist_live_session
    outbox_id = uuid.uuid4()
    task.push_request(
        id="zz-persist-q",
        retries=0,
        called_directly=False,
        is_eager=True,
        headers={
            "downstream_outbox_id": str(outbox_id),
            "downstream_dispatch_token": str(uuid.uuid4()),
        },
    )
    try:
        result = task(
            run_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            build_number="7",
        )
    finally:
        task.pop_request()

    assert result == {"deferred": True, "downstream_outbox_id": str(outbox_id)}
    assert calls == [("begin",), ("defer", "task_LiveEvidenceDrainBusy")]
