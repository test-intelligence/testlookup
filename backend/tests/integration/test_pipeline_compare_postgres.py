"""W14 AGT-05 on real PostgreSQL, through the app: ``GET /agents/pipelines/compare``.

* real rows: a rerun pair on one run (``rerun_of``, attempt 2), a stage only
  in b, ``_replay`` checksums equal on one stage and different on another;
* ONE stage statement for both sides (statement counter on the app's engine);
* a VIEWER of the project reads it (200); a member of another project gets the
  pipeline 403 for either side; an admin comparing across projects gets 422;
* the assembled routing table sends ``/pipelines/compare`` to
  ``compare_pipelines`` (never to ``get_pipeline``'s UUID validator).

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.defect_w06_world import StatementCounter, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/v1/agents/pipelines/compare"


def _stage(pipeline_id, name, start, *, checksum=None, tokens=None, cost=None, result=None):
    from app.models.postgres import AgentStageResult

    data = dict(result or {})
    if checksum is not None:
        data["_replay"] = {"output_checksum_sha256": checksum}
    return AgentStageResult(
        pipeline_run_id=pipeline_id, stage_name=name, status="completed",
        started_at=start, completed_at=start + timedelta(seconds=4),
        result_data=data or None, total_tokens=tokens, cost_usd=cost,
    )


@pytest.fixture
async def world():
    from app.models.postgres import AgentPipelineRun, LaunchStatus, ProjectMember, TestRun, UserRole

    async with defect_world() as w:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        async with w.sessions.begin() as db:
            w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
            db.add(ProjectMember(project_id=w.P, user_id=w.viewer_id, role=UserRole.VIEWER.value))
            w.run_q = uuid.uuid4()
            db.add(TestRun(id=w.run_q, project_id=w.Q, build_number=f"cmp-q-{w.tag}", jenkins_job="cmp",
                           status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1))
            await db.flush()
            w.a, w.b, w.q = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            db.add(AgentPipelineRun(id=w.a, test_run_id=w.run_r, workflow_type="offline", status="completed",
                                    started_at=now - timedelta(minutes=10), completed_at=now - timedelta(minutes=9),
                                    execution_metadata={"workflow_ref": "offline@1", "analysis_mode_resolved": "rules"}))
            db.add(AgentPipelineRun(id=w.q, test_run_id=w.run_q, workflow_type="offline", status="completed"))
            await db.flush()
            db.add(AgentPipelineRun(id=w.b, test_run_id=w.run_r, workflow_type="offline", status="failed",
                                    attempt=2, rerun_of=w.a, started_at=now - timedelta(minutes=5),
                                    completed_at=now - timedelta(minutes=4),
                                    execution_metadata={"workflow_ref": "offline@1"}))
            await db.flush()
            t_a, t_b = now - timedelta(minutes=10), now - timedelta(minutes=5)
            for stage in (
                _stage(w.a, "ingestion", t_a, checksum="c-ingest", tokens=0, cost=0.0),
                _stage(w.a, "summary", t_a + timedelta(seconds=10), checksum="c-sum-1", tokens=120, cost=0.01),
                _stage(w.b, "ingestion", t_b, checksum="c-ingest"),
                _stage(w.b, "summary", t_b + timedelta(seconds=10), checksum="c-sum-2", tokens=150, cost=0.02),
                _stage(w.b, "triage", t_b + timedelta(seconds=20)),
                _stage(w.q, "ingestion", t_a, checksum="c-ingest"),
            ):
                db.add(stage)
        w.viewer = w.jwt(w.viewer_id)
        yield w


async def test_a_viewer_compares_a_rerun_pair_with_one_stage_statement(world):
    w = world
    with StatementCounter(w.engine) as counter:
        response = await w.client.get(URL, params={"a": str(w.a), "b": str(w.b)}, headers=w.viewer)
    assert response.status_code == 200, response.text
    body = response.json()
    stage_reads = [s for s in counter.statements if "agent_stage_results" in s]
    assert len(stage_reads) == 1, stage_reads
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in counter.statements)

    assert body["same_test_run"] is True
    assert body["b"]["rerun_of"] == str(w.a) and body["b"]["attempt"] == 2
    assert body["a"]["workflow_ref"] == "offline@1" and body["a"]["analysis_mode_resolved"] == "rules"
    assert body["a"]["cost_usd"] == 0.01 and body["a"]["total_tokens"] == 120
    assert [row["stage_name"] for row in body["stages"]] == ["ingestion", "summary", "triage"]
    rows = {row["stage_name"]: row for row in body["stages"]}
    assert rows["ingestion"]["output_checksum_equal"] is True
    assert rows["summary"]["output_checksum_equal"] is False
    assert rows["summary"]["tokens_delta"] == 30
    assert rows["triage"]["a"] is None and rows["triage"]["changed"] == ["presence"]
    assert body["output"] == "changed"
    assert body["changed_stages"] == 2 and body["total"] == 3


async def test_a_member_of_another_project_gets_the_pipeline_403_for_either_side(world):
    w = world
    for a, b in ((w.a, w.b), (w.b, w.a)):
        response = await w.client.get(URL, params={"a": str(a), "b": str(b)}, headers=w.outsider)
        assert response.status_code == 403
        assert response.json()["detail"] == "You do not have access to this pipeline run"
    # The member of P comparing P's pipeline with Q's: the IDOR is a 403, not a 422 that confirms Q's id.
    response = await w.client.get(URL, params={"a": str(w.a), "b": str(w.q)}, headers=w.member)
    assert response.status_code == 403


async def test_an_admin_across_projects_gets_422_and_unknown_ids_404(world):
    from app.core.agent_ops_vocab import COMPARE_CROSS_PROJECT, COMPARE_SAME_ID

    w = world
    response = await w.client.get(URL, params={"a": str(w.a), "b": str(w.q)}, headers=w.admin)
    assert response.status_code == 422 and response.json()["detail"] == COMPARE_CROSS_PROJECT
    response = await w.client.get(URL, params={"a": str(w.a), "b": str(w.a)}, headers=w.admin)
    assert response.status_code == 422 and response.json()["detail"] == COMPARE_SAME_ID
    response = await w.client.get(URL, params={"a": str(uuid.uuid4()), "b": str(w.a)}, headers=w.admin)
    assert response.status_code == 404 and response.json()["detail"] == "Pipeline run not found"


async def test_the_routing_table_sends_compare_to_compare_pipelines(world):
    from starlette.routing import Match

    from app.main import app

    w = world
    scope = {"type": "http", "path": URL, "method": "GET", "root_path": "", "query_string": b"", "headers": []}
    first_full = next(route for route in app.router.routes if route.matches(scope)[0] == Match.FULL)
    assert first_full.endpoint.__name__ == "compare_pipelines"
    # Without the query ids FastAPI names the missing parameters, not a uuid_parsing error on a path id.
    response = await w.client.get(URL, headers=w.viewer)
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert {tuple(e["loc"]) for e in errors} == {("query", "a"), ("query", "b")}
    assert all(e["type"] == "missing" for e in errors)
