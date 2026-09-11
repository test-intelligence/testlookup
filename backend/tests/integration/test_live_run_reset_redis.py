"""H6 batch 5: the legacy run_start reset is one atomic step, on real Redis.

``RedisLiveRunState.start(reset=True)`` used to ask EXISTS, then HGET status,
then replace the state. Two run_starts for a COMPLETED run could both see
"completed": the first replaced the state, a result was counted, and the second
replaced it again and wiped that result. It is now one Lua script.

Each trial below plants a completed run, then races two resetting run_starts
against a result that is counted once the new run is visible. Every Redis
command gets a small random delay first, so the old round trips interleave the
way they do across API workers. The result must survive every trial.

Keys are unique per test (a random run id) and removed afterwards.
Requires ``REDIS_URL``.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import random
import uuid

import pytest

from app.streams import LIVE_ACTIVE_SET
from app.streams import live_run_state
from app.streams.live_run_state import RedisLiveRunState, _STATE_KEY

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TRIALS = 60


class _Jittered:
    """Delays every command a little, as a busy network and event loop do."""

    def __init__(self, client, rng: random.Random) -> None:
        self._client = client
        self._rng = rng

    async def _pause(self) -> None:
        # Heavy-tailed, like a busy worker: usually prompt, sometimes a whole
        # scheduling slice late. A uniform delay keeps two resetters in step,
        # and in step they never interleave the way that loses a result.
        if self._rng.random() < 0.3:
            await asyncio.sleep(0.008)
        else:
            await asyncio.sleep(self._rng.uniform(0, 0.001))

    def pipeline(self, *args, **kwargs):
        pipe = self._client.pipeline(*args, **kwargs)
        execute = pipe.execute

        async def _execute(*a, **kw):
            await self._pause()
            return await execute(*a, **kw)

        pipe.execute = _execute
        return pipe

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        # redis.asyncio commands are plain methods that RETURN a coroutine
        # (``iscoroutinefunction`` is False for eval, hget, exists...), so
        # wrap every call and delay whatever turns out to be awaitable.
        async def _call(*args, **kwargs):
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                await self._pause()
                return await result
            return result

        return _call


@pytest.fixture
async def redis_client():
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


async def _plant_completed(client, run_id: str) -> None:
    key = _STATE_KEY(run_id)
    await client.delete(key)
    await client.hset(key, mapping={
        "run_id": run_id, "project_id": "p", "build_number": "1", "total": 7,
        "passed": 0, "failed": 7, "skipped": 0, "broken": 0, "unknown": 0,
        "status": "completed", "suite_name": "previous-suite",
    })
    await client.expire(key, 600)


async def test_two_resets_and_a_result_never_lose_the_result(redis_client, monkeypatch):
    rng = random.Random(20260911)
    monkeypatch.setattr(live_run_state, "get_redis", lambda: _Jittered(redis_client, rng))
    run_id = f"h6-b5-reset-{uuid.uuid4().hex}"
    key = _STATE_KEY(run_id)
    lost = []
    try:
        for trial in range(TRIALS):
            await _plant_completed(redis_client, run_id)

            async def _reset():
                await RedisLiveRunState.start(run_id, "p", "2", reset=True)

            async def _count_once_the_new_run_is_visible():
                # Straight to Redis, unjittered: the count lands in the window
                # between the first reset and any second one.
                while await redis_client.hget(key, "status") != "running":
                    await asyncio.sleep(0)
                await redis_client.hincrby(key, "failed", 1)

            await asyncio.wait_for(
                asyncio.gather(_reset(), _reset(), _count_once_the_new_run_is_visible()),
                timeout=10,
            )
            state = await redis_client.hgetall(key)
            if state.get("failed") != "1" or state.get("status") != "running":
                lost.append((trial, state.get("failed"), state.get("status")))
            assert "suite_name" not in state, "the reset kept a field from the last run"
        assert lost == [], (
            f"a second reset wiped a result counted after the first, in "
            f"{len(lost)}/{TRIALS} trials: {lost[:5]}"
        )
    finally:
        await redis_client.delete(key)
        await redis_client.srem(LIVE_ACTIVE_SET, run_id)


async def test_concurrent_creates_do_not_zero_each_other(redis_client, monkeypatch):
    """The create path had the same gap: both saw no key, the later HSET zeroed."""
    rng = random.Random(911)
    monkeypatch.setattr(live_run_state, "get_redis", lambda: _Jittered(redis_client, rng))
    run_id = f"h6-b5-create-{uuid.uuid4().hex}"
    key = _STATE_KEY(run_id)
    lost = []
    try:
        for trial in range(TRIALS):
            await redis_client.delete(key)

            async def _create():
                await RedisLiveRunState.start(run_id, "p", "2")

            async def _count_once_created():
                while not await redis_client.exists(key):
                    await asyncio.sleep(0)
                await redis_client.hincrby(key, "passed", 1)

            await asyncio.wait_for(
                asyncio.gather(_create(), _create(), _count_once_created()), timeout=10
            )
            passed = await redis_client.hget(key, "passed")
            if passed != "1":
                lost.append((trial, passed))
        assert lost == [], f"a second create zeroed a counted result: {lost[:5]}"
    finally:
        await redis_client.delete(key)
        await redis_client.srem(LIVE_ACTIVE_SET, run_id)


class _Recording:
    """Counts every command start() sends; a pipeline counts as one."""

    def __init__(self, client) -> None:
        self._client = client
        self.commands: list[str] = []

    def pipeline(self, *args, **kwargs):
        pipe = self._client.pipeline(*args, **kwargs)
        execute = pipe.execute

        async def _execute(*a, **kw):
            self.commands.append("pipeline")
            return await execute(*a, **kw)

        pipe.execute = _execute
        return pipe

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        async def _call(*args, **kwargs):
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                self.commands.append(name)
                return await result
            return result

        return _call


@pytest.mark.parametrize("planted", ["none", "running", "completed"])
@pytest.mark.parametrize("reset", [False, True])
async def test_start_decides_and_writes_in_one_round_trip(redis_client, monkeypatch, planted, reset):
    """Atomic means one command. Any check made in a round trip of its own --
    EXISTS, HGET status -- leaves a gap another run_start can act in, however
    the write after it is batched. This is the deterministic form of the race
    test above, whose interleaving is only likely."""
    recorder = _Recording(redis_client)
    monkeypatch.setattr(live_run_state, "get_redis", lambda: recorder)
    run_id = f"h6-b5-one-{uuid.uuid4().hex}"
    key = _STATE_KEY(run_id)
    try:
        if planted != "none":
            await redis_client.hset(key, mapping={"status": planted, "failed": 3})
        await RedisLiveRunState.start(run_id, "p", "1", reset=reset)
        assert recorder.commands == ["eval"], recorder.commands
        expected_failed = "0" if planted == "none" or (reset and planted == "completed") else "3"
        assert await redis_client.hget(key, "failed") == expected_failed
    finally:
        await redis_client.delete(key)
        await redis_client.srem(LIVE_ACTIVE_SET, run_id)


async def test_a_run_in_progress_is_kept_and_a_completed_one_replaced(redis_client, monkeypatch):
    monkeypatch.setattr(live_run_state, "get_redis", lambda: redis_client)
    run_id = f"h6-b5-keep-{uuid.uuid4().hex}"
    key = _STATE_KEY(run_id)
    try:
        await RedisLiveRunState.start(run_id, "p", "1")
        await RedisLiveRunState.record_test_event(run_id, "FAILED", "a", return_state=False)
        await RedisLiveRunState.start(run_id, "p", "1", reset=True)
        assert await redis_client.hget(key, "failed") == "1", "a run in progress was wiped"

        await redis_client.hset(key, "status", "completed")
        await RedisLiveRunState.start(run_id, "p", "1")  # the consumer: never resets
        assert await redis_client.hget(key, "failed") == "1"

        await RedisLiveRunState.start(run_id, "p", "2", reset=True)
        assert await redis_client.hget(key, "failed") == "0"
        assert await redis_client.hget(key, "status") == "running"
        assert 0 < await redis_client.ttl(key) <= 86_400
        assert await redis_client.sismember(LIVE_ACTIVE_SET, run_id)
    finally:
        await redis_client.delete(key)
        await redis_client.srem(LIVE_ACTIVE_SET, run_id)
