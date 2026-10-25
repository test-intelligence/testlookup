"""Evaluating and publishing a fork through the app on real PostgreSQL never answers 500.

Found in the W14 walk (AGT-08 needs a PUBLISHED fork to become a project default): the evaluation and the publish
flush the definition row, which expires its server-side ``updated_at`` (``onupdate=now()``); ``serialize()`` then
lazy-loaded it outside the greenlet (``MissingGreenlet``) and both routes answered 500. The routes now refresh the
row before serialising. The router's unit tests drive a fake session that never expires an attribute, so only a real
session shows it.

Uses ``tests/integration/defect_w06_world.py`` (it awaits ``dispose_engine_for_loop()`` before building its engine and
after disposing it). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.integration.defect_w06_world import defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def world():
    from app.models.postgres import ProjectMember, UserRole
    from app.services import workflow_definition_service as svc

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            w.lead_id = await w.user(db, role=UserRole.QA_LEAD.value, full_name="Publish Lead")
            db.add(ProjectMember(project_id=w.P, user_id=w.lead_id, role=UserRole.QA_LEAD.value))
            await db.flush()
            row = await svc.fork_definition(
                db, w.P, svc.builtin("offline"), svc.WorkflowForkV1(workflow_id="wf.publish-me", name="Publish me"),
                actor_id=None,
            )
            w.workflow_id, w.version = row.workflow_id, row.version
            w.sha = svc.definition_checksum(row.definition)
        w.lead = w.jwt(w.lead_id)
        yield w


async def _status(w) -> str:
    from app.models.postgres import WorkflowDefinition

    async with w.sessions() as db:
        return (await db.execute(
            select(WorkflowDefinition.status).where(
                WorkflowDefinition.project_id == w.P, WorkflowDefinition.workflow_id == w.workflow_id,
            )
        )).scalar_one()


async def test_evaluating_and_publishing_a_fork_never_answers_500(world):
    w = world
    base = f"/api/v1/projects/{w.P}/workflows/{w.workflow_id}"

    evaluated = await w.client.post(f"{base}/evaluate", json={"version": w.version}, headers=w.lead)
    assert evaluated.status_code == 200, evaluated.text
    manifest = evaluated.json()["manifest_checksum"]

    body = {"version": w.version, "definition_sha256": w.sha}
    published = await w.client.post(f"{base}/publish", json=body, headers=w.lead)
    # The evaluation gate decides (insufficient evidence publishes; a measured regression is a 409 asking for an
    # explicit acceptance) -- never the 500 of an expired attribute.
    assert published.status_code in (200, 409), published.text
    if published.status_code == 409:
        assert await _status(w) == "draft"
        published = await w.client.post(f"{base}/publish", json={
            **body, "accept_regression": True, "reason": "regression test: no replay corpus",
            "eval_manifest_checksum": manifest,
        }, headers=w.lead)
    assert published.status_code == 200, published.text
    answer = published.json()
    assert answer["status"] == "published"
    assert answer["updated_at"] is not None
    assert await _status(w) == "published"
