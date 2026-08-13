"""Opt-in PostgreSQL coverage for child claim/startup failure recovery."""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

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


async def test_claimed_child_startup_failure_is_finalized_without_provider_call(
    monkeypatch,
):
    """A failure after the queued claim cannot strand a running child."""
    from app.agents.investigator import workflow

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: factory())
    graph_calls: list[str] = []

    async def fail_before_provider(*_args, **_kwargs):
        raise RuntimeError("password=should-never-reach-provider")

    async def graph_must_not_run(*_args, **_kwargs):
        graph_calls.append("unexpected")
        return {}

    async def fail_audit_mirror(*_args, **_kwargs):
        raise RuntimeError("password=mirror-secret")

    monkeypatch.setattr(workflow, "_create_stage_rows", fail_before_provider)
    monkeypatch.setattr(
        workflow,
        "_investigator_app",
        SimpleNamespace(ainvoke=graph_must_not_run),
    )
    monkeypatch.setattr(
        "app.services.pipeline_event_log.emit_event",
        fail_audit_mirror,
    )
    investigation_id = uuid.uuid4()
    parent_pipeline_id: uuid.UUID | None = None
    cluster_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    child_pipeline_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}"
    )
    try:
        async with engine.begin() as db:
            authority = (
                await db.execute(
                    text(
                        "SELECT tr.id AS run_id, tr.project_id, tc.id AS test_case_id "
                        "FROM test_runs tr JOIN test_cases tc ON tc.test_run_id = tr.id "
                        "WHERE tc.status IN ('FAILED','BROKEN') LIMIT 1"
                    )
                )
            ).mappings().first()
            if authority is None:
                pytest.skip("homelab database has no failed test authority fixture")
            run_id = authority["run_id"]
            project_id = authority["project_id"]
            test_case_id = authority["test_case_id"]
            members = [str(test_case_id)]
            parent_pipeline_id = uuid.uuid4()
            cluster_id = uuid.uuid4()
            display_id = f"claim{str(cluster_id).replace('-', '')[:14]}"
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,spawn_depth,execution_metadata) "
                    "VALUES (:id,:run,'deep','completed',0,'{}'::jsonb)"
                ),
                {"id": parent_pipeline_id, "run": run_id},
            )
            await db.execute(
                text(
                    "INSERT INTO failure_clusters "
                    "(id,test_run_id,pipeline_run_id,cluster_id,label,member_test_ids,size) "
                    "VALUES (:id,:run,:pipeline,:cluster,'claim integration',"
                    "CAST(:members AS jsonb),1)"
                ),
                {
                    "id": cluster_id,
                    "run": run_id,
                    "pipeline": parent_pipeline_id,
                    "cluster": display_id,
                    "members": json.dumps(members),
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,parent_pipeline_run_id,parent_task_id,spawn_depth,"
                    "workflow_type,status,execution_metadata) VALUES "
                    "(:id,:run,:parent,:task,1,'investigation','pending','{}'::jsonb)"
                ),
                {
                    "id": child_pipeline_id,
                    "run": run_id,
                    "parent": parent_pipeline_id,
                    "task": f"pipeline:{parent_pipeline_id}:task:cluster:{display_id}",
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
                    "CAST(:members AS jsonb),:parent,:task,:lineage,1,:spawn,'startup-test',"
                    "'queued','shadow','auto:cluster_child',CAST(:budget AS jsonb),"
                    "CAST(:spend AS jsonb),'[]'::jsonb)"
                ),
                {
                    "id": investigation_id,
                    "project": project_id,
                    "run": run_id,
                    "cluster": cluster_id,
                    "scope": "a" * 64,
                    "members": json.dumps(members),
                    "parent": parent_pipeline_id,
                    "task": f"pipeline:{parent_pipeline_id}:task:cluster:{display_id}",
                    "lineage": parent_pipeline_id,
                    "spawn": "b" * 64,
                    "budget": json.dumps({"max_llm_calls": 1, "max_tokens": 100, "max_seconds": 30}),
                    "spend": json.dumps({"ledger_version": 2, "reservations": {}}),
                },
            )

        with pytest.raises(RuntimeError) as error:
            await workflow.run_investigation(str(investigation_id))
        assert "should-never-reach-provider" not in str(error.value)
        assert graph_calls == []

        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT status,error FROM agent_investigations WHERE id=:id"
                    ),
                    {"id": investigation_id},
                )
            ).one()
            pipeline_status = (
                await db.execute(
                    text("SELECT status,error FROM agent_pipeline_runs WHERE id=:id"),
                    {"id": child_pipeline_id},
                )
            ).one()
            ledger_count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM agent_runs "
                        "WHERE run_id=:run AND agent_id='investigator'"
                    ),
                    {"run": run_id},
                )
            ).scalar_one()
        assert row.status == "failed"
        assert "should-never-reach-provider" not in (row.error or "")
        assert "mirror-secret" not in (row.error or "")
        assert pipeline_status.status == "failed"
        assert "should-never-reach-provider" not in (pipeline_status.error or "")
        assert "mirror-secret" not in (pipeline_status.error or "")
        assert ledger_count >= 1
    finally:
        async with engine.begin() as db:
            await db.execute(
                text(
                    "DELETE FROM agent_runs "
                    "WHERE details_path=:path AND agent_id='investigator'"
                ),
                {"path": f"/investigations/{investigation_id}"},
            )
            await db.execute(
                text("DELETE FROM agent_investigations WHERE id=:id"),
                {"id": investigation_id},
            )
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id=:id"),
                {"id": child_pipeline_id},
            )
            if cluster_id is not None:
                await db.execute(
                    text("DELETE FROM failure_clusters WHERE id=:id"),
                    {"id": cluster_id},
                )
            if parent_pipeline_id is not None:
                await db.execute(
                    text("DELETE FROM agent_pipeline_runs WHERE id=:id"),
                    {"id": parent_pipeline_id},
                )
        await engine.dispose()
