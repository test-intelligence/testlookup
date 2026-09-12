"""E7.4: the manual ``POST .../retry`` and ``POST .../cancel`` endpoints.

What these hold:

* retry refuses (409) a run that is in progress, a run that succeeded, and a
  run at its attempt ceiling -- and the ceiling refusal carries ``links.rerun``
  so the caller is told what to do instead of only being told "no";
* retry RESUMES under unchanged config and RERUNS under changed config, which
  is the whole point of E7.4: a retry after an operator narrows a tool
  allowlist must not silently replay the old one;
* cancel terminalises a run with no live worker and signals one that has;
* both endpoints are tenant-gated, like every other ``/pipelines/{id}/*`` route.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

PROJECT = uuid.uuid4()
RUN_ID = uuid.uuid4()


def _snapshot(**kw):
    base = {
        "requested": "auto",
        "resolved": "llm",
        "resolution_reason": "ok",
        "provider": "ollama",
        "model": "llama3",
        "offline": True,
        "resolved_at": "2026-09-12T00:00:00+00:00",
    }
    base.update(kw)
    return base


def _pipeline(status="failed", *, attempt=1, max_attempts=5, snapshot=None, plan=True):
    metadata = {"analysis_mode_resolution": snapshot or _snapshot()}
    if plan:
        metadata["initial_workflow_plan"] = {"stages": ["ingestion", "summary"]}
    return SimpleNamespace(
        id=uuid.uuid4(),
        test_run_id=RUN_ID,
        workflow_type="offline",
        status=status,
        attempt=attempt,
        max_attempts=max_attempts,
        next_retry_at=None,
        completed_at=datetime.now(timezone.utc),
        error="boom",
        cancel_requested=False,
        execution_metadata=metadata,
        lease_owner=None,
        lease_expires_at=None,
        fencing_token=None,
    )


class _DB:
    """Serves the pipeline row, the TestRun, and (for cancel) the flag read."""

    def __init__(self, pipeline, *, in_flight=None):
        self.pipeline = pipeline
        self.in_flight = in_flight
        self.committed = False
        self._entity_reads = 0

    async def execute(self, stmt):
        if len(list(getattr(stmt, "selected_columns", []))) == 1:
            return SimpleNamespace(
                scalar_one_or_none=lambda: getattr(self.pipeline, "cancel_requested", False)
            )
        self._entity_reads += 1
        # ``_latest_in_progress_pipeline`` is the only entity read that orders
        # and limits; everything else wants the pipeline itself. Counting calls
        # would misfire, since the cancel path reads the row twice.
        text = str(stmt).upper()
        row = self.in_flight if "ORDER BY" in text else self.pipeline
        return SimpleNamespace(scalar_one_or_none=lambda: row)

    async def get(self, _model, _pk):
        return SimpleNamespace(
            id=RUN_ID, project_id=PROJECT, build_number="b-42"
        )

    async def commit(self):
        self.committed = True


def _user():
    return SimpleNamespace(email="qa@example.com", id=uuid.uuid4())


async def _call_retry(monkeypatch, db, *, snapshot=None):
    from app.agents import workflow
    from app.routers import agents as router
    from app.worker import tasks

    async def _resolve():
        return snapshot or _snapshot()

    monkeypatch.setattr(workflow, "_resolve_analysis_mode_snapshot", _resolve)
    # Neutralised by default so a test that does not care about dispatch does
    # not reach a broker; tests that assert on it install their own mock first
    # and monkeypatch keeps the later binding.
    if not isinstance(tasks.resume_agent_pipeline.apply_async, MagicMock):
        monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", MagicMock())
    if not isinstance(tasks.run_agent_pipeline.delay, MagicMock):
        monkeypatch.setattr(tasks.run_agent_pipeline, "delay", MagicMock())
    with patch.object(router, "_require_pipeline_access", AsyncMock(return_value=None)):
        return await router.retry_pipeline(db.pipeline.id, db=db, current_user=_user())


async def _call_cancel(db):
    from app.routers import agents as router

    with patch.object(router, "_require_pipeline_access", AsyncMock(return_value=None)):
        return await router.cancel_pipeline(db.pipeline.id, db=db, current_user=_user())


# ── retry: refusals ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
async def test_retry_refuses_a_run_still_in_progress(monkeypatch, status):
    db = _DB(_pipeline(status))
    with pytest.raises(HTTPException) as exc:
        await _call_retry(monkeypatch, db)
    assert exc.value.status_code == 409
    assert "in progress" in exc.value.detail["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "passed"])
async def test_retry_refuses_a_run_that_succeeded(monkeypatch, status):
    """Retrying a good run would discard its result without saying so."""
    db = _DB(_pipeline(status))
    with pytest.raises(HTTPException) as exc:
        await _call_retry(monkeypatch, db)
    assert exc.value.status_code == 409
    assert exc.value.detail["links"]["rerun"].endswith("/pipelines/trigger")


@pytest.mark.asyncio
async def test_a_degraded_completed_run_is_retryable(monkeypatch):
    """The rows that used to be ``partial`` (E7.1) finished with failed stages
    and ARE worth retrying."""
    pipeline = _pipeline("completed")
    pipeline.execution_metadata["stage_quality"] = "degraded"
    db = _DB(pipeline)
    result = await _call_retry(monkeypatch, db)
    assert result.mode == "resume"


@pytest.mark.asyncio
async def test_retry_at_the_attempt_ceiling_is_409_with_a_rerun_link(monkeypatch):
    db = _DB(_pipeline("failed", attempt=5, max_attempts=5))
    with pytest.raises(HTTPException) as exc:
        await _call_retry(monkeypatch, db)
    assert exc.value.status_code == 409
    assert exc.value.detail["attempt"] == 5
    assert exc.value.detail["max_attempts"] == 5
    assert exc.value.detail["links"]["rerun"], (
        "a ceiling refusal must tell the caller how to start a fresh run, "
        "not just refuse"
    )


@pytest.mark.asyncio
async def test_retry_refuses_when_another_pipeline_is_already_in_flight(monkeypatch):
    """An automatic retry can fire between the operator's read and their click."""
    in_flight = _pipeline("running")
    db = _DB(_pipeline("failed"), in_flight=in_flight)
    with pytest.raises(HTTPException) as exc:
        await _call_retry(monkeypatch, db)
    assert exc.value.status_code == 409
    assert exc.value.detail["pipeline_run_id"] == str(in_flight.id)


# ── retry: resume vs rerun ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unchanged_config_resumes_the_same_id(monkeypatch):
    from app.worker import tasks

    db = _DB(_pipeline("failed", attempt=2))
    apply_async = MagicMock()
    monkeypatch.setattr(tasks.resume_agent_pipeline, "apply_async", apply_async)

    result = await _call_retry(monkeypatch, db)

    assert result.mode == "resume"
    assert result.pipeline_run_id == str(db.pipeline.id)
    assert result.attempt == 3
    kwargs = apply_async.call_args.kwargs["kwargs"]
    assert kwargs["pipeline_run_id"] == str(db.pipeline.id)
    assert "expected_attempt" not in kwargs, (
        "the stale-attempt guard exists for SCHEDULED retries; a person asking "
        "now must not be refused by it"
    )


@pytest.mark.asyncio
async def test_changed_config_starts_a_new_run_linked_by_rerun_of(monkeypatch):
    """The point of the story: a retry after the config changed must not
    replay checkpoints authorised under the old config."""
    from app.worker import tasks

    db = _DB(_pipeline("failed"))
    delay = MagicMock()
    monkeypatch.setattr(tasks.run_agent_pipeline, "delay", delay)

    result = await _call_retry(monkeypatch, db, snapshot=_snapshot(model="phi3"))

    assert result.mode == "rerun"
    assert result.reason == "config_changed"
    assert result.rerun_of == str(db.pipeline.id)
    assert result.pipeline_run_id is None, (
        "the new id is minted by the worker; handing back a placeholder would "
        "look like an id the caller can poll"
    )
    assert result.links["poll"]
    assert delay.call_args.kwargs["rerun_of"] == str(db.pipeline.id)
    assert delay.call_args.kwargs["workflow_type"] == "offline"


@pytest.mark.asyncio
async def test_an_offline_flip_forces_a_rerun(monkeypatch):
    from app.worker import tasks

    db = _DB(_pipeline("failed", snapshot=_snapshot(offline=True)))
    monkeypatch.setattr(tasks.run_agent_pipeline, "delay", MagicMock())
    result = await _call_retry(monkeypatch, db, snapshot=_snapshot(offline=False))
    assert result.mode == "rerun"


@pytest.mark.asyncio
async def test_a_run_with_no_frozen_plan_reruns_rather_than_doing_nothing(monkeypatch):
    from app.worker import tasks

    db = _DB(_pipeline("failed", plan=False))
    monkeypatch.setattr(tasks.run_agent_pipeline, "delay", MagicMock())
    result = await _call_retry(monkeypatch, db)
    assert result.mode == "rerun"
    assert result.reason == "no_frozen_plan"


# ── cancel ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_terminalises_a_waiting_run_and_commits():
    db = _DB(_pipeline("retry_wait"))
    body = await _call_cancel(db)
    assert body["terminal"] is True
    assert body["public_status"] == "failed"
    assert db.pipeline.status == "failed"
    assert db.committed is True, "the router owns the commit"


@pytest.mark.asyncio
async def test_cancel_of_a_running_run_signals_the_worker():
    db = _DB(_pipeline("running"))
    body = await _call_cancel(db)
    assert body["accepted"] is True
    assert body["terminal"] is False
    assert db.pipeline.cancel_requested is True
    assert db.pipeline.status == "running"


@pytest.mark.asyncio
async def test_cancel_of_a_finished_run_is_409():
    db = _DB(_pipeline("completed"))
    with pytest.raises(HTTPException) as exc:
        await _call_cancel(db)
    assert exc.value.status_code == 409
    assert db.committed is False


# ── tenancy ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["retry_pipeline", "cancel_pipeline"])
async def test_both_endpoints_are_tenant_gated(endpoint):
    """Every ``/pipelines/{id}/*`` route derives access from the owning run's
    project; a new mutating route missing that check is an IDOR."""
    from app.routers import agents as router

    db = _DB(_pipeline("failed"))
    with patch.object(
        router,
        "_require_pipeline_access",
        AsyncMock(side_effect=HTTPException(403, detail="nope")),
    ):
        with pytest.raises(HTTPException) as exc:
            await getattr(router, endpoint)(
                db.pipeline.id, db=db, current_user=_user()
            )
    assert exc.value.status_code == 403


def test_both_endpoints_require_the_qa_engineer_role():
    """A read-only VIEWER must not be able to spend LLM budget or stop a run."""
    import inspect

    from app.routers import agents as router

    for name in ("retry_pipeline", "cancel_pipeline"):
        src = inspect.getsource(getattr(router, name))
        assert "require_role(UserRole.QA_ENGINEER)" in src, f"{name} is unguarded"
