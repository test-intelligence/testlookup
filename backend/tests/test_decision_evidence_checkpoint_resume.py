from types import SimpleNamespace

import pytest
import structlog

from app.agents.workflow import (
    _canonical_checksum,
    _claim_pipeline_resume,
    _load_checkpoint,
)


@pytest.fixture(autouse=True)
def _production_structlog():
    """Bind a real ``structlog.BoundLogger``, as ``configure_logging`` does.

    Regression guard. ``_load_checkpoint`` logs its success line and wraps the
    whole body in ``except Exception`` — so a logging call that raises makes the
    function return ``None`` and silently disables checkpoint restore, with only
    a ``checkpoint_load_failed`` warning to show for it.

    ``BoundLogger.info`` is ``(event, **kw)``: passing stdlib-style positional
    ``%s`` args raises ``TypeError``. Unconfigured, structlog hands back a lazy
    proxy that tolerates them, so this bug is INVISIBLE unless something has
    already configured logging — which in the test suite meant "unless another
    test imported ``app.main`` first". That made a real production defect look
    like a test-ordering flake. Configuring it here makes the check
    deterministic and order-independent.
    """
    structlog.configure(
        wrapper_class=structlog.BoundLogger,
        cache_logger_on_first_use=False,
    )
    try:
        yield
    finally:
        structlog.reset_defaults()


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: self.value)


class _Session:
    def __init__(self, previous_run, stages):
        self.results = iter((_Result(previous_run), _Result(stages)))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return next(self.results)


def _stage(stage_name, checkpoint_data, *, pipeline_run_id="old-pipeline", checksum=None):
    output_checksum = checksum or _canonical_checksum(checkpoint_data)
    attempt_key = _canonical_checksum({
        "pipeline_run_id": str(pipeline_run_id),
        "stage_name": stage_name,
        "attempt": 1,
        "output_checksum_sha256": output_checksum,
    })
    return SimpleNamespace(
        pipeline_run_id=pipeline_run_id,
        stage_name=stage_name,
        checkpoint_data=checkpoint_data,
        result_data={
            "_replay": {
                "input_checksum_sha256": "0" * 64,
                "output_checksum_sha256": output_checksum,
                "runtime_versions": {"prompt_registry": "abc123"},
                "attempt": 1,
                "attempt_idempotency_key": attempt_key
            }
        },
    )


@pytest.mark.asyncio
async def test_cross_pipeline_retry_reruns_snapshot_bound_terminal_chain(monkeypatch):
    previous = SimpleNamespace(id="old-pipeline")
    stages = [
        _stage("summary", {"structured_summary": {"source": "old-summary"}}),
        _stage(
            "release_risk",
            {"release_decision": {"pipeline_run_id": "old-pipeline"}},
        ),
        _stage(
            "decision_report",
            {"decision_evidence_snapshot": {"pipeline_run_id": "old-pipeline"}},
        ),
        _stage(
            "decision_report_critic",
            {"decision_report_verification": {"status": "failed"}},
        ),
    ]
    monkeypatch.setattr(
        "app.agents.workflow.AsyncSessionLocal",
        lambda: _Session(previous, stages),
    )

    checkpoint = await _load_checkpoint("run-1", "deep")

    assert checkpoint == {
        "structured_summary": {"source": "old-summary"},
        "_checkpoint_replay_metadata": {
            "summary": {
                "source_pipeline_run_id": "old-pipeline",
                "stage_name": "summary",
                "input_checksum_sha256": "0" * 64,
                "output_checksum_sha256": _canonical_checksum(
                    {"structured_summary": {"source": "old-summary"}}
                ),
                "runtime_versions": {"prompt_registry": "abc123"},
                "attempt": 1,
                "attempt_idempotency_key": _canonical_checksum({
                    "pipeline_run_id": "old-pipeline",
                    "stage_name": "summary",
                    "attempt": 1,
                    "output_checksum_sha256": _canonical_checksum(
                        {"structured_summary": {"source": "old-summary"}}
                    ),
                }),
            }
        },
        "_checkpoint_stages": ["summary"],
    }


class _ResumeSession:
    def __init__(self, pipeline, project_id, stages):
        self.results = iter((_Result(pipeline), _Result(project_id), _Result(stages)))
        self.commit_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return next(self.results)

    async def commit(self):
        self.commit_count += 1


@pytest.mark.asyncio
async def test_same_pipeline_resume_claim_resets_only_incomplete_stages(monkeypatch):
    completed = SimpleNamespace(
        stage_name="summary",
        status="completed",
        attempt=1,
        idempotency_key="a" * 64,
        result_data={"_replay": {"attempt": 1}},
    )
    failed = SimpleNamespace(
        stage_name="decision_report",
        status="failed",
        attempt=2,
        idempotency_key="b" * 64,
        result_data={"old": True},
        started_at="started",
        completed_at="finished",
        error="provider failed",
        stop_reason="capability_error",
        skipped_reason="old reason",
        execution_path="executed",
    )
    pipeline = SimpleNamespace(
        id="pipeline-1",
        status="failed",
        test_run_id="run-1",
        workflow_type="deep",
        execution_metadata={
            "initial_workflow_plan": {"schema_version": 2, "stages": []},
            "cluster_child_settings": {"enabled": False},
            "async_decision_report_supersession_enabled": True,
            "analysis_mode_resolution": {"requested": "rules", "resolved": "rules"},
        },
        started_at=None,
        completed_at="done",
        error="old error",
    )
    session = _ResumeSession(pipeline, "project-1", [completed, failed])
    monkeypatch.setattr("app.agents.workflow.AsyncSessionLocal", lambda: session)

    claimed = await _claim_pipeline_resume("pipeline-1")

    assert claimed["resume_attempt"] == 1
    assert claimed["async_decision_report_supersession_enabled"] is True
    assert pipeline.status == "running"
    assert pipeline.completed_at is None
    assert pipeline.error is None
    assert completed.attempt == 1
    assert completed.status == "completed"
    assert failed.attempt == 3
    assert failed.status == "pending"
    assert failed.idempotency_key is None
    assert failed.started_at is None
    assert failed.completed_at is None
    assert failed.result_data["_resume"]["previous_attempt"] == 2
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_same_pipeline_resume_claim_is_idempotent_for_running_pipeline(monkeypatch):
    pipeline = SimpleNamespace(
        id="pipeline-1",
        status="running",
        test_run_id="run-1",
        workflow_type="offline",
        execution_metadata={},
    )
    session = _ResumeSession(pipeline, "project-1", [])
    monkeypatch.setattr("app.agents.workflow.AsyncSessionLocal", lambda: session)

    assert await _claim_pipeline_resume("pipeline-1") is None
    assert session.commit_count == 0
@pytest.mark.asyncio
async def test_partial_replay_metadata_is_not_restored(monkeypatch):
    previous = SimpleNamespace(id="old-pipeline")
    stages = [
        SimpleNamespace(
            pipeline_run_id="old-pipeline",
            stage_name="summary",
            checkpoint_data={"structured_summary": {"source": "legacy"}},
            result_data={
                "_replay": {
                    "output_checksum_sha256": _canonical_checksum(
                        {"structured_summary": {"source": "legacy"}}
                    ),
                    "runtime_versions": {"prompt_registry": "abc123"},
                }
            },
        )
    ]
    monkeypatch.setattr(
        "app.agents.workflow.AsyncSessionLocal",
        lambda: _Session(previous, stages),
    )

    assert await _load_checkpoint("run-1", "deep") is None


def test_resume_task_is_registered_without_automatic_retries():
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath("app", "worker", "tasks.py").read_text(encoding="utf-8")
    task_start = source.index('name="app.worker.tasks.resume_agent_pipeline"')
    task_source = source[task_start:task_start + 500]
    assert "max_retries=0" in task_source
    assert "def resume_agent_pipeline" in task_source

@pytest.mark.asyncio
async def test_resume_pipeline_dispatches_by_authoritative_workflow(monkeypatch):
    pipeline = SimpleNamespace(id="pipeline-1", workflow_type="deep")
    session = _ResumeSession(pipeline, "project-1", [])
    monkeypatch.setattr("app.agents.workflow.AsyncSessionLocal", lambda: session)
    observed = {}

    async def _resume_deep(**kwargs):
        observed.update(kwargs)
        return {"resumed": True}

    monkeypatch.setattr("app.agents.workflow.run_deep_pipeline", _resume_deep)
    from app.agents.workflow import resume_pipeline

    result = await resume_pipeline("pipeline-1", "build-42")

    assert result == {"resumed": True}
    assert observed == {"build_number": "build-42", "pipeline_run_id": "pipeline-1", "expected_attempt": None}
@pytest.mark.asyncio
async def test_checkpoint_without_replay_metadata_is_not_restored(monkeypatch):
    previous = SimpleNamespace(id="old-pipeline")
    stages = [
        SimpleNamespace(
            pipeline_run_id="old-pipeline",
            stage_name="summary",
            checkpoint_data={"structured_summary": {"source": "legacy"}},
            result_data={},
        )
    ]
    monkeypatch.setattr(
        "app.agents.workflow.AsyncSessionLocal",
        lambda: _Session(previous, stages),
    )

    assert await _load_checkpoint("run-1", "deep") is None


@pytest.mark.asyncio
async def test_checkpoint_with_mismatched_output_checksum_is_not_restored(monkeypatch):
    previous = SimpleNamespace(id="old-pipeline")
    stages = [
        _stage(
            "summary",
            {"structured_summary": {"source": "tampered"}},
            checksum="f" * 64,
        )
    ]
    monkeypatch.setattr(
        "app.agents.workflow.AsyncSessionLocal",
        lambda: _Session(previous, stages),
    )

    assert await _load_checkpoint("run-1", "deep") is None
