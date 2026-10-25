"""W14 AGT-08: ``POST /agents/pipelines/trigger`` without a workflow is not pinned.

Before W14 the router resolved ``payload.workflow_id or "offline"``, so a manual
trigger was pinned to built-in offline AT THE ROUTER and a project default could
never reach it. Now a request that names no workflow queues the task with
``workflow_id=None, workflow_version=None`` (the worker resolves the project
default at task start), calls no ``get_published_definition``, records
``workflow_ref: "default:offline"`` and answers ``workflow_source:
"resolved_at_start"``. The ``delay`` kwargs are BOUND to the real task
signature (a mocked ``delay`` alone would hide Celery's argument check).
A NAMED workflow keeps today's path (``tests/test_deep_investigation_gate.py``).
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.schemas import TriggerPipelineRequest

PROJECT = uuid.uuid4()
RUN = uuid.uuid4()


def _bind(kwargs: dict) -> inspect.BoundArguments:
    from app.worker.tasks import run_agent_pipeline

    signature = inspect.signature(run_agent_pipeline.run)
    if next(iter(signature.parameters), None) == "self":
        return signature.bind(None, **kwargs)
    return signature.bind(**kwargs)


class _Session:
    """The run for ``select(TestRun)``; "nothing in progress" for the pipeline read."""

    def __init__(self, run):
        self.run = run
        self.statements: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        sql = str(stmt)
        self.statements.append(sql)
        if "agent_pipeline_runs" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        return SimpleNamespace(scalar_one_or_none=lambda: self.run)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER", full_name="Quinn QA", username="quinn")


async def _trigger(payload, *, published=None):
    from app.routers import agents as agents_router
    from app.services import workflow_definition_service as workflow_svc
    from app.worker.tasks import run_agent_pipeline

    run = SimpleNamespace(id=RUN, project_id=PROJECT, build_number="b-77")
    db = _Session(run)
    calls: list[dict] = []

    def delay(**kwargs):
        _bind(kwargs)          # raises TypeError when the payload does not fit the task
        calls.append(kwargs)
        return SimpleNamespace(id="task-77")

    scope = AsyncMock()
    record = AsyncMock()
    get_published = AsyncMock(return_value=published)
    with patch.object(agents_router, "resolve_project_scope", scope), \
         patch.object(agents_router, "record_activity", record), \
         patch.object(workflow_svc, "get_published_definition", get_published), \
         patch.object(run_agent_pipeline, "delay", delay):
        body = await agents_router.trigger_pipeline(payload, db=db, current_user=_user(), _=None)
    return body, calls, record, get_published, scope, db


@pytest.mark.asyncio
async def test_no_workflow_queues_the_task_unpinned_for_the_worker_to_resolve():
    body, calls, record, get_published, scope, db = await _trigger(TriggerPipelineRequest(test_run_id=RUN))

    get_published.assert_not_awaited()
    scope.assert_awaited_once()
    assert scope.await_args.args[2] == str(PROJECT)       # the run's own project is checked
    [kwargs] = calls
    bound = _bind(kwargs).arguments
    assert bound["workflow_id"] is None
    assert bound["workflow_version"] is None
    assert bound["workflow_type"] == "offline"
    assert bound["test_run_id"] == str(RUN) and bound["project_id"] == str(PROJECT)
    assert body == {
        "message": "Pipeline queued",
        "task_id": "task-77",
        "run_id": str(RUN),
        "workflow_ref": None,
        "workflow_source": "resolved_at_start",
    }
    [event] = [call.kwargs for call in record.await_args_list]
    assert event["event_type"] == "analysis.triggered"
    assert event["context"]["workflow_ref"] == "default:offline"
    assert event["context"]["workflow_type"] == "offline"
    # the in-progress check still ran for the offline base before anything queued
    assert any("agent_pipeline_runs" in s for s in db.statements)


@pytest.mark.asyncio
async def test_a_named_fork_is_still_resolved_and_pinned_at_the_router():
    published = {"base": "offline", "workflow_id": "wf.my-fork", "version": 3}
    body, calls, record, get_published, _scope, _db = await _trigger(
        TriggerPipelineRequest(test_run_id=RUN, workflow_id="wf.my-fork"), published=published,
    )

    get_published.assert_awaited_once()
    assert get_published.await_args.args[1:] == (PROJECT, "wf.my-fork", None)
    [kwargs] = calls
    bound = _bind(kwargs).arguments
    assert bound["workflow_id"] == "wf.my-fork" and bound["workflow_version"] == 3
    assert body == {"message": "Pipeline queued", "task_id": "task-77", "run_id": str(RUN),
                    "workflow_ref": "wf.my-fork@3"}
    assert record.await_args.kwargs["context"]["workflow_ref"] == "wf.my-fork@3"
