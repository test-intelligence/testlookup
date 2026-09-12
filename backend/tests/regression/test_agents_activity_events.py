"""The four agent dispatch endpoints record an activity ledger event.

``trigger_pipeline``, ``bulk_trigger_pipelines``, ``defect_command`` and
``regression_watch`` are project-scoped mutations -- each spends the project's
LLM budget and two of them write agent results into it -- and recorded nothing.
They were tracked as baselined violations of ``backend.activity-coverage``
until E7.4 added the first event to ``routers/agents.py``: the guard asked
"does this router record anywhere?", so all four went stale and nothing
tracked them any more. The guard is now per-endpoint; these tests prove the
rows are actually emitted, which a regex cannot.

Every event here is attempt-mode, and that is load-bearing: none of these
handlers commits the request session, so an outcome event staged on it would
be silently dropped.
"""
from __future__ import annotations

import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import TriggerPipelineRequest
from app.routers import agents as agents_router
from app.services.activity import events as E

_PROJECT = uuid.uuid4()
_OTHER_PROJECT = uuid.uuid4()
_RUN = uuid.uuid4()


def _user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = "QA_ENGINEER"
    user.full_name = "Probe Agent"
    return user


def _fake_tasks_module(task):
    module = types.ModuleType("app.worker.tasks")
    module.run_agent_pipeline = task  # type: ignore[attr-defined]
    return module


def _result(*, scalar=None, rows=None):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=scalar)
    result.all = MagicMock(return_value=rows or [])
    return result


def _recorded(record: AsyncMock) -> list[dict]:
    return [call.kwargs for call in record.await_args_list]


@pytest.mark.parametrize(
    "event_type",
    [
        "analysis.triggered",
        "analysis.bulk_triggered",
        "defect.commander_run",
        "analysis.regression_watch_run",
    ],
)
def test_each_event_is_registered_in_attempt_mode(event_type):
    spec = E.ACTIVITY_EVENTS[event_type]
    assert spec.write_mode == "attempt"


class TestTriggerPipeline:
    @pytest.mark.asyncio
    async def test_a_queued_trigger_records_analysis_triggered(self):
        run = MagicMock(id=_RUN, project_id=_PROJECT, build_number=42)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar=run))
        task = MagicMock()
        task.delay = MagicMock(return_value=MagicMock(id="task-1"))
        record = AsyncMock()
        user = _user()

        with patch.dict(sys.modules, {"app.worker.tasks": _fake_tasks_module(task)}), \
                patch.object(agents_router, "resolve_project_scope", AsyncMock()), \
                patch.object(agents_router, "_latest_in_progress_pipeline", AsyncMock(return_value=None)), \
                patch.object(agents_router, "record_activity", record):
            body = await agents_router.trigger_pipeline(
                TriggerPipelineRequest(test_run_id=_RUN), db=db, current_user=user, _=None
            )

        assert body["task_id"] == "task-1"
        [event] = _recorded(record)
        assert event["event_type"] == "analysis.triggered"
        assert event["project_id"] == _PROJECT
        assert event["entity_id"] == _RUN
        assert event["entity_label"] == "Build 42"
        assert event["actor"].actor_id == user.id

    @pytest.mark.asyncio
    async def test_an_already_running_pipeline_records_nothing(self):
        """Nothing was dispatched, so there is no attempt to record."""
        run = MagicMock(id=_RUN, project_id=_PROJECT, build_number=42)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar=run))
        existing = MagicMock(id=uuid.uuid4(), status="running", attempt=1, next_retry_at=None)
        task = MagicMock()
        record = AsyncMock()

        with patch.dict(sys.modules, {"app.worker.tasks": _fake_tasks_module(task)}), \
                patch.object(agents_router, "resolve_project_scope", AsyncMock()), \
                patch.object(agents_router, "_latest_in_progress_pipeline", AsyncMock(return_value=existing)), \
                patch.object(agents_router, "record_activity", record):
            response = await agents_router.trigger_pipeline(
                TriggerPipelineRequest(test_run_id=_RUN), db=db, current_user=_user(), _=None
            )

        assert response.status_code == 200
        task.delay.assert_not_called()
        record.assert_not_awaited()


class TestBulkTrigger:
    @pytest.mark.asyncio
    async def test_one_row_per_project_counting_only_what_queued(self):
        runs = [
            (uuid.uuid4(), _PROJECT, 1),
            (uuid.uuid4(), _PROJECT, 2),
            (uuid.uuid4(), _PROJECT, 3),
            (uuid.uuid4(), _OTHER_PROJECT, 4),
        ]
        failing_run = runs[2][0]

        def apply_async(*, kwargs, queue):
            if kwargs["test_run_id"] == str(failing_run):
                raise RuntimeError("broker down")

        task = MagicMock()
        task.apply_async = MagicMock(side_effect=apply_async)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(rows=runs))
        record = AsyncMock()

        with patch.dict(sys.modules, {"app.worker.tasks": _fake_tasks_module(task)}), \
                patch.object(agents_router, "get_accessible_project_ids", AsyncMock(return_value=None)), \
                patch.object(agents_router, "record_activity", record):
            body = await agents_router.bulk_trigger_pipelines(
                agents_router.BulkTriggerRequest(run_ids=[r[0] for r in runs]),
                db=db, current_user=_user(), _=None,
            )

        assert body.queued == 3
        events = {e["project_id"]: e for e in _recorded(record)}
        assert set(events) == {_PROJECT, _OTHER_PROJECT}
        assert all(e["event_type"] == "analysis.bulk_triggered" for e in events.values())
        assert events[_PROJECT]["context"]["count"] == 2
        assert events[_OTHER_PROJECT]["context"]["count"] == 1
        assert events[_PROJECT]["entity_id"] == _PROJECT

    @pytest.mark.asyncio
    async def test_nothing_found_records_nothing(self):
        task = MagicMock()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(rows=[]))
        record = AsyncMock()

        with patch.dict(sys.modules, {"app.worker.tasks": _fake_tasks_module(task)}), \
                patch.object(agents_router, "get_accessible_project_ids", AsyncMock(return_value=None)), \
                patch.object(agents_router, "record_activity", record):
            body = await agents_router.bulk_trigger_pipelines(
                agents_router.BulkTriggerRequest(run_ids=[uuid.uuid4()]),
                db=db, current_user=_user(), _=None,
            )

        assert body.queued == 0
        record.assert_not_awaited()


class TestCommandEndpoints:
    @pytest.mark.asyncio
    async def test_defect_command_records_after_the_agent_returns(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar=7))
        record = AsyncMock()
        runner = AsyncMock(return_value={"defect_id": "d-1", "cluster_id": "c-1"})

        with patch.object(agents_router, "_authorize_run_and_project", AsyncMock(return_value=_PROJECT)), \
                patch("app.agents.defect_commander.run_defect_commander", runner), \
                patch.object(agents_router, "record_activity", record):
            await agents_router.defect_command(
                cluster_id="c-1", run_id=_RUN, project_id=_PROJECT,
                db=db, current_user=_user(), _=None,
            )

        [event] = _recorded(record)
        assert event["event_type"] == "defect.commander_run"
        assert event["project_id"] == _PROJECT
        assert event["entity_id"] == _RUN
        assert event["entity_label"] == "Build 7"
        assert event["context"] == {"cluster_id": "c-1", "defect_id": "d-1"}

    @pytest.mark.asyncio
    async def test_defect_command_that_raises_records_nothing(self):
        db = AsyncMock()
        record = AsyncMock()
        runner = AsyncMock(side_effect=RuntimeError("llm down"))

        with patch.object(agents_router, "_authorize_run_and_project", AsyncMock(return_value=_PROJECT)), \
                patch("app.agents.defect_commander.run_defect_commander", runner), \
                patch.object(agents_router, "record_activity", record):
            with pytest.raises(RuntimeError):
                await agents_router.defect_command(
                    cluster_id="c-1", run_id=_RUN, project_id=_PROJECT,
                    db=db, current_user=_user(), _=None,
                )

        record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_regression_watch_records_against_the_scoped_project(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar=None))
        record = AsyncMock()
        runner = AsyncMock(return_value={"new_regression": []})

        with patch.object(agents_router, "_authorize_run_and_project", AsyncMock(return_value=_PROJECT)), \
                patch("app.agents.regression_watchman.run_regression_watchman", runner), \
                patch.object(agents_router, "record_activity", record):
            body = await agents_router.regression_watch(
                run_id=_RUN, project_id=_PROJECT, db=db, current_user=_user(), _=None,
            )

        assert body["run_id"] == str(_RUN)
        [event] = _recorded(record)
        assert event["event_type"] == "analysis.regression_watch_run"
        assert event["project_id"] == _PROJECT
        assert event["entity_id"] == _RUN
        # A run with no build number still gets a usable label, not "Build None".
        assert event["entity_label"] == str(_RUN)
