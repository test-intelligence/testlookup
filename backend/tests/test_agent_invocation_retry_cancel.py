"""E1.2 (slice 2): retry and cancel an invocation, and read its output.

The routes apply the pipeline rules to the invocation's run:

* retry: 409 while in progress, after a clean finish, at the attempt ceiling,
  or when the configuration changed (the last three carry ``links.rerun``);
  otherwise resume the SAME run -- which keeps the restricted plan -- after the
  commit. A lost dispatch is sent to a worker again with a fresh dispatch clock.
* cancel: 409 before the run exists or after it finished; otherwise ask the run
  to stop and record it.
* the poll view carries the invoked stage's output once that stage completed.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value, all=lambda: [] if self._value is None else [self._value])


class _Db:
    """Answers each query by the table it reads, so the tests do not depend on query order."""

    def __init__(self, *, invocation, pipeline=None, review=None, stage=None, build="42"):
        self.rows = {
            "agent_invocations": invocation,
            "agent_pipeline_runs": pipeline,
            "review_requests": review,
            "agent_stage_results": stage,
            "test_runs": build,
        }
        self.order: list[str] = []
        self.commit = AsyncMock(side_effect=lambda: self.order.append("commit"))

    async def execute(self, stmt):
        sql = str(stmt)
        for table, value in self.rows.items():
            if f"FROM {table}" in sql:
                return _Result(value)
        raise AssertionError(f"unexpected query: {sql}")


def _invocation(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid.uuid4(), "project_id": uuid.uuid4(), "agent_id": "agent.summary.v1",
        "stage_name": "summary", "test_run_id": uuid.uuid4(), "pipeline_run_id": uuid.uuid4(),
        "workflow_type": "offline", "mode": "async", "created_at": now, "dispatched_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _pipeline(invocation, **overrides):
    base = {
        "id": invocation.pipeline_run_id, "status": "failed", "attempt": 1, "max_attempts": 5,
        "next_retry_at": None, "error": "boom", "review_policy": "required", "execution_metadata": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def harness(monkeypatch):
    pytest.importorskip("celery")
    from app.routers import agent_invoke as router
    from app.worker import tasks

    dispatched: dict[str, MagicMock] = {}

    def _dispatcher(name, db_holder):
        mock = MagicMock(side_effect=lambda **_kw: db_holder["db"].order.append(f"dispatch:{name}"))
        dispatched[name] = mock
        return mock

    holder: dict = {}
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", _dispatcher("resume", holder))
    monkeypatch.setattr(tasks.run_agent_invocation, "apply_async", _dispatcher("invocation", holder))
    monkeypatch.setattr(router, "record_activity", AsyncMock())
    monkeypatch.setattr(router, "ActorRef", SimpleNamespace(from_user=lambda _u: "actor"))
    monkeypatch.setattr(router, "_current_mode_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "_invocation_config_changed", AsyncMock(return_value=False))
    monkeypatch.setattr(router, "decide_retry_mode", lambda _meta, _snap: SimpleNamespace(is_rerun=False, reason="config_unchanged"))

    def _use(db):
        holder["db"] = db
        return db

    return SimpleNamespace(router=router, tasks=tasks, dispatched=dispatched, use=_use)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="qa@example.com")


# -- retry ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["wrong_category", "unsupported_claim", "missing_evidence", "contradiction", "stale_data", "other"])
@pytest.mark.parametrize("scenario", ["unchanged", "changed", "ceiling"])
async def test_retry_refuses_review_rejected_invocation_without_side_effects(harness, monkeypatch, reason, scenario):
    invocation = _invocation()
    pipeline = _pipeline(invocation, error=f"review_rejected: {reason}", attempt=5 if scenario == "ceiling" else 1)
    before = vars(pipeline).copy()
    db = harness.use(_Db(invocation=invocation, pipeline=pipeline))
    monkeypatch.setattr(
        harness.router, "decide_retry_mode",
        lambda _m, _s: SimpleNamespace(is_rerun=scenario == "changed", reason="config_changed"),
    )

    with pytest.raises(HTTPException) as exc:
        await harness.router.retry_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "review_rejected"
    assert exc.value.detail["links"]["rerun"] == "/api/v1/agents/agent.summary.v1/invoke"
    assert vars(pipeline) == before
    assert db.order == []
    harness.router.record_activity.assert_not_awaited()
    harness.dispatched["resume"].assert_not_called()
    harness.dispatched["invocation"].assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pipeline_fields, rerun_link, fragment",
    [
        ({"status": "running"}, False, "still in progress"),
        ({"status": "passed"}, True, "finished successfully"),
        ({"status": "failed", "attempt": 5, "max_attempts": 5}, True, "attempt ceiling"),
    ],
)
async def test_retry_is_refused_by_the_pipeline_rules(harness, pipeline_fields, rerun_link, fragment):
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=_pipeline(invocation, **pipeline_fields)))

    with pytest.raises(HTTPException) as exc:
        await harness.router.retry_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)

    assert exc.value.status_code == 409 and fragment in exc.value.detail["message"]
    assert ("links" in exc.value.detail) is rerun_link
    if rerun_link:
        assert exc.value.detail["links"]["rerun"] == "/api/v1/agents/agent.summary.v1/invoke"
    assert db.order == [], "a refused retry neither commits nor dispatches"


@pytest.mark.asyncio
async def test_retry_is_refused_when_the_configuration_changed(harness, monkeypatch):
    monkeypatch.setattr(
        harness.router, "decide_retry_mode", lambda _m, _s: SimpleNamespace(is_rerun=True, reason="config_changed"),
    )
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=_pipeline(invocation)))

    with pytest.raises(HTTPException) as exc:
        await harness.router.retry_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "config_changed" and "rerun" in exc.value.detail["links"]
    assert db.order == []


@pytest.mark.asyncio
async def test_retry_is_refused_when_the_frozen_agent_config_changed(harness):
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=_pipeline(invocation)))
    harness.router._invocation_config_changed.return_value = True

    with pytest.raises(HTTPException) as exc:
        await harness.router.retry_invocation(
            invocation_id=invocation.id,
            db=db,
            current_user=_user(),
            _=None,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "agent_config_changed"
    assert exc.value.detail["links"]["rerun"].endswith("/invoke")
    assert db.order == []


@pytest.mark.asyncio
async def test_retry_resumes_the_same_run_after_the_commit(harness):
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=_pipeline(invocation)))

    await harness.router.retry_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)

    assert db.order == ["commit", "dispatch:resume"]
    call = harness.dispatched["resume"].call_args.kwargs
    assert call["kwargs"]["pipeline_run_id"] == str(invocation.pipeline_run_id)
    assert call["queue"] == "ai_analysis"
    inspect.signature(harness.tasks.resume_agent_pipeline.run).bind(**call["kwargs"])
    activity = harness.router.record_activity.await_args.kwargs
    assert activity["event_type"] == "analysis.retried" and activity["context"]["mode"] == "resume"


@pytest.mark.asyncio
async def test_a_lost_dispatch_is_sent_again_with_a_fresh_dispatch_clock(harness):
    old = datetime.now(timezone.utc) - harness.router.DISPATCH_GRACE - timedelta(minutes=1)
    invocation = _invocation(created_at=old, dispatched_at=old)
    db = harness.use(_Db(invocation=invocation, pipeline=None))

    view = await harness.router.retry_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)

    assert db.order == ["commit", "dispatch:invocation"]
    kwargs = harness.dispatched["invocation"].call_args.kwargs["kwargs"]
    assert kwargs == {"invocation_id": str(invocation.id)}
    inspect.signature(harness.tasks.run_agent_invocation.run).bind(**kwargs)
    assert invocation.created_at == old, "the request time is not rewritten"
    assert invocation.dispatched_at > old
    assert view["status"] == "in_progress"
    assert harness.router.record_activity.await_args.kwargs["context"]["mode"] == "redispatch"


@pytest.mark.asyncio
async def test_retry_is_refused_for_an_invocation_that_has_simply_not_started_yet(harness):
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=None))

    with pytest.raises(HTTPException) as exc:
        await harness.router.retry_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)
    assert exc.value.status_code == 409 and db.order == []


# -- cancel ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_is_refused_before_the_run_exists(harness):
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=None))

    with pytest.raises(HTTPException) as exc:
        await harness.router.cancel_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)
    assert exc.value.status_code == 409 and db.order == []


@pytest.mark.asyncio
async def test_cancel_is_refused_after_the_run_finished(harness, monkeypatch):
    invocation = _invocation()
    db = harness.use(_Db(invocation=invocation, pipeline=_pipeline(invocation, status="passed")))
    outcome = SimpleNamespace(
        accepted=False, terminal=True, status="passed", reason="already terminal",
        as_dict=lambda: {"accepted": False, "status": "passed"},
    )
    monkeypatch.setattr(harness.router, "request_cancel", AsyncMock(return_value=outcome))

    with pytest.raises(HTTPException) as exc:
        await harness.router.cancel_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)
    assert exc.value.status_code == 409 and db.order == []


@pytest.mark.asyncio
async def test_cancel_asks_the_run_to_stop_and_records_it(harness, monkeypatch):
    invocation = _invocation()
    pipeline = _pipeline(invocation, status="running")
    db = harness.use(_Db(invocation=invocation, pipeline=pipeline))
    outcome = SimpleNamespace(
        accepted=True, terminal=False, status="running", reason="by qa@example.com",
        as_dict=lambda: {"accepted": True},
    )
    request_cancel = AsyncMock(return_value=outcome)
    monkeypatch.setattr(harness.router, "request_cancel", request_cancel)

    await harness.router.cancel_invocation(invocation_id=invocation.id, db=db, current_user=_user(), _=None)

    assert request_cancel.await_args.args[1] == pipeline.id
    assert db.order == ["commit"]
    activity = harness.router.record_activity.await_args.kwargs
    assert activity["event_type"] == "analysis.cancelled"
    assert activity["context"]["invocation_id"] == str(invocation.id)


# -- output and wiring ---------------------------------------------------------------------


def test_the_view_carries_the_stage_output_only_once_it_completed():
    from app.routers.agent_invoke import project_invocation

    invocation = _invocation()
    pipeline = _pipeline(invocation, status="completed")
    done = SimpleNamespace(status="completed", checkpoint_data={"executive_summary": "ok"}, result_data=None)
    running = SimpleNamespace(status="running", checkpoint_data={"partial": True}, result_data=None)

    assert project_invocation(invocation, pipeline, None, stage=done)["output"] == {"executive_summary": "ok"}
    assert project_invocation(invocation, pipeline, None, stage=running)["output"] is None
    assert project_invocation(invocation, pipeline, None)["output"] is None


@pytest.mark.parametrize("handler_name", ["retry_invocation", "cancel_invocation"])
def test_retry_and_cancel_need_the_role_and_the_access_guard(handler_name):
    from app.routers import agent_invoke as router

    params = inspect.signature(getattr(router, handler_name)).parameters
    assert "require_invocation_access" in getattr(params["_"].default.dependency, "__qualname__", "")
    assert "require_role" in getattr(params["current_user"].default.dependency, "__qualname__", "")


def test_the_dispatch_clock_migration_follows_the_invocations_table():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "migrations/versions/0177_agent_invocation_dispatched_at.py").read_text(encoding="utf-8")
    assert 'down_revision = "0176"' in source
    assert 'op.add_column("agent_invocations", sa.Column("dispatched_at"' in source
