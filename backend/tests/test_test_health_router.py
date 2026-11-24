"""D-50: the flaky-coach Refresh also queues this project's flakiness-score recompute.

The four-signal score had ONE writer, the 06:10 UTC beat, so a new or freshly
imported project read "Not scored yet" for up to a day and Refresh did not help.
The enqueue is checked against the REAL task signature (``inspect.signature(
task.run).bind``), so a payload the worker would reject fails here too -- a
blind ``apply_async`` mock would accept anything.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers import test_health
from app.worker import celery_app as celery_module
from app.worker.tasks import recompute_flaky_scores


def _db_with_project() -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(id=uuid.uuid4())
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


def _record_publishes(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def apply_async(args=None, kwargs=None, **options):
        # The worker calls ``run(*args, **kwargs)``: bind exactly that.
        inspect.signature(recompute_flaky_scores.run).bind(*(args or ()), **(kwargs or {}))
        sent.append({"args": args, "kwargs": kwargs, "options": options})
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(recompute_flaky_scores, "apply_async", apply_async)
    return sent


@pytest.mark.asyncio
async def test_refresh_queues_the_score_recompute_for_this_project_only(monkeypatch):
    project_id = uuid.uuid4()
    sent = _record_publishes(monkeypatch)
    monkeypatch.setattr(test_health, "refresh_flaky_coach", AsyncMock(return_value=3))

    body = await test_health.refresh_project_flaky_coach(
        project_id, days=30, current_user=MagicMock(), _=MagicMock(), db=_db_with_project(),
    )

    assert body == {"status": "completed", "flaky_tests_found": 3, "scores_queued": True}
    # One publish, scoped to the project: a missing project_id would sweep every project.
    assert sent == [{"args": None, "kwargs": {"project_id": str(project_id)}, "options": {}}]


@pytest.mark.asyncio
async def test_a_broker_outage_does_not_fail_the_committed_refresh(monkeypatch):
    def down(*_a, **_k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(recompute_flaky_scores, "apply_async", down)
    monkeypatch.setattr(test_health, "refresh_flaky_coach", AsyncMock(return_value=0))
    db = _db_with_project()

    body = await test_health.refresh_project_flaky_coach(
        uuid.uuid4(), days=30, current_user=MagicMock(), _=MagicMock(), db=db,
    )

    assert body["scores_queued"] is False
    db.commit.assert_awaited_once()


def test_the_recompute_runs_on_the_default_queue():
    """The homelab worker pool consumes ``default``; the task has no route of its own."""
    router = celery_module.celery_app.amqp.router
    route = router.route({}, recompute_flaky_scores.name, args=(), kwargs={"project_id": "x"})
    assert route["queue"].name == "default"
