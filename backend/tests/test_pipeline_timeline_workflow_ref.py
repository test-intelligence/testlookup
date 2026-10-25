"""W14 AGT-05 / AGT-08: the timeline answer names the workflow that ran.

``workflow_ref`` and ``workflow_source`` come from the pipeline row the route
already loaded (``execution_metadata.workflow_ref`` /
``workflow_default_source``) -- no new statement -- and are ``None`` on a
legacy row that never recorded them (never a guessed ``offline@1``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.schemas import PipelineTimelineResponse


class _Session:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.statements: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.statements.append(sql)
        if "agent_stage_results" in sql:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        return SimpleNamespace(scalar_one_or_none=lambda: self.pipeline)


def _pipeline(metadata):
    t0 = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), workflow_type="offline",
        status="completed", started_at=t0, completed_at=t0, execution_metadata=metadata,
    )


async def _timeline(pipeline):
    from app.routers import agents as agents_router

    db = _Session(pipeline)
    with patch.object(agents_router, "get_accessible_project_ids", AsyncMock(return_value=None)), \
         patch("app.services.agent_cost_service.get_pipeline_cost_summary", AsyncMock(return_value={})), \
         patch("app.services.agent_cost_service.check_alerts", AsyncMock(return_value=[])), \
         patch("app.services.pipeline_event_log.get_pipeline_timeline", AsyncMock(return_value=[])):
        body = await agents_router.get_pipeline_timeline(
            pipeline_id=pipeline.id, db=db, current_user=SimpleNamespace(id=uuid.uuid4(), role="admin"),
        )
    return body, db


@pytest.mark.asyncio
async def test_the_timeline_carries_the_recorded_workflow_ref_and_source():
    body, db = await _timeline(_pipeline({
        "workflow_ref": "wf.my-fork@1",
        "workflow_default_source": "project_default",
    }))
    parsed = PipelineTimelineResponse.model_validate(body)
    assert parsed.workflow_ref == "wf.my-fork@1"
    assert parsed.workflow_source == "project_default"
    # The two fields cost no statement: the pipeline load and the stage read only.
    assert len(db.statements) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, {}, {"tools_used": []}])
async def test_a_legacy_row_answers_none_for_both(metadata):
    body, _ = await _timeline(_pipeline(metadata))
    parsed = PipelineTimelineResponse.model_validate(body)
    assert parsed.workflow_ref is None
    assert parsed.workflow_source is None
