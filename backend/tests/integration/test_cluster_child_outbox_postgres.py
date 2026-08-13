"""Opt-in PostgreSQL coverage for durable cluster-child outbox recovery."""
from __future__ import annotations

import json
import os
import types
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

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


async def test_stale_sent_child_is_replayed_only_while_queued(monkeypatch):
    from app.services import cluster_investigation_orchestrator as orchestrator

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: factory())
    dispatched: list[dict] = []

    def apply_async(**kwargs):
        dispatched.append(kwargs)

    task_module = types.ModuleType("app.worker.tasks")
    task_module.run_agent_child_investigation = types.SimpleNamespace(
        apply_async=apply_async
    )
    monkeypatch.setitem(__import__("sys").modules, "app.worker.tasks", task_module)

    parent_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    child_pipeline_id = uuid.uuid4()
    spawn_key = "a" * 64
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
            ).first()
            if authority is None:
                pytest.skip("homelab database has no failed test authority fixture")
            run_id, project_id, test_case_id = authority

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
                    "VALUES (:id,:run,:pipeline,:cluster,'outbox integration',"
                    "CAST(:members AS jsonb),1)"
                ),
                {
                    "id": cluster_id,
                    "run": run_id,
                    "pipeline": parent_id,
                    "cluster": f"pg{str(cluster_id).replace('-', '')[:17]}",
                    "members": json.dumps([str(test_case_id)]),
                },
            )
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,parent_pipeline_run_id,parent_task_id,spawn_depth,"
                    "workflow_type,status,execution_metadata) VALUES "
                    "(:id,:run,:parent,:task,1,'investigation','pending','{}'::json)"
                ),
                {
                    "id": child_pipeline_id,
                    "run": run_id,
                    "parent": parent_id,
                    "task": f"pipeline:{parent_id}:task:cluster:outbox",
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
                    "'queued','shadow','auto:cluster_child',CAST(:budget AS jsonb),"
                    "CAST(:spend AS jsonb),'[]'::jsonb)"
                ),
                {
                    "id": investigation_id,
                    "project": project_id,
                    "run": run_id,
                    "cluster": cluster_id,
                    "scope": "b" * 64,
                    "members": json.dumps([str(test_case_id)]),
                    "parent": parent_id,
                    "task": f"pipeline:{parent_id}:task:cluster:outbox",
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
                    "status,attempts,sent_at,next_attempt_at) VALUES "
                    "(:id,:spawn,:investigation,:parent,:project,:run,'pending',0,NULL,NULL)"
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

        first = await orchestrator.relay_child_dispatch_outbox(
            parent_pipeline_run_id=str(parent_id)
        )
        assert first == {"claimed": 1, "sent": 1, "failed": 0}
        assert len(dispatched) == 1
        assert dispatched[0]["task_id"] == f"cluster-child-{spawn_key}"

        async with factory() as db:
            await db.execute(
                text(
                    "UPDATE agent_child_dispatch_outbox SET sent_at=:old "
                    "WHERE id=:id"
                ),
                {
                    "id": outbox_id,
                    "old": datetime.now(timezone.utc) - timedelta(seconds=301),
                },
            )
            await db.commit()

        recovered = await orchestrator.relay_child_dispatch_outbox(
            parent_pipeline_run_id=str(parent_id)
        )
        assert recovered == {"claimed": 1, "sent": 1, "failed": 0}
        assert len(dispatched) == 2

        async with factory() as db:
            await db.execute(
                text(
                    "UPDATE agent_investigations SET status='completed' WHERE id=:id"
                ),
                {"id": investigation_id},
            )
            await db.execute(
                text(
                    "UPDATE agent_child_dispatch_outbox SET sent_at=:old "
                    "WHERE id=:id"
                ),
                {
                    "id": outbox_id,
                    "old": datetime.now(timezone.utc) - timedelta(seconds=301),
                },
            )
            await db.commit()

        terminal = await orchestrator.relay_child_dispatch_outbox(
            parent_pipeline_run_id=str(parent_id)
        )
        assert terminal == {"claimed": 0, "sent": 0, "failed": 0}
        assert len(dispatched) == 2
    finally:
        async with engine.begin() as db:
            await db.execute(
                text("DELETE FROM agent_child_dispatch_outbox WHERE id=:id"),
                {"id": outbox_id},
            )
            await db.execute(
                text("DELETE FROM agent_investigations WHERE id=:id"),
                {"id": investigation_id},
            )
            await db.execute(
                text("DELETE FROM failure_clusters WHERE id=:id"),
                {"id": cluster_id},
            )
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id IN (:child,:parent)"),
                {"child": child_pipeline_id, "parent": parent_id},
            )
        await engine.dispose()


async def test_concurrent_parent_dispatch_is_singleton_and_reuses_spawn_key(monkeypatch):
    """Project locking + deterministic spawn keys prevent duplicate children."""
    from app.services import cluster_investigation_orchestrator as orchestrator

    engine = create_async_engine(_dsn(), pool_size=8, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: factory())

    parent_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
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
            settings = {
                "enabled": True,
                "feature_flag_enabled": True,
                "policy_enabled": True,
                "mode": "shadow",
                "max_children": 1,
                "max_members": 10,
                "max_active_per_project": 1,
                "max_children_per_day": 10,
                "aggregate_budget": {
                    "max_llm_calls": 1,
                    "max_tokens": 100,
                    "max_cost_usd": 1.0,
                    "max_seconds": 10,
                },
            }
            await db.execute(
                text(
                    "INSERT INTO agent_pipeline_runs "
                    "(id,test_run_id,workflow_type,status,spawn_depth,execution_metadata) "
                    "VALUES (:id,:run,'deep','completed',0,CAST(:meta AS jsonb))"
                ),
                {"id": parent_id, "run": run_id, "meta": json.dumps({"cluster_child_settings": settings})},
            )
            await db.execute(
                text(
                    "INSERT INTO failure_clusters "
                    "(id,test_run_id,pipeline_run_id,cluster_id,label,member_test_ids,size) "
                    "VALUES (:id,:run,:pipeline,:cluster,'dispatch integration',"
                    "CAST(:members AS jsonb),1)"
                ),
                {
                    "id": cluster_id,
                    "run": run_id,
                    "pipeline": parent_id,
                    "cluster": f"pg{str(cluster_id).replace('-', '')[:17]}",
                    "members": json.dumps([str(test_case_id)]),
                },
            )

        async def dispatch():
            return await orchestrator.stage_cluster_investigations(
                parent_pipeline_run_id=str(parent_id),
                project_id=str(project_id),
                run_id=str(run_id),
                frozen_settings=settings,
            )

        results = await asyncio.gather(dispatch(), dispatch())
        assert all(item["planner_sha256"] == results[0]["planner_sha256"] for item in results)
        assert all(item["dispatched_count"] == 1 for item in results)

        async with factory() as db:
            counts = (
                await db.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM agent_investigations "
                        " WHERE parent_pipeline_run_id=:parent) AS investigations, "
                        "(SELECT count(*) FROM agent_child_dispatch_outbox "
                        " WHERE parent_pipeline_run_id=:parent) AS outboxes, "
                        "(SELECT count(*) FROM agent_stage_results "
                        " WHERE pipeline_run_id=:parent AND stage_name='cluster_investigation') AS stages"
                    ),
                    {"parent": parent_id},
                )
            ).mappings().one()
            assert counts["investigations"] == 1
            assert counts["outboxes"] == 1
            assert counts["stages"] == 1
    finally:
        async with engine.begin() as db:
            await db.execute(
                text(
                    "DELETE FROM agent_child_dispatch_outbox "
                    "WHERE parent_pipeline_run_id=:parent"
                ),
                {"parent": parent_id},
            )
            await db.execute(
                text(
                    "DELETE FROM agent_investigations "
                    "WHERE parent_pipeline_run_id=:parent"
                ),
                {"parent": parent_id},
            )
            await db.execute(
                text(
                    "DELETE FROM agent_stage_results WHERE pipeline_run_id=:parent"
                ),
                {"parent": parent_id},
            )
            await db.execute(
                text("DELETE FROM failure_clusters WHERE id=:id"),
                {"id": cluster_id},
            )
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id=:parent"),
                {"parent": parent_id},
            )
        await engine.dispose()
