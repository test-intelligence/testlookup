"""D-0020 (GPT-6) on real PostgreSQL, through the app: ``GET /agents/pipelines?run_id=``.

The run page reads a run's pipelines by run. It used to send the selected project too, so a member of projects P and
Q opening a P run with Q selected got ``200 []`` (the filters intersect) and the Evidence tab said no pipeline had
analysed the run. The client now sends the run alone and relies on the server's membership filter, which these tests
pin:

* a member of P and Q reads the P run's pipelines by run, newest first (the fixed request);
* the same read naming Q keeps its documented meaning, an intersection: ``200 []`` (the old request);
* a member of Q only gets ``200 []`` for the P run with no project named (access is still enforced);
* an admin reads every pipeline of the run.

Uses ``tests/integration/defect_w06_world.py``. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and
``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/v1/agents/pipelines"


@pytest.fixture
async def world():
    from app.models.postgres import AgentPipelineRun, ProjectMember, UserRole

    async with defect_world() as w:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        async with w.sessions.begin() as db:
            w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
            for project in (w.P, w.Q):
                db.add(ProjectMember(project_id=project, user_id=w.viewer_id, role=UserRole.VIEWER.value))
            w.older, w.newer = uuid.uuid4(), uuid.uuid4()
            db.add(AgentPipelineRun(id=w.older, test_run_id=w.run_r, workflow_type="investigation", status="failed",
                                    created_at=now - timedelta(hours=2), started_at=now - timedelta(hours=2)))
            db.add(AgentPipelineRun(id=w.newer, test_run_id=w.run_r, workflow_type="offline", status="completed",
                                    created_at=now - timedelta(hours=1), started_at=now - timedelta(hours=1),
                                    completed_at=now - timedelta(minutes=59)))
        w.viewer = w.jwt(w.viewer_id)
        yield w


async def _list(w, headers, **params):
    response = await w.client.get(URL, params={"run_id": str(w.run_r), "limit": 100, **params}, headers=headers)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


async def test_a_member_of_two_projects_reads_the_runs_pipelines_by_run(world):
    w = world
    assert await _list(w, w.viewer) == [str(w.newer), str(w.older)]


async def test_naming_another_member_project_keeps_the_documented_intersection(world):
    w = world
    # What the Evidence tab used to send with Q selected: a member's project, so no 403, and no rows.
    assert await _list(w, w.viewer, project_id=str(w.Q)) == []
    assert await _list(w, w.viewer, project_id=str(w.P)) == [str(w.newer), str(w.older)]


async def test_a_non_member_gets_nothing_without_a_project(world):
    w = world
    # The outsider is a member of Q only: the run-only read the client now makes still hides P's pipelines.
    assert await _list(w, w.outsider) == []


async def test_an_admin_reads_every_pipeline_of_the_run(world):
    w = world
    assert await _list(w, w.admin) == [str(w.newer), str(w.older)]
