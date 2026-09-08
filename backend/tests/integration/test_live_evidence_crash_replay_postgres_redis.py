"""Real PostgreSQL/Redis proof for post-commit live-evidence replay."""
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


async def test_postgres_commit_then_redis_cleanup_crash_replays_once(monkeypatch):
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("REDIS_URL is not configured")

    redis_asyncio = pytest.importorskip("redis.asyncio")
    from app.db import postgres
    from app.models.postgres import (
        LiveEventReceipt,
        LiveIngestionAttempt,
        Project,
        TestCase,
        TestRun,
    )
    from app.services import high_volume_detector, live_session_drainer, stream_service
    from app.streams import LIVE_BATCH_DEDUP_KEY, LIVE_EVIDENCE_STREAM_KEY, LIVE_STATE_KEY
    from app.streams import live_run_state

    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    stream_key = LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id)
    dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)

    class _CrashBeforeCleanup:
        def __init__(self, delegate):
            self.delegate = delegate
            self.crashed = False

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        async def eval(self, script, *args):
            if "XPENDING" in script and not self.crashed:
                self.crashed = True
                raise RuntimeError("simulated crash after PostgreSQL commit")
            return await self.delegate.eval(script, *args)

    try:
        monkeypatch.setattr(postgres, "get_session_factory", lambda: sessions)
        monkeypatch.setattr(live_session_drainer, "AsyncSessionLocal", sessions)
        async def _record_test_events(_project_id: str, _count: int) -> None:
            return None

        monkeypatch.setattr(
            high_volume_detector,
            "record_test_events",
            _record_test_events,
        )
        async with sessions() as db:
            db.add(Project(
                id=project_id,
                name=f"live replay {project_id.hex}",
                slug=f"live-replay-{project_id.hex}",
                is_active=True,
            ))
            await db.commit()

        monkeypatch.setattr(stream_service, "get_redis", lambda: client)
        monkeypatch.setattr(live_run_state, "get_redis", lambda: client)
        await client.hset(LIVE_STATE_KEY.format(run_id=run_id), mapping={
            "run_id": str(run_id), "project_id": str(project_id),
            "build_number": "crash-replay", "passed": 0, "failed": 0,
            "skipped": 0, "broken": 0, "unknown": 0, "total": 0,
        })
        assert await stream_service._persist_event_batch(
            str(run_id), str(run_id),
            [{"event_type": "test_result", "test_name": "replayed", "status": "PASSED"}],
            batch_id="crash-replay-batch",
        ) == 1

        crashing = _CrashBeforeCleanup(client)
        monkeypatch.setattr(live_session_drainer, "get_redis", lambda: crashing)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await live_session_drainer.drain_run_buffer(
                str(run_id), str(project_id), build_number="crash-replay"
            )
        assert await client.xlen(stream_key) == 1
        await client.hset(dedupe_key, "__gate__", "closed")

        monkeypatch.setattr(live_session_drainer, "get_redis", lambda: client)
        result = await live_session_drainer.drain_run_buffer(
            str(run_id), str(project_id), build_number="crash-replay"
        )
        assert result["drained"] == 1
        assert await client.xlen(stream_key) == 0
        assert await client.scard(f"{dedupe_key}:pending") == 0
        assert await client.ttl(dedupe_key) > 0
        assert await client.ttl(f"{dedupe_key}:passed") > 0
        receipt = json.loads(await client.hget(
            dedupe_key,
            stream_service._dedupe_field(str(run_id), "crash-replay-batch"),
        ))
        assert receipt["state"] == "projected"

        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(TestCase).where(
                TestCase.test_run_id == run_id
            )) == 1
            assert await db.scalar(select(func.count()).select_from(LiveEventReceipt).where(
                LiveEventReceipt.run_id == run_id
            )) == 1
            assert await db.scalar(select(func.count()).select_from(LiveIngestionAttempt).where(
                LiveIngestionAttempt.run_id == run_id
            )) == 1
    finally:
        try:
            try:
                await client.delete(
                    stream_key,
                    dedupe_key,
                    LIVE_STATE_KEY.format(run_id=run_id),
                    f"testlookup:live:testcases:{run_id}",
                    f"testlookup:live:drain_lock:{run_id}",
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
