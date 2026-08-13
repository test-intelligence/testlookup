"""Opt-in PostgreSQL coverage for concurrent child cancellation."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

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


async def test_concurrent_cancel_terminalizes_queued_and_cooperatively_marks_running(
    monkeypatch,
):
    from app.services import cluster_investigation_orchestrator as orchestrator

    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: factory())

    parent_id = uuid.uuid4()
    queued_id = uuid.uuid4()
    running_id = uuid.uuid4()
    cluster_ids = [uuid.uuid4(), uuid.uuid4()]
    outbox_ids = [uuid.uuid4(), uuid.uuid4()]
    spawn_keys = ["1" * 64, "2" * 64]
    parent_task_ids = [
        f"pipeline:{parent_id}:task:cluster:cancel-queued",
        f"pipeline:{parent_id}:task:cluster:cancel-running",
    ]
    investigation_ids = [queued_id, running_id]
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
            for index, cluster_id in enumerate(cluster_ids):
                await db.execute(
                    text(
                        "INSERT INTO failure_clusters "
                        "(id,test_run_id,pipeline_run_id,cluster_id,label,member_test_ids,size) "
                        "VALUES (:id,:run,:pipeline,:cluster,:label,"
                        "CAST(:members AS jsonb),1)"
                    ),
                    {
                        "id": cluster_id,
                        "run": run_id,
                        "pipeline": parent_id,
                        "cluster": f"cx{index}{str(cluster_id).replace('-', '')[:17]}",
                        "label": f"cancel integration {index}",
                        "members": json.dumps([str(test_case_id)]),
                    },
                )

            for index, investigation_id in enumerate(investigation_ids):
                status = "queued" if index == 0 else "running"
                outbox_status = "pending" if index == 0 else "sending"
                await db.execute(
                    text(
                        "INSERT INTO agent_investigations "
                        "(id,project_id,run_id,scope_type,failure_cluster_id,"
                        "cluster_scope_sha256,cluster_member_test_ids,parent_pipeline_run_id,"
                        "parent_task_id,spawn_lineage_id,spawn_depth,spawn_key,selection_reason,"
                        "status,mode,triggered_by,budget,spend,hypotheses,started_at) VALUES "
                        "(:id,:project,:run,'failure_cluster',:cluster,:scope,"
                        "CAST(:members AS jsonb),:parent,:task,:lineage,1,:spawn,'integration',"
                        ":status,'shadow','auto:cluster_child',CAST(:budget AS jsonb),"
                        "CAST(:spend AS jsonb),'[]'::jsonb,:started_at)"
                    ),
                    {
                        "id": investigation_id,
                        "project": project_id,
                        "run": run_id,
                        "cluster": cluster_ids[index],
                        "scope": f"{index + 3}" * 64,
                        "members": json.dumps([str(test_case_id)]),
                        "parent": parent_id,
                        "task": parent_task_ids[index],
                        "lineage": parent_id,
                        "spawn": spawn_keys[index],
                        "status": status,
                        "budget": json.dumps({"max_llm_calls": 1, "max_tokens": 10}),
                        "spend": json.dumps({"ledger_version": 2, "reservations": {}}),
                        "started_at": (
                            datetime.now(timezone.utc) if status == "running" else None
                        ),
                    },
                )
                await db.execute(
                    text(
                        "INSERT INTO agent_child_dispatch_outbox "
                        "(id,spawn_key,investigation_id,parent_pipeline_run_id,project_id,run_id,"
                        "status,attempts,next_attempt_at) VALUES "
                        "(:id,:spawn,:investigation,:parent,:project,:run,:status,0,NULL)"
                    ),
                    {
                        "id": outbox_ids[index],
                        "spawn": spawn_keys[index],
                        "investigation": investigation_id,
                        "parent": parent_id,
                        "project": project_id,
                        "run": run_id,
                        "status": outbox_status,
                    },
                )

        results = await asyncio.gather(
            orchestrator.cancel_cluster_children(
                str(parent_id), reason="parent_cancel"
            ),
            orchestrator.cancel_cluster_children(str(parent_id), reason="parent_cancel"),
        )
        assert sorted(results) == [1, 2]

        async with factory() as db:
            rows = list(
                (
                    await db.execute(
                        text(
                            "SELECT id,status,cancel_requested,cancelled_by "
                            "FROM agent_investigations WHERE id IN (:queued,:running) "
                            "ORDER BY id"
                        ),
                        {"queued": queued_id, "running": running_id},
                    )
                ).mappings()
            )
            outboxes = list(
                (
                    await db.execute(
                        text(
                            "SELECT investigation_id,status,next_attempt_at,last_error "
                            "FROM agent_child_dispatch_outbox "
                            "WHERE investigation_id IN (:queued,:running) "
                            "ORDER BY investigation_id"
                        ),
                        {"queued": queued_id, "running": running_id},
                    )
                ).mappings()
            )
        by_id = {row["id"]: row for row in rows}
        assert by_id[queued_id]["status"] == "cancelled"
        assert by_id[queued_id]["cancel_requested"] is False
        assert by_id[running_id]["status"] == "running"
        assert by_id[running_id]["cancel_requested"] is True
        assert all(row["status"] == "failed" for row in outboxes)
        assert all(row["next_attempt_at"] is None for row in outboxes)
        assert all(row["last_error"] == "parent_cancel" for row in outboxes)
        assert by_id[running_id]["cancelled_by"] == "parent_cancel"
    finally:
        async with engine.begin() as db:
            await db.execute(
                text(
                    "DELETE FROM agent_child_dispatch_outbox "
                    "WHERE id IN (:a,:b)"
                ),
                {"a": outbox_ids[0], "b": outbox_ids[1]},
            )
            await db.execute(
                text(
                    "DELETE FROM agent_investigations WHERE id IN (:a,:b)"
                ),
                {"a": queued_id, "b": running_id},
            )
            await db.execute(
                text(
                    "DELETE FROM failure_clusters WHERE id IN (:a,:b)"
                ),
                {"a": cluster_ids[0], "b": cluster_ids[1]},
            )
            await db.execute(
                text("DELETE FROM agent_pipeline_runs WHERE id=:id"),
                {"id": parent_id},
            )
        await engine.dispose()
