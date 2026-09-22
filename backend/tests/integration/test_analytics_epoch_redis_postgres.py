"""VIZ-212 on real Redis (and, when configured, real PostgreSQL).

The unit tests prove the epoch logic against an in-memory fake. What only a real
server can answer:

* An ``INCR``-created counter really has no TTL (``TTL`` = -1). Redis runs
  ``volatile-lru``, which evicts only keys that have one; a counter with a TTL
  could be evicted and restart at 0, re-validating every entry cached under
  epoch 0.
* ``SCAN MATCH analytics:*`` really does not match ``analytics_epoch:*``, so
  the legacy invalidator cannot reset a counter.
* End to end on PostgreSQL + Redis: a cached dashboard summary for a real
  project changes after a real ``reset_project`` commits.

Requires ``REDIS_URL`` (skips without it); the PostgreSQL test also requires
``TESTLOOKUP_POSTGRES_TEST_DSN``. Every key and row is unique to the test and
removed afterwards. The shared ``analytics_epoch:__all__`` counter is only ever
incremented, never deleted — deleting it would reset it for everyone.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def redis_client(monkeypatch):
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")
    redis_asyncio = pytest.importorskip("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - an unreachable server is a skip
        await client.aclose()
        pytest.skip(f"Redis at REDIS_URL is unreachable: {exc}")
    # cache_service imports get_redis lazily, so this reaches every call.
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


async def _forget(client, project_id: str) -> None:
    keys = [f"analytics_epoch:{project_id}"]
    async for key in client.scan_iter(match=f"analytics:*:{project_id}:*"):
        keys.append(key)
    await client.delete(*keys)


async def test_epoch_counters_have_no_ttl(redis_client) -> None:
    from app.services import cache_service

    project_id = f"viz212-{uuid.uuid4().hex}"
    try:
        before_all = await cache_service.get_analytics_epoch(None)
        await cache_service.bump_analytics_epoch(project_id)
        await cache_service.bump_analytics_epoch(project_id)

        assert await cache_service.get_analytics_epoch(project_id) == 2
        assert await cache_service.get_analytics_epoch(None) >= (before_all or 0) + 2
        assert await redis_client.ttl(f"analytics_epoch:{project_id}") == -1
        assert await redis_client.ttl("analytics_epoch:__all__") == -1
    finally:
        await _forget(redis_client, project_id)


async def test_scan_delete_leaves_the_counter_and_the_bump_misses(redis_client) -> None:
    from app.services import cache_service

    project_id = f"viz212-{uuid.uuid4().hex}"
    try:
        epoch = await cache_service.get_analytics_epoch(project_id)
        assert epoch == 0
        await cache_service.cache_set("probe", {"v": 1}, project_id, ttl=60, epoch=epoch, d=7)
        assert await cache_service.cache_get("probe", project_id, epoch=epoch, d=7) == {"v": 1}
        entry = next(iter([k async for k in redis_client.scan_iter(
            match=f"analytics:probe:{project_id}:*"
        )]))
        assert 0 < await redis_client.ttl(entry) <= 60, "entries keep their TTL"

        await cache_service.invalidate_analytics_cache(project_id)

        assert await redis_client.exists(entry) == 0, "the legacy SCAN+DELETE still runs"
        assert await cache_service.get_analytics_epoch(project_id) == 1, (
            "the counter survived the SCAN+DELETE and was bumped"
        )
        new_epoch = await cache_service.get_analytics_epoch(project_id)
        assert await cache_service.cache_get("probe", project_id, epoch=new_epoch, d=7) is None
    finally:
        await _forget(redis_client, project_id)


@pytest.mark.parametrize(
    "stored",
    ["not-a-number", "-3", str(2**63 - 1)],
    ids=["garbage", "negative", "overflowed"],
)
async def test_a_corrupt_counter_is_repaired_on_a_real_server(redis_client, stored) -> None:
    """A real INCR on a garbage value errors ("not an integer"), at the 64-bit
    maximum errors ("would overflow"), on a negative value stays negative. In
    every case the pipeline must still bump ``__all__``, and the counter must
    come back as a fresh, usable, TTL-less epoch."""
    from app.services import cache_service

    project_id = f"viz212-{uuid.uuid4().hex}"
    key = f"analytics_epoch:{project_id}"
    try:
        await redis_client.set(key, stored)
        before_all = await cache_service.get_analytics_epoch(None)

        await cache_service.bump_analytics_epochs([project_id])

        assert await cache_service.get_analytics_epoch(None) > (before_all or 0), (
            "__all__ was not bumped because the project's INCR failed"
        )
        repaired = await cache_service.get_analytics_epoch(project_id)
        assert repaired is not None and 10**12 < repaired < 2**63 - 1
        assert await redis_client.ttl(key) == -1

        # The read path repairs too.
        await redis_client.set(key, stored)
        assert await cache_service.get_analytics_epoch(project_id) is None
        assert (await cache_service.get_analytics_epoch(project_id) or 0) > 10**12
        assert await redis_client.ttl(key) == -1
    finally:
        await _forget(redis_client, project_id)


# ── PostgreSQL + Redis: a cached summary follows a committed reset ───────────


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def factory():
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_cached_summary_changes_after_a_committed_reset(redis_client, factory) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import delete, text

    from app.models.postgres import LaunchStatus, Project, TestRun
    from app.services.metrics_service import get_dashboard_summary
    from app.services.project_reset_service import reset_project

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    name = f"viz212 epoch {token}"
    now = datetime.now(timezone.utc)
    async with factory() as db:
        db.add(Project(id=project_id, name=name, slug=f"viz212-{token}"))
        await db.flush()
        for n in range(2):
            db.add(TestRun(
                id=uuid.uuid4(), project_id=project_id,
                build_number=f"viz212-{token[:8]}-{n}", status=LaunchStatus.PASSED,
                total_tests=10, passed_tests=10, failed_tests=0,
                created_at=now, start_time=now,
            ))
        await db.commit()

    try:
        async with factory() as db:
            first = await get_dashboard_summary(db, str(project_id), days=7)
        async with factory() as db:
            cached = await get_dashboard_summary(db, str(project_id), days=7)
        assert first["total_executions_7d"]["value"] > 0
        assert cached == first

        async with factory() as db:
            await reset_project(
                db, project_id=project_id, mode="runs", confirmation_name=name
            )

        async with factory() as db:
            after = await get_dashboard_summary(db, str(project_id), days=7)
        assert after["total_executions_7d"]["value"] == 0, (
            "the summary still counts runs the reset deleted: the cache entry "
            "outlived the commit"
        )
    finally:
        async with factory() as db:
            await db.execute(delete(TestRun).where(TestRun.project_id == project_id))
            await db.execute(
                text(
                    "DELETE FROM settings_audit_log "
                    "WHERE changed_fields->>'project_id' = :pid"
                ),
                {"pid": str(project_id)},
            )
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await _forget(redis_client, str(project_id))
