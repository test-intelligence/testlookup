"""Opt-in PostgreSQL coverage for retention cascading child runtime rows."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _MongoCollection:
    async def count_documents(self, _query):
        return 0

    async def delete_many(self, _query):
        return type("DeleteResult", (), {"deleted_count": 0})()


class _Mongo:
    def __getitem__(self, _name):
        return _MongoCollection()


class _Storage:
    async def list_objects(self, _prefix, *, bucket):
        return []

    async def delete_prefix(self, _prefix, *, bucket):
        return 0

    async def delete_object(self, _key, *, bucket):
        return None


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def test_retention_purges_old_run_and_all_child_runtime_rows():
    from app.services.retention_service import run_purge

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    run_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    child_pipeline_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    spawn_key = "a" * 64
    old_created_at = datetime.now(timezone.utc) - timedelta(days=400)
    try:
        async with engine.begin() as db:
            authority = (
                await db.execute(
                    text("SELECT id FROM projects WHERE is_active = true LIMIT 1")
                )
            ).scalar_one_or_none()
            if authority is None:
                pytest.skip("homelab database has no active project fixture")
            project_id = authority
            # W22a: the keep-last floor (default 10 runs) would keep this aged
            # run in a sparse project; keep_last_runs = 0 for THIS transaction
            # only (the project is shared, so the prior state is restored
            # before the transaction commits — see the end of the block).
            prior_keep_last = (
                await db.execute(
                    text("SELECT keep_last_runs FROM project_retention_policies WHERE project_id=:p"),
                    {"p": project_id},
                )
            ).scalar_one_or_none()
            floor_policy_id = uuid.uuid4()
            if prior_keep_last is None:
                await db.execute(
                    text("INSERT INTO project_retention_policies (id, project_id, keep_last_runs) VALUES (:i, :p, 0)"),
                    {"i": floor_policy_id, "p": project_id},
                )
            else:
                await db.execute(
                    text("UPDATE project_retention_policies SET keep_last_runs = 0 WHERE project_id=:p"),
                    {"p": project_id},
                )

            await db.execute(
                text(
                    "INSERT INTO test_runs "
                    "(id,project_id,build_number,status,ingestion_source,created_at) "
                    "VALUES (:id,:project,:build,'FAILED','unknown',:created)"
                ),
                {
                    "id": run_id,
                    "project": project_id,
                    "build": f"codex-retention-{run_id.hex}",
                    "created": old_created_at,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,spawn_depth,execution_metadata) "
                    "VALUES (:id,:run,'deep','completed',0,'{}'::json)"
                ),
                {"id": parent_id, "run": run_id},
            )
            await db.execute(
                text(
                    "INSERT INTO failure_clusters "
                    "(id,test_run_id,pipeline_run_id,cluster_id,label,member_test_ids,size) "
                    "VALUES (:id,:run,:pipeline,:cluster,'retention integration',"
                    "CAST(:members AS jsonb),1)"
                ),
                {
                    "id": cluster_id,
                    "run": run_id,
                    "pipeline": parent_id,
                    "cluster": f"rt{cluster_id.hex[:18]}",
                    "members": json.dumps([str(uuid.uuid4())]),
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,parent_pipeline_run_id,parent_task_id,spawn_depth,"
                    "workflow_type,status,execution_metadata) VALUES "
                    "(:id,:run,:parent,:task,1,'investigation','completed','{}'::json)"
                ),
                {
                    "id": child_pipeline_id,
                    "run": run_id,
                    "parent": parent_id,
                    "task": f"pipeline:{parent_id}:task:cluster:retention",
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_investigations "
                    "(id,project_id,run_id,scope_type,failure_cluster_id,"
                    "cluster_scope_sha256,cluster_member_test_ids,parent_pipeline_run_id,"
                    "parent_task_id,spawn_lineage_id,spawn_depth,spawn_key,selection_reason,"
                    "status,mode,triggered_by,budget,spend,hypotheses) VALUES "
                    "(:id,:project,:run,'failure_cluster',:cluster,:scope,"
                    "CAST(:members AS jsonb),:parent,:task,:lineage,1,:spawn,'integration',"
                    "'completed','shadow','auto:cluster_child',CAST(:budget AS jsonb),"
                    "CAST(:spend AS jsonb),'[]'::jsonb)"
                ),
                {
                    "id": investigation_id,
                    "project": project_id,
                    "run": run_id,
                    "cluster": cluster_id,
                    "scope": "b" * 64,
                    "members": json.dumps([str(uuid.uuid4())]),
                    "parent": parent_id,
                    "task": f"pipeline:{parent_id}:task:cluster:retention",
                    "lineage": parent_id,
                    "spawn": spawn_key,
                    "budget": json.dumps({"max_llm_calls": 1, "max_tokens": 10}),
                    "spend": json.dumps({"ledger_version": 2, "reservations": {}}),
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_child_dispatch_outbox "
                    "(id,spawn_key,investigation_id,parent_pipeline_run_id,project_id,run_id,"
                    "status,attempts,next_attempt_at) VALUES "
                    "(:id,:spawn,:investigation,:parent,:project,:run,'failed',10,NULL)"
                ),
                {
                    "id": outbox_id,
                    "spawn": spawn_key,
                    "investigation": investigation_id,
                    "parent": parent_id,
                    "project": project_id,
                    "run": run_id,
                },
            )

            # W22a: a session joined to this connection's transaction — with a
            # policy row present, the policy read needs ORM rows, which a bare
            # AsyncConnection does not return (it answered the row's first column).
            purge_session = AsyncSession(bind=db)
            result = await run_purge(
                purge_session,
                project_id=project_id,
                mode="execute",
                now=datetime.now(timezone.utc),
                mongo=_Mongo(),
                storage=_Storage(),
            )
            await purge_session.close()
            assert result["counts"]["postgres"]["runs"] == 1
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM agent_child_dispatch_outbox WHERE id=:id"
                    ),
                    {"id": outbox_id},
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    text("SELECT count(*) FROM agent_investigations WHERE id=:id"),
                    {"id": investigation_id},
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    text("SELECT count(*) FROM agent_pipeline_runs WHERE id=:id"),
                    {"id": child_pipeline_id},
                )
            ).scalar_one() == 0
            # W22a: restore the shared project's policy (see the top of the block).
            if prior_keep_last is None:
                await db.execute(
                    text("DELETE FROM project_retention_policies WHERE id=:i"), {"i": floor_policy_id}
                )
            else:
                await db.execute(
                    text("UPDATE project_retention_policies SET keep_last_runs=:k WHERE project_id=:p"),
                    {"k": prior_keep_last, "p": project_id},
                )
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM test_runs WHERE id=:id"), {"id": run_id}
            )
        await engine.dispose()
