"""Redis/PostgreSQL proofs for archive-recovery admission and cleanup."""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_archive_recovery_repairs_a_post_manifest_admission_failure(monkeypatch):
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")

    redis_asyncio = pytest.importorskip("redis.asyncio")
    from app.services import live_run_recovery_service as recovery
    from app.streams import LIVE_BATCH_DEDUP_KEY, LIVE_EVIDENCE_STREAM_KEY

    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    run_id = str(uuid.uuid4())
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id)
    dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
    original_lua = recovery._RECOVERY_BATCH_LUA
    failure_point = "for i = 1, #event_ids do"
    assert failure_point in original_lua
    broken_lua = original_lua.replace(
        failure_point,
        "error('injected post-manifest failure')\n" + failure_point,
        1,
    )

    try:
        monkeypatch.setattr(recovery, "get_redis", lambda: client)
        monkeypatch.setattr(recovery, "_RECOVERY_BATCH_LUA", broken_lua)
        with pytest.raises(Exception, match="injected post-manifest failure"):
            await recovery._stage_archive_to_redis(
                run_id,
                [{"test_name": "crash-repair", "status": "PASSED"}],
            )

        assert await client.xlen(stream_key) == 1
        fields = await client.hgetall(dedupe_key)
        dedupe_field = fields["__admitting__"]
        admitting = json.loads(fields[dedupe_field])
        assert admitting["state"] == "staged"

        monkeypatch.setattr(recovery, "_RECOVERY_BATCH_LUA", original_lua)
        assert await recovery._stage_archive_to_redis(
            run_id,
            [{"test_name": "crash-repair", "status": "PASSED"}],
        ) == 1

        assert await client.xlen(stream_key) == 1
        fields = await client.hgetall(dedupe_key)
        receipt = json.loads(fields[dedupe_field])
        assert receipt["state"] == "accepted"
        assert await client.scard(f"{dedupe_key}:pending") == 1
        assert "__admitting__" not in fields
    finally:
        try:
            await client.delete(
                stream_key,
                dedupe_key,
                *[f"{dedupe_key}:{suffix}" for suffix in (
                    "pending", "passed", "failed", "skipped", "broken",
                    "unknown", "received",
                )],
            )
        finally:
            await client.aclose()


async def test_archive_recovery_stage_drain_and_cleanup(monkeypatch):
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")

    redis_asyncio = pytest.importorskip("redis.asyncio")
    from app.models.postgres import Project, TestCase, TestRun
    from app.services import live_run_recovery_service as recovery
    from app.services import live_session_drainer
    from app.streams import LIVE_BATCH_DEDUP_KEY, LIVE_EVIDENCE_STREAM_KEY
    from app.streams import live_run_state

    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id)
    dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)

    try:
        monkeypatch.setattr(live_session_drainer, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(live_run_state, "get_redis", lambda: client)
        async with sessions() as db:
            db.add(Project(
                id=project_id,
                name=f"recovery cleanup {project_id.hex}",
                slug=f"recovery-cleanup-{project_id.hex}",
                is_active=True,
            ))
            await db.commit()

        monkeypatch.setattr(recovery, "get_redis", lambda: client)
        assert await recovery._stage_archive_to_redis(
            str(run_id),
            [{"test_name": "recovered", "status": "PASSED"}],
        ) == 1

        fields = await client.hgetall(dedupe_key)
        dedupe_field = next(key for key in fields if key.startswith("batch:"))
        pending_key = f"{dedupe_key}:pending"
        assert await client.scard(pending_key) == 1

        monkeypatch.setattr(live_session_drainer, "get_redis", lambda: client)
        result = await live_session_drainer.drain_run_buffer(
            str(run_id),
            str(project_id),
            build_number="archive-recovery",
        )
        assert result["drained"] == 1
        assert await client.xlen(stream_key) == 0

        fields = await client.hgetall(dedupe_key)
        assert json.loads(fields[dedupe_field])["state"] == "projected"
        assert await client.scard(pending_key) == 0
        ttl = await client.ttl(dedupe_key)
        assert 0 < ttl <= recovery._RECOVERY_DEDUPE_TTL_SECONDS

        async with sessions() as db:
            assert await db.scalar(
                select(func.count()).select_from(TestCase).where(
                    TestCase.test_run_id == run_id
                )
            ) == 1
    finally:
        try:
            try:
                await client.delete(
                    stream_key,
                    dedupe_key,
                    f"testlookup:live:testcases:{run_id}",
                    f"testlookup:live:drain_lock:{run_id}",
                    *[f"{dedupe_key}:{suffix}" for suffix in (
                        "pending", "passed", "failed", "skipped", "broken",
                        "unknown", "received",
                    )],
                )
            finally:
                async with sessions() as db:
                    await db.execute(delete(TestRun).where(TestRun.id == run_id))
                    await db.execute(delete(Project).where(Project.id == project_id))
                    await db.commit()
        finally:
            try:
                await client.aclose()
            finally:
                await engine.dispose()
