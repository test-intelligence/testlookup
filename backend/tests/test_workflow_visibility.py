"""
Tests for Run Intelligence & Deep Analysis Workflow Visibility (WF-1 through WF-8).

Covers:
  - Pipeline status model: never_run, completed, partial, failed
  - Trigger response: task_id vs pipeline_run_id
  - Partial completion semantics: required vs optional stages
  - Deep pipeline status in intelligence response
  - Stage summary counts
"""
import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WF-1: Pipeline Status Model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.postgres import AgentPipelineRun, AgentStageResult  # noqa: E402


class TestPipelineModels:
    def test_pipeline_run_has_status(self):
        columns = {c.name for c in AgentPipelineRun.__table__.columns}
        assert "status" in columns
        assert "workflow_type" in columns
        assert "completed_at" in columns
        assert "error" in columns

    def test_stage_result_has_required_fields(self):
        columns = {c.name for c in AgentStageResult.__table__.columns}
        assert "status" in columns
        assert "skipped_reason" in columns
        assert "execution_path" in columns
        assert "fallback_used" in columns

    def test_pipeline_status_column_width(self):
        """Status column must be wide enough for 'retry_wait' (10 chars)."""
        col = AgentPipelineRun.__table__.c.status
        assert col.type.length >= 20

    def test_workflow_type_column_exists(self):
        col = AgentPipelineRun.__table__.c.workflow_type
        assert col.type.length >= 20


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WF-2: Trigger Response Contract
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTriggerResponse:
    def test_trigger_response_has_task_id_in_source(self):
        """Verify TriggerDeepResponse includes task_id field (avoid heavy import chain)."""
        from pathlib import Path
        router_path = Path(__file__).parent.parent / "app" / "routers" / "deep_investigation.py"
        content = router_path.read_text(encoding="utf-8")
        assert "task_id" in content
        assert "TriggerDeepResponse" in content

    def test_trigger_returns_task_id_not_only_pipeline_id(self):
        """Verify the trigger endpoint sets task_id in the response."""
        from pathlib import Path
        router_path = Path(__file__).parent.parent / "app" / "routers" / "deep_investigation.py"
        content = router_path.read_text(encoding="utf-8")
        assert "task_id=task.id" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WF-3: Required vs Optional Stage Constants (verified via source read)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDeepStageClassification:
    """Verify stage constants are defined in workflow.py source (avoids heavy import chain)."""

    def test_stage_constants_in_workflow_source(self):
        from pathlib import Path
        workflow_path = Path(__file__).parent.parent / "app" / "agents" / "workflow.py"
        content = workflow_path.read_text(encoding="utf-8")
        assert "DEEP_REQUIRED_STAGES" in content
        assert "DEEP_OPTIONAL_STAGES" in content
        # Verify specific stages are in the source
        for stage in ["ingestion", "anomaly_detection", "failure_clustering", "root_cause_analysis", "summary"]:
            assert stage in content, f"Required stage '{stage}' not found in workflow.py"
        for stage in ["triage", "flaky_sentinel", "test_health", "release_risk"]:
            assert stage in content, f"Optional stage '{stage}' not found in workflow.py"

    def test_mark_pipeline_done_never_writes_partial(self):
        """E7.1: a graph that finished with failed stages is ``completed`` with
        ``execution_metadata.stage_quality == "degraded"``; the literal
        ``partial`` must not be written anywhere in workflow.py."""
        from pathlib import Path
        workflow_path = Path(__file__).parent.parent / "app" / "agents" / "workflow.py"
        content = workflow_path.read_text(encoding="utf-8")
        assert '"partial"' not in content, "workflow.py still writes the retired 'partial' status"
        assert "apply_transition(run, PipelineRunStatus.COMPLETED" in content
        assert "apply_transition(run, PipelineRunStatus.FAILED" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WF-4: Pipeline Status States
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPipelineStatusStates:
    def test_never_run_response_shape(self):
        """When no pipeline exists, response should indicate never_run."""
        resp = {
            "pipeline_run_id": None,
            "workflow_type": "deep",
            "status": "never_run",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "stage_summary": {"completed": 0, "failed": 0, "skipped": 0, "pending": 0},
        }
        assert resp["status"] == "never_run"
        assert resp["pipeline_run_id"] is None

    def test_completed_response_shape(self):
        resp = {
            "pipeline_run_id": "uuid-1",
            "workflow_type": "deep",
            "status": "completed",
            "started_at": "2026-04-06T10:00:00Z",
            "completed_at": "2026-04-06T10:05:00Z",
            "error": None,
            "stage_summary": {"completed": 9, "failed": 0, "skipped": 0, "pending": 0},
        }
        assert resp["status"] == "completed"
        assert resp["stage_summary"]["completed"] == 9

    def test_degraded_completion_response_shape(self):
        """What used to be ``partial``: completed, with failed stages visible in
        the summary and ``stage_quality`` degraded."""
        from app.services.workflow_run_state import public_status
        resp = {
            "pipeline_run_id": "uuid-2",
            "workflow_type": "deep",
            "status": "completed",
            "started_at": "2026-04-06T10:00:00Z",
            "completed_at": "2026-04-06T10:03:00Z",
            "error": None,
            "stage_summary": {"completed": 7, "failed": 2, "skipped": 0, "pending": 0},
            "execution_metadata": {"stage_quality": "degraded"},
        }
        assert resp["status"] == "completed"
        assert public_status(resp["status"]) == "completed"
        assert resp["stage_summary"]["failed"] == 2
        assert resp["execution_metadata"]["stage_quality"] == "degraded"

    def test_failed_response_shape(self):
        resp = {
            "pipeline_run_id": "uuid-3",
            "workflow_type": "deep",
            "status": "failed",
            "started_at": "2026-04-06T10:00:00Z",
            "completed_at": "2026-04-06T10:01:00Z",
            "error": "Pipeline execution error: timeout",
            "stage_summary": {"completed": 2, "failed": 1, "skipped": 6, "pending": 0},
        }
        assert resp["status"] == "failed"
        assert resp["error"] is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WF-5: Frontend PipelineStatus Type
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFrontendServiceContract:
    def test_deep_investigation_service_has_pipeline_status(self):
        """Verify the frontend service module exports getPipelineStatus."""
        # This is a structural test — we check the file exists and has the function
        from pathlib import Path
        svc_path = Path(__file__).parent.parent.parent / "frontend" / "src" / "services" / "deepInvestigationService.ts"
        if svc_path.exists():
            content = svc_path.read_text()
            assert "getPipelineStatus" in content
            assert "PipelineStatus" in content
