"""Regression: admin manual-trigger maintenance endpoints.

Bug pinned (2026-05-18): operators had no way to fire the Phase 4.5
backfill / drain tasks on demand after a deploy — they had to wait
for the next Celery beat tick (hourly for placeholder backfill, 15
min for unassigned failures, 30s for drain).

Fix: ``app/routers/admin_maintenance.py`` added three POST endpoints
under ``/api/v1/admin/maintenance/*``, ADMIN-only.

What this file pins:

  * Each endpoint queues the right Celery task via ``apply_async``.
  * Successful queue returns ``queued=True`` + ``task_id`` +
    ``task_name``.
  * Dispatch failure → 500 (not a silent return).
  * Authorisation gate (``require_role(ADMIN)``) lives on the route
    decorator; the handler itself doesn't enforce it, so we exercise
    the handler with a mocked admin user.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _admin():
    return SimpleNamespace(id=uuid.uuid4(), role="ADMIN")


@pytest.mark.asyncio
async def test_trigger_placeholder_backfill_queues_celery_task():
    from app.routers.admin_maintenance import trigger_placeholder_backfill

    fake_task = SimpleNamespace(id="celery-task-1")
    with patch("app.worker.tasks.backfill_placeholder_test_cases") as task:
        task.apply_async = MagicMock(return_value=fake_task)
        result = await trigger_placeholder_backfill(
            max_runs_per_project=500, current_user=_admin(),
        )

    assert result["queued"] is True
    assert result["task_id"] == "celery-task-1"
    assert result["task_name"] == "backfill_placeholder_test_cases"
    task.apply_async.assert_called_once()
    kwargs = task.apply_async.call_args.kwargs
    assert kwargs["kwargs"]["max_runs_per_project"] == 500


@pytest.mark.asyncio
async def test_trigger_unassigned_failures_backfill_queues_celery_task():
    from app.routers.admin_maintenance import trigger_unassigned_failures_backfill

    fake_task = SimpleNamespace(id="celery-task-2")
    with patch("app.worker.tasks.backfill_unassigned_failures") as task:
        task.apply_async = MagicMock(return_value=fake_task)
        result = await trigger_unassigned_failures_backfill(
            max_runs_per_project=200, current_user=_admin(),
        )

    assert result["queued"] is True
    assert result["task_id"] == "celery-task-2"
    assert result["task_name"] == "backfill_unassigned_failures"


@pytest.mark.asyncio
async def test_trigger_drain_active_live_sessions_queues_celery_task():
    from app.routers.admin_maintenance import trigger_drain_active_sessions

    fake_task = SimpleNamespace(id="celery-task-3")
    with patch("app.worker.tasks.drain_active_live_sessions") as task:
        task.apply_async = MagicMock(return_value=fake_task)
        result = await trigger_drain_active_sessions(current_user=_admin())

    assert result["queued"] is True
    assert result["task_id"] == "celery-task-3"
    assert result["task_name"] == "drain_active_live_sessions"


@pytest.mark.asyncio
async def test_dispatch_failure_returns_500_not_silent_success():
    """A broken Celery broker must not produce ``queued=True`` —
    operators need the failure surfaced loud."""
    from app.routers.admin_maintenance import trigger_placeholder_backfill
    from fastapi import HTTPException

    with patch("app.worker.tasks.backfill_placeholder_test_cases") as task:
        task.apply_async = MagicMock(side_effect=Exception("broker down"))
        with pytest.raises(HTTPException) as exc:
            await trigger_placeholder_backfill(
                max_runs_per_project=500, current_user=_admin(),
            )
    assert exc.value.status_code == 500
    assert "broker down" in exc.value.detail
