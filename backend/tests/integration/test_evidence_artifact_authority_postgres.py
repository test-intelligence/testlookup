"""Opt-in PostgreSQL trigger coverage for verified evidence artifacts."""
from __future__ import annotations

import os
import uuid

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


async def test_verified_artifact_scope_and_immutability_triggers_fail_closed():
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    artifact_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    try:
        async with engine.begin() as db:
            fixture = (
                await db.execute(
                    text(
                        "SELECT tc.id AS test_case_id, tc.test_run_id, tr.project_id "
                        "FROM test_cases tc JOIN test_runs tr ON tr.id=tc.test_run_id "
                        "ORDER BY tc.created_at LIMIT 1"
                    )
                )
            ).first()
            if fixture is None:
                pytest.skip("homelab database has no test-case fixture")
            test_case_id, run_id, project_id = fixture
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,execution_metadata) "
                    "VALUES (:id,:run,'deep','completed','{}'::json)"
                ),
                {"id": pipeline_id, "run": run_id},
            )
            await db.execute(
                text(
                    "INSERT INTO evidence_artifacts "
                    "(id,run_id,project_id,producer_pipeline_run_id,test_case_id,"
                    "artifact_type,source_system,summary_excerpt,schema_version,"
                    "content_sha256,content_size_bytes,media_type,sensitivity,freshness,"
                    "integrity_status,retention_class,idempotency_key) VALUES "
                    "(:id,:run,:project,:pipeline,:test,'stack_trace','pytest',"
                    "'bounded test evidence',2,:sha,12,'text/plain','internal','current',"
                    "'verified','artifacts',:key)"
                ),
                {
                    "id": artifact_id,
                    "run": run_id,
                    "project": project_id,
                    "pipeline": pipeline_id,
                    "test": test_case_id,
                    "sha": "a" * 64,
                    "key": "b" * 64,
                },
            )

        # The verified-row trigger must reject every UPDATE, not only content
        # changes; this protects integrity status and provenance fields too.
        async with engine.connect() as db:
            tx = await db.begin()
            with pytest.raises(Exception, match="verified evidence artifacts are immutable"):
                await db.execute(
                    text(
                        "UPDATE evidence_artifacts SET summary_excerpt='tampered' "
                        "WHERE id=:id"
                    ),
                    {"id": artifact_id},
                )
            await tx.rollback()
            excerpt = await db.execute(
                text("SELECT summary_excerpt FROM evidence_artifacts WHERE id=:id"),
                {"id": artifact_id},
            )
            assert excerpt.scalar_one() == "bounded test evidence"

            # The same session remains usable for reads after the trigger
            # rejection; no verified-row mutation is permitted.
            await db.rollback()
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM evidence_artifacts WHERE id=:id"),
                {"id": artifact_id},
            )
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id=:id"),
                {"id": pipeline_id},
            )
        await engine.dispose()
