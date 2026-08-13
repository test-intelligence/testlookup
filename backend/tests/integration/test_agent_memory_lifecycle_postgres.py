"""Opt-in PostgreSQL lifecycle/retention coverage for Agent Memory."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_memory_expiry_supersession_and_rollback_are_concurrent_safe():
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    ids = [uuid.uuid4() for _ in range(3)]
    invalid_id = uuid.uuid4()
    try:
        async with engine.begin() as db:
            row = (
                await db.execute(
                    text("SELECT id, project_id FROM test_runs ORDER BY created_at LIMIT 1")
                )
            ).first()
            if row is None:
                pytest.skip("homelab database has no test run fixture")
            run_id, project_id = row

            now = datetime.now(timezone.utc)
            for entry_id, entity_id, expires_at in (
                (ids[0], "expired", now - timedelta(minutes=5)),
                (ids[1], "replacement", now + timedelta(days=30)),
                (ids[2], "current", now + timedelta(days=30)),
            ):
                await db.execute(
                    text(
                        "INSERT INTO agent_memory_entries "
                        "(id, project_id, run_id, entity_type, entity_id, "
                        "source_type, trust_level, lifecycle_status, payload, expires_at) "
                        "VALUES (:id,:project,:run_id,'cluster',:entity,'pipeline_agent',"
                        "'derived','active','{}'::jsonb,:expires_at)"
                    ),
                    {
                        "id": entry_id,
                        "project": project_id,
                        "run_id": run_id,
                        "entity": entity_id,
                        "expires_at": expires_at,
                    },
                )

            await db.execute(
                text(
                    "UPDATE agent_memory_entries SET lifecycle_status='superseded', "
                    "superseded_by_id=:replacement, superseded_at=:now "
                    "WHERE id=:current AND project_id=:project"
                ),
                {
                    "replacement": ids[1],
                    "current": ids[2],
                    "project": project_id,
                    "now": now,
                },
            )

        async def expire_once() -> int:
            async with engine.begin() as db:
                result = await db.execute(
                    text(
                        "UPDATE agent_memory_entries SET lifecycle_status='expired' "
                        "WHERE id=:id AND lifecycle_status='active' AND expires_at <= :now"
                    ),
                    {"id": ids[0], "now": datetime.now(timezone.utc)},
                )
                return int(result.rowcount or 0)

        assert sum(await asyncio.gather(expire_once(), expire_once())) == 1

        # A lifecycle CHECK violation must roll back while leaving the session
        # usable for a valid write.
        async with engine.connect() as db:
            tx = await db.begin()
            with pytest.raises(Exception, match="check constraint"):
                await db.execute(
                    text(
                        "INSERT INTO agent_memory_entries "
                        "(id,project_id,run_id,entity_type,entity_id,source_type,trust_level,lifecycle_status,payload) "
                        "VALUES (:id,:project,:run_id,'cluster','invalid','pipeline_agent','derived','corrupted','{}'::jsonb)"
                    ),
                    {"id": invalid_id, "project": project_id, "run_id": run_id},
                )
            await tx.rollback()
            status = await db.execute(
                text("SELECT lifecycle_status FROM agent_memory_entries WHERE id=:id"),
                {"id": ids[0]},
            )
            assert status.scalar_one() == "expired"
    finally:
        async with engine.begin() as db:
            for entry_id in ids + [invalid_id]:
                await db.execute(
                    text("DELETE FROM agent_memory_entries WHERE id=:id"),
                    {"id": entry_id},
                )
        await engine.dispose()
