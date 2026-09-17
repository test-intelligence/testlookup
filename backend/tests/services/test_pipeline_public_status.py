"""E7.5: four public statuses, and ``passed`` for runs with nothing to review.

Architecture section 7.1: a report-producing run rests at ``completed`` until a
human accepts its review (E8); a run that produced no report settles
``completed -> passed`` in the finalize transaction, so a client can always wait
for ``passed | failed``. Before this story nothing ever wrote ``passed``.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_capability_registry import (
    CAPABILITY_REGISTRY,
    REPORT_OUTPUT_SCHEMAS,
    is_report_producing,
)
from app.services.workflow_run_state import (
    DEGRADED,
    IN_PROGRESS_STATUSES,
    PUBLIC_STATUS,
    REVIEW_NOT_APPLICABLE,
    passes_without_review,
    public_status,
)

# ── which capabilities produce a report ──────────────────────────────────────


@pytest.mark.parametrize(
    "stage", ["summary", "root_cause_analysis", "decision_report", "report_refinement"]
)
def test_the_report_stages_named_by_the_architecture_are_report_producing(stage):
    assert is_report_producing(stage) is True


@pytest.mark.parametrize(
    "stage", ["ingestion", "anomaly_detection", "flaky_sentinel", "test_health", "triage"]
)
def test_evidence_and_action_stages_are_not(stage):
    assert is_report_producing(stage) is False


def test_an_unregistered_stage_requires_review():
    """Fail closed: a report wrongly auto-passed reaches distribution unread."""
    assert is_report_producing("a_stage_added_without_a_registry_entry") is True


def test_every_report_schema_is_actually_emitted_by_a_registered_capability():
    """A schema named here that no capability emits is a typo that silently
    lets its real stage skip review."""
    emitted = {spec.output_schema for spec in CAPABILITY_REGISTRY.values()}
    assert REPORT_OUTPUT_SCHEMAS <= emitted, REPORT_OUTPUT_SCHEMAS - emitted


# ── the rule ─────────────────────────────────────────────────────────────────


def test_a_clean_run_of_only_non_report_stages_passes_without_review():
    assert passes_without_review({}, ["ingestion", "flaky_sentinel", "test_health"]) is True


def test_any_report_stage_that_ran_keeps_the_run_at_completed():
    assert passes_without_review({}, ["ingestion", "summary"]) is False


def test_a_degraded_run_never_passes_without_review():
    assert passes_without_review({"stage_quality": DEGRADED}, ["ingestion"]) is False


def test_a_run_where_nothing_ran_does_not_pass():
    """Absence is not health: an empty run proves nothing."""
    assert passes_without_review({}, []) is False
    assert passes_without_review(None, None) is False


def test_an_unknown_stage_keeps_the_run_for_review():
    assert passes_without_review({}, ["ingestion", "mystery_stage"]) is False


# ── the in-progress set ──────────────────────────────────────────────────────


def test_in_progress_is_every_non_terminal_internal_state():
    assert set(IN_PROGRESS_STATUSES) == {"pending", "running", "retry_wait"}


def test_the_public_projection_has_exactly_four_values():
    assert set(PUBLIC_STATUS.values()) == {"in_progress", "completed", "failed", "passed"}
    for raw in ("pending", "running", "retry_wait", "completed", "passed", "failed",
                "partial", "cancelled", "bogus", None):
        assert public_status(raw) in {"in_progress", "completed", "failed", "passed"}


def test_only_pending_runs_filter_uses_the_whole_in_progress_set():
    """``== "running"`` missed pending and retry_wait: a run whose pipeline was
    parked for a scheduled retry was offered for a second, concurrent one."""
    from app.routers import runs

    src = inspect.getsource(runs)
    assert 'AgentPipelineRun.status == "running"' not in src
    assert "AgentPipelineRun.status.in_(IN_PROGRESS_STATUSES)" in src


# ── finalize (_mark_pipeline_done) ───────────────────────────────────────────


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
    """First execute loads the run, second its stages, the rest are empty."""

    def __init__(self, pipeline, stages):
        self._results = [_Result(scalar=pipeline), _Result(scalars=stages)]
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

    async def rollback(self):
        pass


def _pipeline():
    return SimpleNamespace(
        id=uuid.uuid4(),
        test_run_id=uuid.uuid4(),
        workflow_type="offline",
        status="running",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        error=None,
        execution_metadata={},
        review_policy="human_required",
        lease_owner="host:1",
        fencing_token="tok",
        lease_expires_at=datetime.now(timezone.utc),
    )


def _stage(name, status="completed"):
    return SimpleNamespace(stage_name=name, status=status)


async def _finalize(monkeypatch, stages):
    from app.agents import workflow
    from app.services import agent_action_ledger_service, run_downstream_outbox

    pipeline = _pipeline()
    session = _Session(pipeline, stages)
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        agent_action_ledger_service, "persist_report_action_proposals", AsyncMock()
    )
    monkeypatch.setattr(
        run_downstream_outbox, "stage_ai_summary_notification_operation", AsyncMock()
    )
    await workflow._mark_pipeline_done(str(pipeline.id), success=True)
    return pipeline, session


@pytest.mark.asyncio
async def test_finalize_settles_a_no_report_run_to_passed(monkeypatch):
    pipeline, session = await _finalize(
        monkeypatch, [_stage("ingestion"), _stage("flaky_sentinel")]
    )
    assert pipeline.status == "passed"
    assert pipeline.review_policy == REVIEW_NOT_APPLICABLE, (
        "a passed run still marked human_required would violate the section 7.2 "
        "invariant that such a run has an accepted review"
    )
    assert pipeline.completed_at is not None
    assert session.committed is True


@pytest.mark.asyncio
async def test_finalize_leaves_a_report_run_at_completed(monkeypatch):
    pipeline, _ = await _finalize(
        monkeypatch, [_stage("ingestion"), _stage("summary")]
    )
    assert pipeline.status == "completed"
    assert pipeline.review_policy == "human_required"


@pytest.mark.asyncio
async def test_finalize_leaves_a_degraded_run_at_completed(monkeypatch):
    pipeline, _ = await _finalize(
        monkeypatch, [_stage("ingestion"), _stage("flaky_sentinel", status="failed")]
    )
    assert pipeline.status == "completed"


@pytest.mark.asyncio
async def test_a_skipped_report_stage_did_not_produce_a_report(monkeypatch):
    """Only stages that reached ``completed`` count as having run."""
    pipeline, _ = await _finalize(
        monkeypatch, [_stage("ingestion"), _stage("summary", status="skipped")]
    )
    assert pipeline.status == "passed"


@pytest.mark.asyncio
async def test_a_failed_run_is_never_passed(monkeypatch):
    from app.agents import workflow
    from app.services import agent_action_ledger_service, run_downstream_outbox

    pipeline = _pipeline()
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: _Session(pipeline, []))
    monkeypatch.setattr(
        agent_action_ledger_service, "persist_report_action_proposals", AsyncMock()
    )
    monkeypatch.setattr(
        run_downstream_outbox, "stage_ai_summary_notification_operation", AsyncMock()
    )
    await workflow._mark_pipeline_done(str(pipeline.id), success=False, error="boom")
    assert pipeline.status == "failed"


@pytest.mark.asyncio
async def test_a_stale_worker_cannot_finalize_the_reapers_new_attempt(monkeypatch):
    """The live T22 pause/reap probe found finalize was the one unfenced write."""
    from sqlalchemy.dialects import postgresql

    from app.agents import workflow
    from app.services.pipeline_lease import LeaseLost

    pipeline = _pipeline()
    pipeline.status = "retry_wait"
    pipeline.fencing_token = "new-token"

    class _StaleSession(_Session):
        def __init__(self):
            super().__init__(pipeline, [])
            self._results = [_Result(scalar=None)]
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return await super().execute(statement)

    session = _StaleSession()
    monkeypatch.setattr(workflow, "AsyncSessionLocal", lambda: session)

    with pytest.raises(LeaseLost):
        await workflow._mark_pipeline_done(
            str(pipeline.id),
            success=True,
            fencing_token="old-token",
        )

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "agent_pipeline_runs.fencing_token = 'old-token'" in sql
    assert "FOR UPDATE" in sql
    assert pipeline.status == "retry_wait"
    assert session.committed is False


@pytest.mark.asyncio
async def test_finalize_preserves_the_frozen_eval_manifest(monkeypatch):
    from app.agents import workflow
    from app.services import agent_action_ledger_service, run_downstream_outbox

    checksum = "a" * 64
    pipeline = _pipeline()
    pipeline.execution_metadata = {"eval_manifest_checksum": checksum}
    monkeypatch.setattr(
        workflow, "AsyncSessionLocal", lambda: _Session(pipeline, [_stage("ingestion")])
    )
    monkeypatch.setattr(
        agent_action_ledger_service, "persist_report_action_proposals", AsyncMock()
    )
    monkeypatch.setattr(
        run_downstream_outbox, "stage_ai_summary_notification_operation", AsyncMock()
    )

    await workflow._mark_pipeline_done(
        str(pipeline.id),
        success=True,
        final_state={
            "project_id": str(uuid.uuid4()),
            "test_run_id": str(pipeline.test_run_id),
        },
    )

    assert pipeline.execution_metadata["eval_manifest_checksum"] == checksum


@pytest.mark.asyncio
async def test_finalize_preserves_the_invocation_config_authority(monkeypatch):
    from app.agents import workflow
    from app.services import agent_action_ledger_service, run_downstream_outbox

    capability = "agent.decision_report.v1"
    accepted_snapshot = {
        capability: {
            "agent_id": capability,
            "config": {"mode": "act"},
        }
    }
    pipeline = _pipeline()
    pipeline.execution_metadata = {
        "workflow_agent_configs": {capability: {"mode": "suggest"}},
        "resolved_agent_configs": accepted_snapshot,
    }
    monkeypatch.setattr(
        workflow, "AsyncSessionLocal", lambda: _Session(pipeline, [_stage("summary")])
    )
    monkeypatch.setattr(
        agent_action_ledger_service, "persist_report_action_proposals", AsyncMock()
    )
    monkeypatch.setattr(
        run_downstream_outbox, "stage_ai_summary_notification_operation", AsyncMock()
    )

    await workflow._mark_pipeline_done(
        str(pipeline.id),
        success=True,
        final_state={
            "project_id": str(uuid.uuid4()),
            "test_run_id": str(pipeline.test_run_id),
        },
    )

    assert pipeline.execution_metadata["resolved_agent_configs"] == accepted_snapshot


# ── the agentic-runtime projection (GET /pipelines/{id}/agentic-runtime) ─────


def _runtime_pipeline(status):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), workflow_type="offline",
        status=status, started_at=now, completed_at=now, error=None,
        execution_metadata={},
    )


@pytest.mark.parametrize(
    "stored, run_status, public, root_task",
    [
        ("retry_wait", "retry_wait", "in_progress", "pending"),
        ("passed", "passed", "passed", "completed"),
        ("running", "running", "in_progress", "running"),
        ("completed", "completed", "completed", "completed"),
        ("failed", "failed", "failed", "failed"),
        ("partial", "completed", "completed", "completed"),
        ("cancelled", "failed", "failed", "failed"),
    ],
)
def test_the_runtime_projection_reports_the_real_status(stored, run_status, public, root_task):
    """Its old allowlist predated retry_wait and passed and reported both as
    ``failed``: a run waiting on a scheduled retry read as dead."""
    from app.services.agentic_runtime_service import build_agentic_run_projection

    projection = build_agentic_run_projection(_runtime_pipeline(stored), [])

    assert projection.status == run_status
    assert projection.public_status == public
    assert projection.terminal_outcome.status == run_status
    assert projection.tasks[0].status == root_task
