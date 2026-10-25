"""W14 AGT-12: the worker's half of a tracked bulk job.

* a task of a CANCELLED job that has not started returns at the top of its body
  (``bulk_cancelled``) -- no pipeline row, no admission key, no metric; running
  pipelines are not touched (each keeps its own Stop);
* a cache outage is not a cancellation: the check raising (or the real
  ``bulk_pipeline_job_service.is_cancelled`` meeting a broken Redis) lets the
  task run;
* the job id travels task -> ``run_offline_pipeline`` / ``run_deep_pipeline`` ->
  ``_create_pipeline_run`` (which writes ``execution_metadata.bulk_job_id``;
  ``tests/integration/test_create_pipeline_run_default_postgres.py`` reads it
  back from PostgreSQL, also after the completion rebuild).

Every task call goes through ``run_agent_pipeline.apply`` (Celery binds the
kwargs to the real signature). ``bulk_pipeline_job_service`` is G-B's module and
is imported lazily, inside each test.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

JOB = str(uuid.uuid4())


def _kwargs(**extra) -> dict:
    return {
        "test_run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "build_number": "b-12",
        "workflow_type": "offline",
        "requested_by": str(uuid.uuid4()),
        **extra,
    }


@pytest.fixture
def worker(monkeypatch):
    import app.agents.workflow as workflow
    import app.services.intelligence_snapshot_service as snapshots
    from app.worker import tasks

    calls: dict[str, list] = {"offline": [], "deep": [], "admission": [], "counted": []}

    async def fake_offline(**kwargs):
        calls["offline"].append(kwargs)
        return {"completed_stages": ["summary"], "errors": []}

    async def fake_deep(**kwargs):
        calls["deep"].append(kwargs)
        return {"completed_stages": ["summary"], "errors": []}

    async def admission(*args, **kwargs):
        calls["admission"].append(args)
        return False

    async def released(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow, "run_offline_pipeline", fake_offline)
    monkeypatch.setattr(workflow, "run_deep_pipeline", fake_deep)
    monkeypatch.setattr(tasks, "_pipeline_admission_duplicate", admission)
    monkeypatch.setattr(tasks, "_release_requested_admission", released)
    monkeypatch.setattr(tasks, "_count_pipeline", lambda *a: calls["counted"].append(a))
    monkeypatch.setattr(tasks, "_enqueue_snapshot_rebuild", lambda *a, **k: True)
    monkeypatch.setattr(tasks, "_capture_ai_history_after_pipeline", lambda *a, **k: None)
    monkeypatch.setattr(snapshots, "invalidate", AsyncMock())

    def run(kwargs: dict) -> dict:
        return tasks.run_agent_pipeline.apply(
            kwargs=kwargs, task_id=f"task-{uuid.uuid4().hex[:8]}"
        ).get()

    return run, calls


def test_a_cancelled_jobs_task_returns_before_any_pipeline(worker, monkeypatch):
    from app.services import bulk_pipeline_job_service

    run, calls = worker
    checked = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk_pipeline_job_service, "is_cancelled", checked)

    result = run(_kwargs(bulk_job_id=JOB))

    assert result == {
        "completed_stages": [], "error_count": 0, "duplicate": True, "bulk_cancelled": True,
    }
    checked.assert_awaited_once_with(JOB)
    assert calls["offline"] == [] and calls["deep"] == []      # no pipeline, so no row
    assert calls["admission"] == []                             # not even the admission key
    assert calls["counted"] == []                               # a skip is not a success


def test_a_live_job_runs_and_carries_its_id_to_the_pipeline(worker, monkeypatch):
    from app.services import bulk_pipeline_job_service

    run, calls = worker
    monkeypatch.setattr(bulk_pipeline_job_service, "is_cancelled", AsyncMock(return_value=False))

    run(_kwargs(bulk_job_id=JOB))
    run(_kwargs(bulk_job_id=JOB, workflow_type="deep"))

    [offline] = calls["offline"]
    [deep] = calls["deep"]
    assert offline["bulk_job_id"] == JOB and deep["bulk_job_id"] == JOB
    assert offline["run_overrides"] is None and deep["run_overrides"] is None


def test_the_check_raising_is_not_a_cancellation(worker, monkeypatch):
    from app.services import bulk_pipeline_job_service

    run, calls = worker
    monkeypatch.setattr(
        bulk_pipeline_job_service, "is_cancelled", AsyncMock(side_effect=RuntimeError("redis down")),
    )

    result = run(_kwargs(bulk_job_id=JOB))

    assert "bulk_cancelled" not in result
    assert len(calls["offline"]) == 1


def test_a_broken_redis_lets_the_task_run(worker, monkeypatch):
    """The REAL ``is_cancelled`` answers False on any Redis error (§1.3)."""
    import app.db.redis_client as redis_mod

    run, calls = worker

    def broken():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(redis_mod, "get_redis", broken)

    result = run(_kwargs(bulk_job_id=JOB))

    assert "bulk_cancelled" not in result
    assert len(calls["offline"]) == 1


def test_a_task_without_a_job_never_asks(worker, monkeypatch):
    from app.services import bulk_pipeline_job_service

    run, calls = worker
    checked = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk_pipeline_job_service, "is_cancelled", checked)

    run(_kwargs())

    checked.assert_not_awaited()
    assert calls["offline"][0]["bulk_job_id"] is None


@pytest.mark.asyncio
async def test_the_entry_points_hand_the_job_id_to_create_pipeline_run(monkeypatch):
    """``run_offline_pipeline`` / ``run_deep_pipeline`` pass both W14 values to
    every ``_create_pipeline_run`` call (new pipeline and durable create)."""
    import app.agents.workflow as workflow

    seen: list[dict] = []

    class _Stop(Exception):
        pass

    async def recorder(*args, **kwargs):
        seen.append({"args": args, **kwargs})
        raise _Stop

    monkeypatch.setattr(workflow, "_create_pipeline_run", recorder)
    overrides = {"failure_ids": [str(uuid.uuid4())], "agent_patches": {}}
    for entry in (workflow.run_offline_pipeline, workflow.run_deep_pipeline):
        with pytest.raises(_Stop):
            await entry(
                test_run_id=str(uuid.uuid4()), project_id=str(uuid.uuid4()), build_number="b",
                bulk_job_id=JOB, run_overrides=overrides,
            )
    assert [(call["bulk_job_id"], call["run_overrides"]) for call in seen] == [
        (JOB, overrides), (JOB, overrides),
    ]
