"""W14 AGT-08 on real PostgreSQL, through the app: the project default workflow routes.

* ``GET /projects/{p}/workflows/defaults`` answers both bases to any member
  (a VIEWER) and writes nothing; it is declared before ``GET /{workflow_id}``;
* ``PUT …/defaults/{base}``: a QA_ENGINEER of the project → 403, a QA_LEAD of
  ANOTHER project → 403, a QA_LEAD → 200 and ONE ``workflow.default_set`` row;
  a second PUT replaces the default (one row per ``(project, base)``); another
  project's fork is "Workflow definition not found" (resolved in THIS project
  only); a deep fork as the offline default and a bad base are 422s;
* ``DELETE …/defaults/{base}`` → 204 + ``workflow.default_cleared``; again →
  204 and no second row (nothing changed, nothing recorded);
* deleting the project cascades its default rows.

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0230) and
``REDIS_URL``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from tests.integration.defect_w06_world import StatementCounter, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _fork(db, project_id, workflow_id, *, base="offline", publish=True):
    from app.services import workflow_definition_service as svc

    row = await svc.fork_definition(
        db, project_id, svc.builtin(base), svc.WorkflowForkV1(workflow_id=workflow_id, name=f"Fork {workflow_id}"),
        actor_id=None,
    )
    if publish:
        await svc.publish_definition(db, row)
    return row


@pytest.fixture
async def world():
    from app.models.postgres import ProjectMember, UserRole

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Priya Lead")
            w.lead_q_id = await w.user(db, role=UserRole.QA_LEAD.value)
            w.viewer_id = await w.user(db, role=UserRole.VIEWER.value)
            for uid, pid, role in (
                (w.lead_id, w.P, UserRole.QA_LEAD), (w.lead_q_id, w.Q, UserRole.QA_LEAD),
                (w.viewer_id, w.P, UserRole.VIEWER),
            ):
                db.add(ProjectMember(project_id=pid, user_id=uid, role=role.value))
            await db.flush()
            w.fork = (await _fork(db, w.P, "wf.my-fork")).workflow_id
            w.fork2 = (await _fork(db, w.P, "wf.other-fork")).workflow_id
            w.deep_fork = (await _fork(db, w.P, "wf.deep-fork", base="deep")).workflow_id
            w.q_fork = (await _fork(db, w.Q, "wf.q-fork")).workflow_id
        w.lead, w.lead_q, w.viewer = w.jwt(w.lead_id), w.jwt(w.lead_q_id), w.jwt(w.viewer_id)
        yield w


def _url(pid, base=None):
    path = f"/api/v1/projects/{pid}/workflows/defaults"
    return f"{path}/{base}" if base else path


async def _events(w, event_type):
    from app.models.postgres import ProjectActivityEvent

    async with w.sessions() as db:
        return list((await db.execute(
            select(ProjectActivityEvent)
            .where(ProjectActivityEvent.project_id == w.P, ProjectActivityEvent.event_type == event_type)
            .order_by(ProjectActivityEvent.recorded_at)
        )).scalars().all())


async def _rows(w, pid=None):
    from app.models.postgres import ProjectWorkflowDefault

    async with w.sessions() as db:
        return list((await db.execute(
            select(ProjectWorkflowDefault).where(ProjectWorkflowDefault.project_id == (pid or w.P))
        )).scalars().all())


async def test_a_viewer_reads_both_bases_and_the_read_writes_nothing(world):
    w = world
    with StatementCounter(w.engine) as counter:
        response = await w.client.get(_url(w.P), headers=w.viewer)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(w.P)
    assert [d["base"] for d in body["defaults"]] == ["offline", "deep"]
    assert all(d["source"] == "builtin" and d["state"] == "ok" for d in body["defaults"])
    assert [d["resolved_ref"] for d in body["defaults"]] == ["offline@1", "deep@1"]
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in counter.statements)
    assert (await w.client.get(_url(w.P), headers=w.outsider)).status_code == 403


async def test_who_may_set_a_default_and_the_refusals(world):
    from app.core.agent_ops_vocab import DEFAULT_BASE_INVALID, DEFAULT_BASE_MISMATCH

    w = world
    body = {"workflow_id": w.fork}
    assert (await w.client.put(_url(w.P, "offline"), json=body, headers=w.member)).status_code == 403
    assert (await w.client.put(_url(w.P, "offline"), json=body, headers=w.lead_q)).status_code == 403
    assert (await w.client.put(_url(w.P, "offline"), json=body, headers=w.viewer)).status_code == 403

    response = await w.client.put(_url(w.P, "offline"), json={"workflow_id": w.q_fork}, headers=w.lead)
    assert response.status_code == 404 and response.json()["detail"] == "Workflow definition not found"
    response = await w.client.put(_url(w.P, "offline"), json={"workflow_id": w.deep_fork}, headers=w.lead)
    assert response.status_code == 422
    assert response.json()["detail"] == DEFAULT_BASE_MISMATCH.format(workflow_id=w.deep_fork, actual="deep")
    response = await w.client.put(_url(w.P, "live"), json=body, headers=w.lead)
    assert response.status_code == 422 and response.json()["detail"] == DEFAULT_BASE_INVALID
    response = await w.client.put(_url(w.P, "offline"), json={"workflow_id": "offline"}, headers=w.lead)
    assert response.status_code == 422          # the pattern: only a fork can be a default
    assert await _rows(w) == []
    assert await _events(w, "workflow.default_set") == []


async def test_set_replace_clear_and_clear_again(world):
    w = world
    response = await w.client.put(_url(w.P, "offline"), json={"workflow_id": w.fork}, headers=w.lead)
    assert response.status_code == 200, response.text
    read = response.json()
    assert read["base"] == "offline" and read["source"] == "project_default"
    assert read["workflow_id"] == w.fork and read["workflow_version"] is None
    assert read["resolved_ref"] == f"{w.fork}@1" and read["state"] == "ok"
    assert read["updated_by"] == "Priya Lead"
    [set_event] = await _events(w, "workflow.default_set")
    assert set_event.context["base"] == "offline" and set_event.context["workflow_id"] == w.fork
    assert set_event.context["version"] == "latest" and set_event.context["previous"] == "builtin"

    response = await w.client.put(_url(w.P, "offline"), json={"workflow_id": w.fork2, "workflow_version": 1},
                                  headers=w.lead)
    assert response.status_code == 200, response.text
    [row] = await _rows(w)
    assert (row.base, row.workflow_id, row.workflow_version) == ("offline", w.fork2, 1)
    events = await _events(w, "workflow.default_set")
    assert len(events) == 2 and events[1].context["previous"] == w.fork

    listed = (await w.client.get(_url(w.P), headers=w.viewer)).json()["defaults"]
    assert listed[0]["resolved_ref"] == f"{w.fork2}@1" and listed[1]["source"] == "builtin"

    response = await w.client.delete(_url(w.P, "offline"), headers=w.lead)
    assert response.status_code == 204
    assert await _rows(w) == []
    [cleared] = await _events(w, "workflow.default_cleared")
    assert cleared.context == {"base": "offline", "workflow_id": w.fork2, "version": 1}
    assert cleared.entity_label == f"Fork {w.fork2}"

    response = await w.client.delete(_url(w.P, "offline"), headers=w.lead)
    assert response.status_code == 204
    assert len(await _events(w, "workflow.default_cleared")) == 1
    assert (await w.client.delete(_url(w.P, "offline"), headers=w.member)).status_code == 403
    assert (await w.client.delete(_url(w.P, "nope"), headers=w.lead)).status_code == 422


async def test_deleting_the_project_cascades_its_defaults(world):
    from app.models.postgres import ProjectWorkflowDefault

    w = world
    async with w.sessions.begin() as db:
        pid = await w.project(db)
        db.add(ProjectWorkflowDefault(project_id=pid, base="deep", workflow_id="wf.gone", workflow_version=None))
    assert len(await _rows(w, pid)) == 1
    async with w.sessions.begin() as db:
        await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})
    async with w.sessions() as db:
        left = (await db.execute(
            select(func.count()).select_from(ProjectWorkflowDefault).where(ProjectWorkflowDefault.project_id == pid)
        )).scalar_one()
    assert left == 0
