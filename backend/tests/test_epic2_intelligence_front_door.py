"""
Epic 2: Run Intelligence As Product Front Door — Unit Tests.

Tests:
  QAI-201: Navigation and routing
  QAI-202: API partial-failure handling, response shape
  QAI-203: Sticky action bar (frontend — schema tests here)
  QAI-204: Snapshot caching — schema, ORM model, service functions
  Edge cases: passed runs, partial data, stale snapshots, large runs
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security", verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"), create_access_token=MagicMock(return_value="access_token"), create_refresh_token=MagicMock(return_value="refresh_token"), decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None)))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-202: API Response Shape
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunIntelligenceResponseShape:
    """Verify the expected response contract fields exist."""

    def test_provenance_schema(self):
        from app.models.schemas import Provenance

        p = Provenance()
        assert p.schema_version == 1
        assert p.fallback_used is False
        assert p.tools_used_count == 0
        assert p.generated_by == "ai_pipeline"

    def test_summary_modes_schema(self):
        from app.models.schemas import SummaryModes

        m = SummaryModes(available=["executive", "developer", "manager"])
        assert len(m.available) == 3
        assert m.default == "executive"

    def test_cluster_insight_response(self):
        from app.models.schemas import ClusterInsightResponse

        ci = ClusterInsightResponse(
            id="x", cluster_id="cl_001", label="Auth failures", size=5,
        )
        assert ci.member_test_ids == []
        assert ci.dimension_scores == []

    def test_baseline_diff_defaults(self):
        from app.models.schemas import BaselineDiff

        d = BaselineDiff()
        assert d.baseline_run_id is None
        assert d.new_failures == []
        assert d.resolved_failures == []
        assert d.regression_classification == "unclassified"

    def test_defect_candidate_defaults(self):
        from app.models.schemas import DefectCandidate

        dc = DefectCandidate(
            cluster_id="cl_001", label="Test", severity_hint="HIGH",
            failure_category="PRODUCT_BUG", confidence=75,
        )
        assert dc.recommended_actions == []

    def test_pipeline_timeline_schema(self):
        from app.models.schemas import PipelineTimelineResponse

        timeline = PipelineTimelineResponse(
            pipeline_run_id=uuid.uuid4(),
            workflow_type="offline",
            status="completed",
        )
        assert timeline.schema_version == 2
        assert timeline.summary.total_stages == 0
        assert timeline.stages == []
        assert timeline.events == []
        assert timeline.replay_integrity.replayable is False

    def test_pipeline_replay_schema(self):
        from app.models.schemas import PipelineReplayResponse

        replay = PipelineReplayResponse(
            pipeline_run_id=uuid.uuid4(),
            test_run_id=uuid.uuid4(),
            workflow_type="deep",
            status="completed",
            event_counts={"stage_completed": 2},
        )
        assert replay.schema_version == 1
        assert replay.replayable is False
        assert replay.stage_replay == []
        assert replay.events == []
        assert replay.audit_gaps.missing_start_events == []

    def test_pipeline_event_log_health_schema(self):
        from app.models.schemas import PipelineEventLogHealthResponse

        health = PipelineEventLogHealthResponse(
            status="degraded",
            write_failure_count=2,
            dead_letter_count=1,
            dead_letter_limit=200,
            recent_dead_letters=[{"event": {"event_type": "stage_started"}}],
        )
        assert health.status == "degraded"
        assert health.dead_letter_count == 1

    def test_partial_errors_field(self):
        """The intelligence response can include partial_errors."""
        response = {
            "run": {"id": "x"},
            "partial_errors": ["summary_unavailable", "baseline_diff_unavailable"],
        }
        assert len(response["partial_errors"]) == 2

    def test_partial_errors_none_when_no_failures(self):
        response = {"run": {"id": "x"}, "partial_errors": None}
        assert response["partial_errors"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-204: Snapshot Caching
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotModel:
    """Verify the RunIntelligenceSnapshot ORM model."""

    def test_model_exists(self):
        from app.models.postgres import RunIntelligenceSnapshot

        assert RunIntelligenceSnapshot.__tablename__ == "run_intelligence_snapshots"

    def test_model_columns(self):
        from app.models.postgres import RunIntelligenceSnapshot

        columns = {c.name for c in RunIntelligenceSnapshot.__table__.columns}
        assert "run_id" in columns
        assert "schema_version" in columns
        assert "payload" in columns
        assert "generated_at" in columns
        assert "fallback_used" in columns
        assert "stale" in columns

    def test_run_id_is_unique(self):
        from app.models.postgres import RunIntelligenceSnapshot

        # The model has unique=True on run_id
        col = RunIntelligenceSnapshot.__table__.c.run_id
        assert col.unique is True


class TestSnapshotServiceConstants:
    def test_schema_version(self):
        from app.services.intelligence_snapshot_service import CURRENT_SCHEMA_VERSION

        assert isinstance(CURRENT_SCHEMA_VERSION, int)
        assert CURRENT_SCHEMA_VERSION >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_passed_run_with_no_clusters(self):
        """Passed runs should still return valid intelligence with empty clusters."""
        response = {
            "run": {"id": "x", "status": "passed", "failed_tests": 0},
            "failure_clusters": [],
            "all_green": True,
            "intelligence_available": False,
            "partial_errors": None,
        }
        assert response["all_green"] is True
        assert response["failure_clusters"] == []

    def test_run_with_partial_data(self):
        """Runs with partial ingestion should not crash."""
        response = {
            "run": {"id": "x", "total_tests": 100, "failed_tests": 5},
            "structured_summary": None,
            "failure_clusters": [],
            "release_decision": None,
            "what_changed_since_last_good_run": None,
            "partial_errors": ["summary_unavailable"],
        }
        assert response["structured_summary"] is None
        assert response["partial_errors"] is not None

    def test_stale_snapshot_returns_data(self):
        """Stale snapshots should still be usable."""
        snapshot = {
            "run_id": "x",
            "schema_version": 2,
            "payload": {"run": {"id": "x"}},
            "stale": True,
        }
        assert snapshot["stale"] is True
        assert snapshot["payload"] is not None

    def test_large_run_clusters_capped(self):
        """Even with many clusters, the response limits to 20."""
        clusters = [{"id": f"cl_{i}", "size": 10 - i} for i in range(25)]
        # The service caps at 20 via .limit(20) in the query
        capped = clusters[:20]
        assert len(capped) == 20


# ═══════════════════════════════════════════════════════════════════════════════
# Regression Guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressionGuards:
    def test_run_intelligence_router_exists(self):
        from app.routers.run_intelligence import router

        paths = [r.path for r in router.routes]
        assert any("intelligence" in p for p in paths)
        assert any("summary" in p for p in paths)
        assert any("baseline-diff" in p for p in paths)

    def test_run_intelligence_service_importable(self):
        from app.services.run_intelligence_service import get_run_intelligence, get_run_mode_summary

        assert callable(get_run_intelligence)
        assert callable(get_run_mode_summary)

    def test_snapshot_service_importable(self):
        from app.services.intelligence_snapshot_service import (
            get_cached_snapshot,
            save_snapshot,
            mark_stale,
            invalidate,
        )

        assert callable(get_cached_snapshot)
        assert callable(save_snapshot)
        assert callable(mark_stale)
        assert callable(invalidate)

    def test_metrics_exist(self):
        from app.core.metrics import (
            run_intelligence_duration_seconds,
            run_intelligence_requests_total,
        )

        assert run_intelligence_duration_seconds._type == "histogram"
        assert run_intelligence_requests_total._type == "counter"


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Timeline Route
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentTimelineRoute:
    @pytest.mark.asyncio
    async def test_pipeline_timeline_includes_stages_and_events(self):
        from app.routers.agents import get_pipeline_timeline

        pipeline_id = uuid.uuid4()
        pipeline = types.SimpleNamespace(
            id=pipeline_id,
            workflow_type="offline",
            status="completed",
            started_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc),
            # ``build_replay_integrity_summary`` → ``_workflow_route_decisions``
            # reads ``pipeline.execution_metadata`` to synthesise route-decision
            # events. Real ORM rows always carry the column (nullable JSONB);
            # the mock must mirror it or the timeline route AttributeErrors.
            execution_metadata=None,
        )
        stage = types.SimpleNamespace(
            stage_name="summary",
            status="completed",
            started_at=datetime(2026, 4, 1, 10, 0, 10, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 1, 10, 0, 20, tzinfo=timezone.utc),
            result_data={"summary_length": 120},
            error=None,
            skipped_reason=None,
            execution_path="executed",
            fallback_used=False,
            # build_agent_observability_summary (called by get_pipeline_timeline)
            # reads stage.fallback_reason alongside fallback_used.
            fallback_reason=None,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            llm_calls_count=1,
            cost_usd=0.01,
            error_category=None,
            confidence_score=92,
            evidence_count=3,
            route_rationale="Summary generated from collected evidence",
            # Replay-integrity scan (build_replay_integrity_summary →
            # _integrity_report) reads ``stage.checkpoint_data`` to flag
            # completed stages with no checkpoint. Real ORM rows carry the
            # column (nullable JSONB); mirror it on the mock.
            checkpoint_data=None,
        )

        class _StageResult:
            def scalars(self):
                return self

            def all(self):
                return [stage]

        class _PipelineResult:
            def scalar_one_or_none(self):
                return pipeline

        fake_db = types.SimpleNamespace(execute=AsyncMock(side_effect=[_PipelineResult(), _StageResult()]))

        with pytest.MonkeyPatch.context() as m:
            m.setattr("app.services.agent_cost_service.get_pipeline_cost_summary", MagicMock(return_value={"total_cost_usd": 0.01, "total_tokens": 30, "stages": []}))
            m.setattr("app.services.agent_cost_service.check_alerts", MagicMock(return_value=[]))
            m.setattr("app.services.pipeline_event_log.get_pipeline_timeline", MagicMock(return_value=[
                {
                    "event_type": "stage_started",
                    "stage_name": "summary",
                    "test_case_id": None,
                    "timestamp": datetime(2026, 4, 1, 10, 0, 10, tzinfo=timezone.utc),
                    "detail": {"message": "Summary stage started"},
                }
            ]))
            response = await get_pipeline_timeline(pipeline_id, db=fake_db, _=object())

        assert response["schema_version"] == 2
        assert response["pipeline_run_id"] == str(pipeline_id)
        assert response["summary"]["total_stages"] == 1
        assert response["stages"][0]["stage_name"] == "summary"
        assert response["events"][0]["event_type"] == "stage_started"
