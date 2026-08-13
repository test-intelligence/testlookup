"""Opt-in PostgreSQL coverage for failure-cluster Investigator resume."""
from __future__ import annotations

import asyncio
import json
import os
import uuid

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


async def test_child_resume_is_lock_serialized_and_preserves_completed_hypothesis(
    monkeypatch,
):
    from app.agents.investigator import workflow

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: factory())
    resumed_calls: list[str] = []

    async def fake_run(investigation_id: str, **_kwargs):
        resumed_calls.append(investigation_id)
        return {"resumed": True, "investigation_id": investigation_id}

    monkeypatch.setattr(workflow, "run_investigation", fake_run)
    investigation_id = uuid.uuid4()
    child_pipeline_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}"
    )
    stage_ids = (uuid.uuid4(), uuid.uuid4())
    try:
        async with engine.begin() as db:
            cluster = (
                await db.execute(
                    text(
                        "SELECT id, test_run_id, pipeline_run_id, cluster_id, "
                        "member_test_ids FROM failure_clusters "
                        "WHERE pipeline_run_id IS NOT NULL "
                        "AND jsonb_array_length(member_test_ids) > 0 "
                        "ORDER BY created_at LIMIT 1"
                    )
                )
            ).first()
            if cluster is None:
                pytest.skip("homelab database has no persisted failure-cluster fixture")
            cluster_id, run_id, parent_pipeline_id, display_id, members = cluster
            project_id = (
                await db.execute(
                    text("SELECT project_id FROM test_runs WHERE id=:id"),
                    {"id": run_id},
                )
            ).scalar_one()
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,parent_pipeline_run_id,parent_task_id,spawn_depth,"
                    "workflow_type,status,execution_metadata) VALUES "
                    "(:id,:run,:parent,:task,1,'investigation','failed','{}'::json)"
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
                    "(:id,:project,:run,'failure_cluster',:cluster,:scope,:members,:parent,"
                    ":task,:lineage,1,:spawn,'pg-resume-test','failed','shadow',"
                    "'manual',:budget,:spend,:hypotheses)"
                ),
                {
                    "id": investigation_id,
                    "project": project_id,
                    "run": run_id,
                    "cluster": cluster_id,
                    "scope": "c" * 64,
                    "members": json.dumps(members),
                    "parent": parent_pipeline_id,
                    "task": f"pipeline:{parent_pipeline_id}:task:cluster:{display_id}",
                    "lineage": parent_pipeline_id,
                    "spawn": "d" * 64,
                    "budget": '{"max_llm_calls":2,"max_tokens":1000,"max_seconds":30}',
                    "spend": '{"ledger_version":2,"llm_calls":1,"tokens":40,"cost_usd":0.0,"reservations":{},"completed_reservations":{}}',
                    "hypotheses": '[{"id":"infra","title":"Infrastructure","status":"complete","confidence":70,"summary":"completed authority","evidence":[]}]',
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_stage_results "
                    "(id,pipeline_run_id,stage_name,status,attempt,idempotency_key,result_data) VALUES "
                    "(:complete,:pipeline,'hypothesis_infra','completed',1,:complete_key,'{}'::json),"
                    "(:pending,:pipeline,'investigator_synthesis','failed',1,:pending_key,'{}'::json)"
                ),
                {
                    "complete": stage_ids[0],
                    "pending": stage_ids[1],
                    "pipeline": child_pipeline_id,
                    "complete_key": "e" * 64,
                    "pending_key": "f" * 64,
                },
            )

        results = await asyncio.gather(
            workflow.resume_investigation(str(investigation_id)),
            workflow.resume_investigation(str(investigation_id)),
        )
        assert sum(bool(item.get("resumed")) for item in results) == 1
        assert any(item.get("skipped") == "status_queued" for item in results)
        assert resumed_calls == [str(investigation_id)]

        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT status, hypotheses, spend FROM agent_investigations "
                        "WHERE id=:id"
                    ),
                    {"id": investigation_id},
                )
            ).one()
            pipeline = (
                await db.execute(
                    text("SELECT status FROM agent_pipeline_runs WHERE id=:id"),
                    {"id": child_pipeline_id},
                )
            ).scalar_one()
            stages = (
                await db.execute(
                    text(
                        "SELECT stage_name,status,attempt,idempotency_key "
                        "FROM agent_stage_results WHERE pipeline_run_id=:id "
                        "ORDER BY stage_name"
                    ),
                    {"id": child_pipeline_id},
                )
            ).all()

        assert row.status == "queued"
        assert row.hypotheses[0]["status"] == "complete"
        assert row.spend["completed_reservations"] == {}
        assert pipeline == "pending"
        assert [(stage.stage_name, stage.status, stage.attempt) for stage in stages] == [
            ("hypothesis_infra", "completed", 1),
            ("investigator_synthesis", "pending", 2),
        ]
        assert stages[0].idempotency_key == "e" * 64
        assert stages[1].idempotency_key is None
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM agent_investigations WHERE id=:id"),
                {"id": investigation_id},
            )
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id=:id"),
                {"id": child_pipeline_id},
            )
        await engine.dispose()
