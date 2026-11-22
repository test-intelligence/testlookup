"""An override rebuilds the run's snapshot itself, and names the run by its build (D-99, S-B-3, D-100).

``POST /release-readiness/{run_id}/override`` deletes the run's intelligence
snapshot (``stage_invalidate``), which also clears ``test_runs.ai_recommendation``,
the column behind the Runs list and its ``?recommendation=`` filter. Nothing
rebuilt it: the Runs list showed "—" and the filter missed the run until someone
opened its Analysis tab. The route now queues ``rebuild_run_snapshot`` after
its commit.

The enqueue is recorded with the task's REAL argument check (``__header__``)
and signature, not a bare mock: a mocked ``apply_async`` cannot see a payload
that does not fit the task.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import ReleaseCouncilOverrideRequest, ReleaseCouncilResponse

RUN = uuid.UUID("11111111-2222-3333-4444-555555555555")
PROJECT = uuid.UUID("99999999-8888-7777-6666-555555555555")


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    def __init__(self, log):
        self.log = log

    async def execute(self, _statement, *_args):
        return _Result(PROJECT)

    async def commit(self):
        self.log.append("commit")


@pytest.fixture
def world(monkeypatch):
    from app.routers import release_readiness as route
    from app.worker import tasks

    log: list = []
    council = ReleaseCouncilResponse(
        run_id=str(RUN),
        recommendation="NO_GO",
        risk_score=40,
        human_override="Payments regression found in manual check",
        original_recommendation="GO",
        build_number="build-412",
        override_audit=[{
            "timestamp": "2026-11-21T10:00:00+00:00",
            "before_recommendation": "GO",
            "before_risk_score": 40,
            "after_recommendation": "NO_GO",
            "reason": "manual check found a bug",
        }],
    )
    monkeypatch.setattr(route, "apply_override", AsyncMock(return_value=council))
    monkeypatch.setattr(
        "app.services.intelligence_snapshot_service.stage_invalidate",
        AsyncMock(side_effect=lambda *_a, **_k: log.append("invalidate")),
    )
    activity = AsyncMock(return_value=None)
    monkeypatch.setattr(route, "record_activity", activity)
    monkeypatch.setattr(route.gate_notifications, "stage_override_no_go", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.services.release_decision_webhook.emit_release_decided", AsyncMock(return_value=None)
    )

    task = tasks.rebuild_run_snapshot
    enqueued: list[dict] = []

    def apply_async(args=None, kwargs=None, **_options):
        args, kwargs = tuple(args or ()), dict(kwargs or {})
        # Celery's own argument check (what apply_async runs before sending), and the function's.
        task.__header__(*args, **kwargs)
        enqueued.append(inspect.signature(task.run).bind(*args, **kwargs).arguments)
        log.append("enqueue")

    monkeypatch.setattr(task, "apply_async", apply_async)
    return SimpleNamespace(route=route, log=log, enqueued=enqueued, activity=activity)


async def _override(world):
    user = SimpleNamespace(id=uuid.uuid4(), username="dana.qa", full_name="Dana Quinn")
    body = ReleaseCouncilOverrideRequest(override_recommendation="NO_GO", reason="manual check found a bug")
    return await world.route.override_release_decision(
        run_id=RUN, body=body, current_user=user, _=user, db=_Session(world.log),
    )


@pytest.mark.asyncio
async def test_an_override_queues_the_snapshot_rebuild_after_its_commit(world):
    await _override(world)

    assert world.enqueued == [{"run_id": str(RUN)}]
    assert world.log == ["invalidate", "commit", "enqueue"], (
        "the rebuild must be queued after the commit, or it can read the pre-override row"
    )


@pytest.mark.asyncio
async def test_a_broker_failure_does_not_fail_the_committed_override(world, monkeypatch):
    from app.worker import tasks

    def broken(*_a, **_k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.rebuild_run_snapshot, "apply_async", broken)
    council = await _override(world)
    assert council.recommendation == "NO_GO"
    assert world.log == ["invalidate", "commit"]


@pytest.mark.asyncio
async def test_the_activity_entry_names_the_run_by_its_build_number(world):
    """D-100: the Activity log read "Release decision for 1111…5555 overridden"."""
    await _override(world)

    kwargs = world.activity.await_args.kwargs
    assert kwargs["entity_label"] == "build-412"
    assert kwargs["entity_id"] == RUN
    assert kwargs["context"]["run_id"] == str(RUN)


@pytest.mark.asyncio
async def test_a_run_without_a_build_number_falls_back_to_its_id(world):
    world.route.apply_override.return_value = world.route.apply_override.return_value.model_copy(
        update={"build_number": None}
    )
    await _override(world)

    assert world.activity.await_args.kwargs["entity_label"] == str(RUN)
