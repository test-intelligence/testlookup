"""E8.1: Finalize stages a review request exactly when a run finished with a report.

Section 8.1: every run that produced a report gets one; a run that produced
none settled ``passed`` (E7.5) and has nothing to review; a failed run is not a
report anyone should be asked to accept.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PROJECT = uuid.uuid4()


class _Result:
    def __init__(self, *, scalar=None, scalars=()):
        self._scalar = scalar
        self._scalars = list(scalars)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars))

    def first(self):
        return None

    def all(self):
        return []


class _Session:
    """Execute order in _mark_pipeline_done: the run, its stages, then (for a
    report run with no final_state) the owning project. Everything after is empty."""

    def __init__(self, pipeline, stages, project_id=PROJECT):
        self._results = [_Result(scalar=pipeline), _Result(scalars=stages), _Result(scalar=project_id)]
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _statement):
        return self._results.pop(0) if self._results else _Result()

    def add(self, _row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


def _pipeline():
    return SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), workflow_type="offline",
        status="running", started_at=datetime.now(timezone.utc), completed_at=None,
        error=None, execution_metadata={}, review_policy="human_required",
        lease_owner="h:1", fencing_token="tok", lease_expires_at=datetime.now(timezone.utc),
    )


def _stage(name, status="completed"):
    return SimpleNamespace(stage_name=name, status=status)


async def _finalize(monkeypatch, stages, *, success=True, stage_review=None):
    from app.agents import workflow
    from app.services import agent_action_ledger_service, review_request_service, run_downstream_outbox

    pipeline = _pipeline()
    session = _Session(pipeline, stages)
    stage_review = stage_review or AsyncMock(return_value=None)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(review_request_service, "stage_run_review_request", stage_review)
    monkeypatch.setattr(agent_action_ledger_service, "persist_report_action_proposals", AsyncMock())
    monkeypatch.setattr(run_downstream_outbox, "stage_ai_summary_notification_operation", AsyncMock())
    await workflow._mark_pipeline_done(str(pipeline.id), success=success, error=None if success else "boom")
    return pipeline, session, stage_review


@pytest.mark.asyncio
async def test_a_completed_report_run_stages_its_review_request(monkeypatch):
    pipeline, session, stage_review = await _finalize(
        monkeypatch, [_stage("ingestion"), _stage("summary")]
    )

    assert pipeline.status == "completed"
    stage_review.assert_awaited_once()
    kwargs = stage_review.await_args.kwargs
    assert kwargs["run"] is pipeline
    assert kwargs["project_id"] == PROJECT
    assert kwargs["report_stage_names"] == ["summary"]
    assert session.committed is True


@pytest.mark.asyncio
async def test_a_degraded_run_that_still_produced_a_report_is_reviewed(monkeypatch):
    pipeline, _, stage_review = await _finalize(
        monkeypatch, [_stage("summary"), _stage("anomaly_detection", status="failed")]
    )
    assert pipeline.status == "completed"
    stage_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_run_that_passed_without_a_report_gets_no_request(monkeypatch):
    pipeline, _, stage_review = await _finalize(
        monkeypatch, [_stage("ingestion"), _stage("flaky_sentinel")]
    )
    assert pipeline.status == "passed"
    stage_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_run_gets_no_request(monkeypatch):
    pipeline, _, stage_review = await _finalize(monkeypatch, [_stage("summary")], success=False)
    assert pipeline.status == "failed"
    stage_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_review_failure_never_strands_the_run(monkeypatch):
    """The run's terminal state must still commit."""
    pipeline, session, _ = await _finalize(
        monkeypatch, [_stage("summary")],
        stage_review=AsyncMock(side_effect=RuntimeError("constraint violated")),
    )
    assert pipeline.status == "completed"
    assert session.committed is True
