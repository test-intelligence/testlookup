"""Real PostgreSQL proof that a rejected middle row does not abort its batch."""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_valid_invalid_valid_batch_commits_both_valid_rows():
    from app.services import ingestion_pipeline as pipeline

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await db.execute(
                text(
                    "CREATE TEMP TABLE m10_savepoint_probe "
                    "(value integer PRIMARY KEY)"
                )
            )
            await db.execute(text("INSERT INTO m10_savepoint_probe VALUES (0)"))

            run = SimpleNamespace(
                id=uuid.uuid4(), project_id=uuid.uuid4(), framework="junit"
            )
            results = [
                {"test_name": "first", "status": "passed", "suite_name": "smoke"},
                {"test_name": "bad", "status": "passed", "suite_name": "smoke"},
                {"test_name": "last", "status": "passed", "suite_name": "smoke"},
            ]

            async def _probe_upsert(session, case, _run, **_kwargs):
                value = {"first": 1, "bad": 0, "last": 2}[case["test_name"]]
                await session.execute(
                    text("INSERT INTO m10_savepoint_probe VALUES (:value)"),
                    {"value": value},
                )
                return SimpleNamespace(test_fingerprint=case["test_name"])

            with patch.object(pipeline, "_upsert_test_case", side_effect=_probe_upsert):
                accepted = await pipeline.ingest_test_results(db, run, results)

            await db.commit()
            values = list(
                (
                    await db.execute(
                        text("SELECT value FROM m10_savepoint_probe ORDER BY value")
                    )
                ).scalars()
            )

        assert accepted == 2
        assert values == [0, 1, 2]
        assert run.ingestion_rejected_tests == 1
        assert run.ingestion_complete is False
    finally:
        await engine.dispose()
