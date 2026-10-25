"""W14 AGT-08: every trigger that names no workflow reaches the worker unpinned.

The project default is resolved at task start (``_create_pipeline_run``), so
every payload that names no workflow must arrive with ``workflow_id`` None:
the automatic post-finalize job (the outbox's staged ``agent_pipeline``,
``run_downstream_outbox.py``) and ``/agents/pipelines/trigger`` without a
workflow. Each payload is BOUND to ``inspect.signature(run_agent_pipeline.run)``
(a mocked ``apply_async`` alone hides Celery's argument check -- the outbox
once staged ``run_id`` and no analysis ever started), then run through the real
task body (``.apply``) to see what reaches ``run_offline_pipeline``.

``run_agent_pipeline``'s two W14 parameters come LAST and defaulted, so every
pre-W14 payload (``test_pipeline_dedup_retry.py``,
``test_pipeline_retry_scheduling.py``) binds unchanged.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

PRE_W14_PARAMETERS = [
    "test_run_id", "project_id", "build_number", "workflow_type", "workflow_id",
    "workflow_version", "rerun_of", "requested_by",
]


def _signature() -> inspect.Signature:
    from app.worker.tasks import run_agent_pipeline

    return inspect.signature(run_agent_pipeline.run)


def _bind(kwargs: dict) -> inspect.BoundArguments:
    bound = _signature().bind(**kwargs)
    bound.apply_defaults()
    return bound


def test_the_new_parameters_are_last_and_defaulted():
    params = list(_signature().parameters.values())
    assert [p.name for p in params] == [*PRE_W14_PARAMETERS, "bulk_job_id", "run_overrides"]
    assert params[-2].default is None and params[-1].default is None
    # A pre-W14 positional call still binds: nothing moved.
    _signature().bind("run", "project", "build", "offline", None, None, None, None)


@pytest.fixture
def worker(monkeypatch):
    """The real task body; the pipeline itself is replaced by a recorder."""
    import app.agents.workflow as workflow
    import app.services.intelligence_snapshot_service as snapshots
    from app.worker import tasks

    reached: list[dict] = []

    async def fake_offline(**kwargs):
        reached.append(kwargs)
        return {"completed_stages": ["summary"], "errors": []}

    async def not_a_duplicate(*_args, **_kwargs):
        return False

    async def released(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow, "run_offline_pipeline", fake_offline)
    monkeypatch.setattr(tasks, "_pipeline_admission_duplicate", not_a_duplicate)
    monkeypatch.setattr(tasks, "_release_requested_admission", released)
    monkeypatch.setattr(tasks, "_enqueue_snapshot_rebuild", lambda *a, **k: True)
    monkeypatch.setattr(tasks, "_capture_ai_history_after_pipeline", lambda *a, **k: None)
    monkeypatch.setattr(snapshots, "invalidate", AsyncMock())

    def run(kwargs: dict) -> dict:
        _bind(kwargs)  # raises TypeError for a payload the task refuses
        tasks.run_agent_pipeline.apply(kwargs=kwargs, task_id=f"task-{uuid.uuid4().hex[:8]}").get()
        return reached[-1]

    return run


def _assert_unpinned(reached: dict) -> None:
    assert reached["workflow_id"] is None
    assert reached["workflow_version"] is None
    assert reached["bulk_job_id"] is None
    assert reached["run_overrides"] is None


def test_the_outbox_agent_pipeline_payload_binds_unpinned(monkeypatch, worker):
    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    staged = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "stage_downstream_operation", staged)
    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="build-42", pass_rate=75.0,
        total_tests=4, failed_tests=1, end_time=datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc),
    )
    # Staged on its own loop: the task body below runs its own (``_run_async``).
    asyncio.run(service.stage_finalize_operations(
        MagicMock(), run=run, project=SimpleNamespace(name="payments"), run_ai=True,
    ))
    [payload] = [
        call.kwargs["payload"] for call in staged.await_args_list
        if call.kwargs["operation"] == "agent_pipeline"
    ]
    # What the relay publishes for that row, with Celery's own call shape.
    apply_async = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "apply_async", apply_async)
    service._publish_downstream(SimpleNamespace(
        id=uuid.uuid4(), operation="agent_pipeline", payload=payload,
        queue="ai_analysis", priority=6, dispatch_token=uuid.uuid4(),
    ))
    sent = apply_async.call_args.kwargs["kwargs"]
    bound = _bind(sent).arguments
    assert bound["workflow_type"] == "offline"
    assert bound["workflow_id"] is None and bound["workflow_version"] is None
    # ``.apply`` runs the task body in-process (it never calls apply_async).
    _assert_unpinned_after_running(worker, sent)


def _assert_unpinned_after_running(worker, kwargs: dict) -> None:
    reached = worker(kwargs)
    _assert_unpinned(reached)
    assert reached["test_run_id"] == kwargs["test_run_id"]


def test_the_router_no_workflow_payload_reaches_the_pipeline_unpinned(worker):
    # routers/agents.py trigger_pipeline's delay(...) kwargs when no workflow is named (§2.3).
    kwargs = {
        "test_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "build_number": "b-77",
        "workflow_type": "offline",
        "workflow_id": None,
        "workflow_version": None,
        "requested_by": str(uuid.uuid4()),
    }
    _assert_unpinned_after_running(worker, kwargs)
