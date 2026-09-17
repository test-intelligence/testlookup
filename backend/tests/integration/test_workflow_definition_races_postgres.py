"""Real PostgreSQL races for immutable workflow-definition authority."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.postgres import WorkflowDefinition
from app.services import workflow_definition_service as svc

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _body(workflow_id: str, name: str) -> svc.WorkflowBodyV1:
    return svc.WorkflowBodyV1.model_validate({
        "workflow_id": workflow_id,
        "name": name,
        "base": "live",
        "steps": [
            {"id": "ingestion", "agent_id": "agent.ingestion.v1"},
            {"id": "summary", "agent_id": "agent.summary.v1"},
        ],
        "edges": [{"from": "ingestion", "to": "summary"}],
    })


async def test_publish_and_edit_serialize_into_immutable_versions() -> None:
    """A concurrent edit must become v2, never alter the version being published."""
    engine = create_async_engine(_dsn(), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    workflow_id = f"wf.m13.race_{uuid.uuid4().hex}"
    editor_started = asyncio.Event()
    editor_finished = asyncio.Event()
    try:
        async with engine.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO projects (id,name,slug,is_active) "
                    "VALUES (:id,'M13 workflow race',:slug,true)"
                ),
                {"id": project_id, "slug": f"m13-workflow-{uuid.uuid4().hex}"},
            )
        async with sessions() as db:
            await svc.create_definition(
                db, project_id, _body(workflow_id, "Original"), actor_id=None
            )
            await db.commit()

        async def edit() -> None:
            async with sessions() as db:
                editor_started.set()
                await svc.update_definition(
                    db, project_id, workflow_id, _body(workflow_id, "Edited"), actor_id=None
                )
                await db.commit()
                editor_finished.set()

        async with sessions() as publisher:
            await svc.lock_definition(publisher, project_id, workflow_id)
            publishing = await svc.get_definition(publisher, project_id, workflow_id)
            assert isinstance(publishing, WorkflowDefinition)
            edit_task = asyncio.create_task(edit())
            await editor_started.wait()
            await asyncio.sleep(0.05)
            assert not editor_finished.is_set(), "the edit escaped the publication lock"
            await svc.publish_definition(publisher, publishing)
            await publisher.commit()
        await edit_task

        async with sessions() as db:
            rows = (
                await db.execute(
                    select(WorkflowDefinition)
                    .where(
                        WorkflowDefinition.project_id == project_id,
                        WorkflowDefinition.workflow_id == workflow_id,
                    )
                    .order_by(WorkflowDefinition.version)
                )
            ).scalars().all()
        assert [(row.version, row.status, row.name) for row in rows] == [
            (1, "published", "Original"),
            (2, "draft", "Edited"),
        ]
    finally:
        async with engine.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        await engine.dispose()
