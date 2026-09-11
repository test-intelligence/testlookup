"""Re-audit M13 and M12 against a real Redis (and, for M13, a real Postgres).

M13: the monthly cap was check-then-act. Ten concurrent calls that each read
"$0.90 of $1.00" all proceeded. Here ten reservations race over ten
independent Redis connections against a real quota row, and exactly as many
as fit under the cap get through.

M12: the concurrency bound was one asyncio.Semaphore per process. Here four
OS processes, each with its own Redis client, share a limit of 2 and the
observed concurrency never passes it; a holder that dies without releasing
frees its slot when its lease lapses; a live holder keeps its slot past the
lease by renewing it.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL`` (migrated to head).
"""
from __future__ import annotations

import asyncio
import itertools
import multiprocessing
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


# ── M13 ─────────────────────────────────────────────────────────────────────

CAP_USD = 1.00
COMMITTED_USD = 0.90
# gpt-4 input is $30/Mtok: 1,000 input tokens and no output is exactly $0.03.
PROVIDER, MODEL, INPUT_TOKENS, OUTPUT_TOKENS = "openai", "gpt-4", 1000, 0


@pytest.fixture
async def capped_project(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.services.feature_flags as flags
    from app.db import postgres as app_postgres
    from app.models.postgres import Project, ProjectLlmQuota, ProjectLlmUsage
    from app.services import llm_cost_reservation as cost
    from app.services.llm_cost_budget import current_period_bounds

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=12, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis_url = _env("REDIS_URL")
    clients = [redis_asyncio.Redis.from_url(redis_url, decode_responses=True) for _ in range(10)]
    rotation = itertools.cycle(clients)

    tag = uuid.uuid4().hex[:10]
    prefix = f"testlookup:test:llmcap:{tag}"
    monkeypatch.setattr(cost, "KEY_PREFIX", prefix)
    monkeypatch.setattr(cost, "_redis", lambda: next(rotation))
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    async def flag_on(key, **kwargs):
        return key == "llm_cost_budget"

    monkeypatch.setattr(flags, "is_enabled", flag_on)

    project_id = uuid.uuid4()
    period_start, period_end = current_period_bounds()
    async with sessions() as db:
        db.add(Project(id=project_id, name=f"m13-{tag}", slug=f"m13-{tag}", is_active=True))
        await db.flush()
        db.add(ProjectLlmQuota(project_id=project_id, enabled=True, hard_cap_usd=CAP_USD))
        db.add(ProjectLlmUsage(
            project_id=project_id, period_start=period_start, period_end=period_end,
            total_cost_usd=COMMITTED_USD, total_input_tokens=0, total_output_tokens=0,
            total_llm_calls=0, cap_hits=0, last_updated_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    key = cost.counter_key(project_id, period_start)
    try:
        yield project_id, key, clients[0]
    finally:
        await clients[0].delete(key)
        async with sessions() as db:
            await db.execute(delete(ProjectLlmUsage).where(ProjectLlmUsage.project_id == project_id))
            await db.execute(delete(ProjectLlmQuota).where(ProjectLlmQuota.project_id == project_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        for client in clients:
            await client.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _reserve_once():
    from app.services import llm_cost_reservation as cost

    try:
        return await cost.reserve(
            PROVIDER, MODEL, input_tokens=INPUT_TOKENS, max_output_tokens=OUTPUT_TOKENS,
        )
    except cost.CostCapExceeded:
        return None


async def test_concurrent_reservations_cannot_pass_the_cap(capped_project):
    from app.services import llm_cost_reservation as cost

    project_id, key, redis = capped_project
    assert cost.price(PROVIDER, MODEL, INPUT_TOKENS, OUTPUT_TOKENS) == pytest.approx(0.03)
    with cost.cost_budget_scope(project_id):
        results = await asyncio.gather(*(_reserve_once() for _ in range(10)))
    granted = [r for r in results if r is not None]
    # $0.90 committed + 3 x $0.03 = $0.99; a fourth would be $1.02.
    assert len(granted) == 3
    assert float(await redis.get(key)) == pytest.approx(0.99)


async def test_settling_releases_the_unused_part_and_a_refusal_follows_the_cap(capped_project):
    from app.services import llm_cost_reservation as cost

    project_id, key, redis = capped_project
    with cost.cost_budget_scope(project_id):
        first = await _reserve_once()
        await cost.settle(first, 0.01)  # the call cost $0.01 of the $0.03 held
        assert float(await redis.get(key)) == pytest.approx(0.91)
        held = [await _reserve_once() for _ in range(4)]
    # 0.91 + 3 x 0.03 = 1.00 fits exactly; the fourth does not.
    assert [r is not None for r in held] == [True, True, True, False]
    await cost.settle(held[0], 0.0)  # a failed call releases everything it held
    assert float(await redis.get(key)) == pytest.approx(0.97)


async def test_a_lost_counter_heals_from_the_durable_meter(capped_project):
    from app.services import llm_cost_reservation as cost

    project_id, key, redis = capped_project
    await redis.delete(key)
    with cost.cost_budget_scope(project_id):
        assert await _reserve_once() is not None
    # Seeded from Postgres ($0.90), not from zero.
    assert float(await redis.get(key)) == pytest.approx(0.93)
    assert 0 < await redis.ttl(key)


# ── M12 ─────────────────────────────────────────────────────────────────────


def _slot_worker(redis_url, prefix, name, limit, rounds, hold, gauge, out):
    async def run():
        import redis.asyncio as redis_asyncio

        from app.services import llm_cluster_semaphore as slots

        slots.KEY_PREFIX = prefix
        client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
        semaphore = slots.ClusterSemaphore(name, limit, lease_seconds=5, redis=client)
        observed = []
        try:
            for _ in range(rounds):
                async with semaphore.slot(timeout=30) as held:
                    assert held is True
                    observed.append(await client.incr(gauge))
                    await asyncio.sleep(hold)
                    await client.decr(gauge)
        finally:
            await client.aclose()
        out.put(observed)

    asyncio.run(run())


def _crashing_holder(redis_url, prefix, name, ready):
    async def run():
        import redis.asyncio as redis_asyncio

        from app.services import llm_cluster_semaphore as slots

        slots.KEY_PREFIX = prefix
        client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
        semaphore = slots.ClusterSemaphore(name, 2, lease_seconds=1.0, redis=client)
        assert await semaphore.try_acquire("crashed-holder")
        ready.put(time.monotonic())

    asyncio.run(run())
    os._exit(0)  # die holding the slot: no release, no heartbeat


@pytest.fixture
async def slots_redis():
    redis_asyncio = pytest.importorskip("redis.asyncio")
    url = _env("REDIS_URL")
    client = redis_asyncio.Redis.from_url(url, decode_responses=True)
    prefix = f"testlookup:test:llmslots:{uuid.uuid4().hex[:10]}"
    try:
        yield url, prefix, client
    finally:
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
        await client.aclose()


async def test_four_processes_never_exceed_the_cluster_limit(slots_redis):
    url, prefix, client = slots_redis
    gauge = f"{prefix}:gauge"
    context = multiprocessing.get_context("spawn")
    out = context.Queue()
    processes = [
        context.Process(target=_slot_worker, args=(url, prefix, "ollama", 2, 3, 0.15, gauge, out))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    try:
        observed = [await asyncio.to_thread(out.get, True, 60) for _ in processes]
    finally:
        for process in processes:
            process.join(timeout=10)
    flat = [value for per_process in observed for value in per_process]
    assert len(flat) == 12
    assert max(flat) == 2, f"concurrency observed: {sorted(flat)}"


async def test_a_crashed_holder_frees_its_slot_when_the_lease_lapses(slots_redis):
    """Beside a LIVE holder: its renewals keep the whole key alive, so the dead
    holder's slot can only come back by its own lease being dropped -- not by
    the key expiring once every holder has gone quiet."""
    from app.services import llm_cluster_semaphore as slots

    url, prefix, client = slots_redis
    original = slots.KEY_PREFIX
    slots.KEY_PREFIX = prefix
    try:
        live = slots.ClusterSemaphore("vllm", 2, lease_seconds=1.0, redis=client)
        async with live.slot(timeout=5) as held:
            assert held is True
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            holder = context.Process(target=_crashing_holder, args=(url, prefix, "vllm", ready))
            holder.start()
            await asyncio.to_thread(ready.get, True, 30)
            holder.join(timeout=10)
            assert holder.exitcode == 0

            waiter = slots.ClusterSemaphore("vllm", 2, lease_seconds=1.0, redis=client)
            assert await waiter.try_acquire("waiter") is False  # live + dead fill both slots
            started = time.monotonic()
            while not await waiter.try_acquire("waiter"):
                assert time.monotonic() - started < 5, "the crashed holder's slot never freed"
                await asyncio.sleep(0.05)
            assert await client.zscore(waiter.key, "crashed-holder") is None
            # The live holder kept its slot throughout: live + waiter.
            assert await client.zcard(waiter.key) == 2
    finally:
        slots.KEY_PREFIX = original


async def test_a_live_holder_keeps_its_slot_past_the_lease(slots_redis):
    from app.services import llm_cluster_semaphore as slots

    url, prefix, client = slots_redis
    original = slots.KEY_PREFIX
    slots.KEY_PREFIX = prefix
    try:
        semaphore = slots.ClusterSemaphore("gemini", 1, lease_seconds=0.6, redis=client)
        other = slots.ClusterSemaphore("gemini", 1, lease_seconds=0.6, redis=client)
        async with semaphore.slot(timeout=5) as held:
            assert held is True
            await asyncio.sleep(1.5)  # 2.5 leases: only renewal keeps it
            assert await other.try_acquire("intruder") is False
        # Released on exit, not left to lapse.
        assert await other.try_acquire("intruder") is True
        with pytest.raises(slots.LLMSlotTimeout):
            async with semaphore.slot(timeout=0.3):
                pass
    finally:
        slots.KEY_PREFIX = original
