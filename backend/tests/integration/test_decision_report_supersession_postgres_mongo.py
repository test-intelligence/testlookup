"""Opt-in live PostgreSQL+Mongo supersession publication coverage."""
from __future__ import annotations

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


async def test_supersession_publishes_one_immutable_version_and_replays_idempotently(
    monkeypatch,
):
    from app.db import postgres as app_postgres
    from app.agents.decision_report_agent import compute_decision_evidence_hash
    from app.db.mongo import Collections, close_mongo, get_mongo_db
    from app.services.decision_report_service import publish_decision_report
    from app.services.decision_report_supersession_service import _process_request

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        "app.services.decision_report_supersession_service.AsyncSessionLocal",
        lambda: factory(),
    )
    mongo = get_mongo_db()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    request_id = uuid.uuid4()
    spawn_key = uuid.uuid4().hex + uuid.uuid4().hex
    report_ids: list[str] = []

    try:
        async with engine.begin() as db:
            authority = (await db.execute(text(
                "SELECT tr.id AS run_id, tr.project_id, tc.id AS test_case_id "
                "FROM test_runs tr JOIN test_cases tc ON tc.test_run_id=tr.id "
                "WHERE tc.status IN ('FAILED','BROKEN') LIMIT 1"
            ))).mappings().first()
            if authority is None:
                pytest.skip("no failure-cluster authority fixture")
            run_id = authority["run_id"]
            project_id = authority["project_id"]
            test_case_id = authority["test_case_id"]
            await db.execute(text(
                "INSERT INTO agent_pipeline_runs "
                "(id,test_run_id,workflow_type,status,spawn_depth,execution_metadata) "
                "VALUES (:id,:run,'deep','completed',0,CAST(:meta AS json))"
            ), {"id": parent_id, "run": run_id, "meta": json.dumps({
                "async_decision_report_supersession_enabled": True,
            })})
            await db.execute(text(
                "INSERT INTO failure_clusters "
                "(id,test_run_id,pipeline_run_id,cluster_id,label,member_test_ids,size) "
                "VALUES (:id,:run,:pipeline,:cluster,'supersession integration',"
                "CAST(:members AS jsonb),1)"
            ), {
                "id": cluster_id, "run": run_id, "pipeline": parent_id,
                "cluster": f"sup{str(cluster_id).replace('-', '')[:17]}",
                "members": json.dumps([str(test_case_id)]),
            })
            await db.execute(text(
                "INSERT INTO agent_investigations "
                "(id,project_id,run_id,scope_type,failure_cluster_id,"
                "cluster_scope_sha256,cluster_member_test_ids,parent_pipeline_run_id,"
                "parent_task_id,spawn_lineage_id,spawn_depth,spawn_key,selection_reason,"
                "status,mode,triggered_by,budget,spend,hypotheses,verdict,cancel_requested) "
                "VALUES (:id,:project,:run,'failure_cluster',:cluster,:scope,:members,"
                ":parent,:task,:lineage,1,:spawn,'integration','completed','shadow',"
                "'auto:cluster_child','{}'::json,'{}'::json,'[]'::json,'{}'::json,false)"
            ), {
                "id": child_id, "project": project_id, "run": run_id,
                "cluster": cluster_id, "scope": "a" * 64,
                "members": json.dumps([str(test_case_id)]), "parent": parent_id,
                "task": f"pipeline:{parent_id}:task:cluster:integration",
                "lineage": parent_id, "spawn": spawn_key,
            })
            await db.execute(text(
                "INSERT INTO decision_report_supersession_requests "
                "(id,project_id,test_run_id,parent_pipeline_run_id,status,reason,"
                "attempts,next_attempt_at) VALUES (:id,:project,:run,:parent,'pending',"
                "'cluster_children_terminal',0,now())"
            ), {"id": request_id, "project": project_id, "run": run_id, "parent": parent_id})

        decision = {
            "schema_version": 1,
            "status": "complete",
            "metrics": {"total_tests": 1, "failed_tests": 1},
            "failure_clusters": [], "deep_findings": {}, "flaky_findings": [],
            "test_health_findings": [], "contract_findings": None,
            "log_findings": None, "regression_classification": None,
            "change_ownership_findings": None,
            "cluster_investigation_results": None, "release_decision": {},
            "quality_review": {"missing_or_failed_specialists": [], "contradictions": []},
            "source_stages": ["release_risk"],
            "verification": {"status": "passed"},
        }
        decision["evidence_bundle_sha256"] = compute_decision_evidence_hash(decision)
        report = await publish_decision_report(
            mongo,
            state={"project_id": str(project_id), "test_run_id": str(run_id), "pipeline_run_id": str(parent_id)},
            decision=decision,
            markdown="parent",
        )
        report_ids.append(report["report_id"])
        result = await _process_request(request_id)
        assert result["status"] == "published"
        report_ids.append(result["report_id"])
        result_retry = await _process_request(request_id)
        assert result_retry == {"status": "skipped"}
        assert len(await mongo[Collections.DECISION_REPORTS].find(
            {"test_run_id": str(run_id), "report_id": report_ids[-1]}, {"_id": 0}
        ).to_list(length=1)) == 1
        latest = await mongo[Collections.DECISION_REPORTS].find_one(
            {"report_id": report_ids[-1]}, {"_id": 0}
        )
        assert latest["supersedes_report_id"] == report_ids[0]
        assert latest["verification"]["status"] == "passed"
    finally:
        await mongo[Collections.DECISION_REPORTS].delete_many(
            {"report_id": {"$in": report_ids}}
        )
        async with engine.begin() as db:
            await db.execute(text(
                "DELETE FROM decision_report_supersession_requests WHERE id=:id"
            ), {"id": request_id})
            await db.execute(text(
                "DELETE FROM agent_investigations WHERE id=:id"
            ), {"id": child_id})
            await db.execute(text(
                "DELETE FROM agent_pipeline_runs WHERE id=:id"
            ), {"id": parent_id})
            await db.execute(text(
                "DELETE FROM failure_clusters WHERE id=:id"
            ), {"id": cluster_id})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_mongo()


async def test_supersession_external_store_failure_is_retryable_and_idempotent(
    monkeypatch,
):
    """A Mongo publication failure must not consume the durable PG request."""
    from app.db import postgres as app_postgres
    from app.agents.decision_report_agent import compute_decision_evidence_hash
    from app.db.mongo import Collections, close_mongo, get_mongo_db
    from app.services.decision_report_service import publish_decision_report as real_publish
    from app.services.decision_report_supersession_service import (
        process_pending_decision_report_supersessions,
    )

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        "app.services.decision_report_supersession_service.AsyncSessionLocal",
        lambda: factory(),
    )
    mongo = get_mongo_db()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    request_id = uuid.uuid4()
    spawn_key = uuid.uuid4().hex + uuid.uuid4().hex
    report_ids: list[str] = []

    try:
        async with engine.begin() as db:
            authority = (await db.execute(text(
                "SELECT tr.id AS run_id, tr.project_id, tc.id AS test_case_id "
                "FROM test_runs tr JOIN test_cases tc ON tc.test_run_id=tr.id "
                "WHERE tc.status IN ('FAILED','BROKEN') LIMIT 1"
            ))).mappings().first()
            if authority is None:
                pytest.skip("no failure-cluster authority fixture")
            run_id = authority["run_id"]
            project_id = authority["project_id"]
            test_case_id = authority["test_case_id"]
            await db.execute(text(
                "INSERT INTO agent_pipeline_runs "
                "(id,test_run_id,workflow_type,status,spawn_depth,execution_metadata) "
                "VALUES (:id,:run,'deep','completed',0,CAST(:meta AS json))"
            ), {"id": parent_id, "run": run_id, "meta": json.dumps({
                "async_decision_report_supersession_enabled": True,
            })})
            await db.execute(text(
                "INSERT INTO failure_clusters "
                "(id,test_run_id,pipeline_run_id,cluster_id,label,member_test_ids,size) "
                "VALUES (:id,:run,:pipeline,:cluster,'retry integration',"
                "CAST(:members AS jsonb),1)"
            ), {
                "id": cluster_id,
                "run": run_id,
                "pipeline": parent_id,
                "cluster": f"retry{str(cluster_id).replace('-', '')[:15]}",
                "members": json.dumps([str(test_case_id)]),
            })
            await db.execute(text(
                "INSERT INTO agent_investigations "
                "(id,project_id,run_id,scope_type,failure_cluster_id,"
                "cluster_scope_sha256,cluster_member_test_ids,parent_pipeline_run_id,"
                "parent_task_id,spawn_lineage_id,spawn_depth,spawn_key,selection_reason,"
                "status,mode,triggered_by,budget,spend,hypotheses,verdict,cancel_requested) "
                "VALUES (:id,:project,:run,'failure_cluster',:cluster,:scope,:members,"
                ":parent,:task,:lineage,1,:spawn,'retry','completed','shadow',"
                "'auto:cluster_child','{}'::json,'{}'::json,'[]'::json,'{}'::json,false)"
            ), {
                "id": child_id,
                "project": project_id,
                "run": run_id,
                "cluster": cluster_id,
                "scope": "b" * 64,
                "members": json.dumps([str(test_case_id)]),
                "parent": parent_id,
                "task": f"pipeline:{parent_id}:task:cluster:retry",
                "lineage": parent_id,
                "spawn": spawn_key,
            })
            await db.execute(text(
                "INSERT INTO decision_report_supersession_requests "
                "(id,project_id,test_run_id,parent_pipeline_run_id,status,reason,"
                "attempts,next_attempt_at) VALUES (:id,:project,:run,:parent,'pending',"
                "'cluster_children_terminal',0,now())"
            ), {"id": request_id, "project": project_id, "run": run_id, "parent": parent_id})

        decision = {
            "schema_version": 1,
            "status": "complete",
            "metrics": {"total_tests": 1, "failed_tests": 1},
            "failure_clusters": [], "deep_findings": {}, "flaky_findings": [],
            "test_health_findings": [], "contract_findings": None,
            "log_findings": None, "regression_classification": None,
            "change_ownership_findings": None,
            "cluster_investigation_results": None, "release_decision": {},
            "quality_review": {"missing_or_failed_specialists": [], "contradictions": []},
            "source_stages": ["release_risk"],
            "verification": {"status": "passed"},
        }
        decision["evidence_bundle_sha256"] = compute_decision_evidence_hash(decision)
        parent_report = await real_publish(
            mongo,
            state={"project_id": str(project_id), "test_run_id": str(run_id), "pipeline_run_id": str(parent_id)},
            decision=decision,
            markdown="retry parent",
        )
        report_ids.append(parent_report["report_id"])

        remaining_failures = 1

        async def flaky_publish(*args, **kwargs):
            nonlocal remaining_failures
            if remaining_failures:
                remaining_failures -= 1
                raise RuntimeError("injected_external_store_failure")
            return await real_publish(*args, **kwargs)

        monkeypatch.setattr(
            "app.services.decision_report_supersession_service.publish_decision_report",
            flaky_publish,
        )
        first = await process_pending_decision_report_supersessions()
        assert first == {"claimed": 1, "published": 0, "pending": 0, "failed": 1}

        async with engine.begin() as db:
            await db.execute(text(
                "UPDATE decision_report_supersession_requests "
                "SET next_attempt_at=now() WHERE id=:id"
            ), {"id": request_id})

        second = await process_pending_decision_report_supersessions()
        assert second == {"claimed": 1, "published": 1, "pending": 0, "failed": 0}
        published = await mongo[Collections.DECISION_REPORTS].find(
            {"test_run_id": str(run_id), "pipeline_run_id": {"$ne": str(parent_id)}, "status": "published"},
            {"_id": 0},
        ).to_list(length=10)
        assert len(published) == 1
        report_ids.append(published[0]["report_id"])
        retry = await process_pending_decision_report_supersessions()
        assert retry == {"claimed": 0, "published": 0, "pending": 0, "failed": 0}
    finally:
        await mongo[Collections.DECISION_REPORTS].delete_many(
            {"report_id": {"$in": report_ids}}
        )
        async with engine.begin() as db:
            await db.execute(text(
                "DELETE FROM decision_report_supersession_requests WHERE id=:id"
            ), {"id": request_id})
            await db.execute(text(
                "DELETE FROM agent_investigations WHERE id=:id"
            ), {"id": child_id})
            await db.execute(text(
                "DELETE FROM agent_pipeline_runs WHERE id=:id"
            ), {"id": parent_id})
            await db.execute(text(
                "DELETE FROM failure_clusters WHERE id=:id"
            ), {"id": cluster_id})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_mongo()
