"""
Unit tests for Release Council Service (Epic 9).

Tests deterministic context assembly, override audit trail, and edge cases.
All DB calls are mocked.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

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
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock())))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ── Schema / model tests ──────────────────────────────────────────────────────

class TestOverrideAuditEntry:
    def test_valid_entry(self):
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-01T12:00:00Z",
            actor_id="abc-123",
            actor_name="admin_user",
            before_recommendation="NO_GO",
            before_risk_score=72,
            after_recommendation="CONDITIONAL_GO",
            reason="Hotfix deployed, blocking issues resolved",
        )
        assert entry.before_recommendation == "NO_GO"
        assert entry.after_recommendation == "CONDITIONAL_GO"
        assert entry.before_risk_score == 72

    def test_optional_fields(self):
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-01T12:00:00Z",
            before_recommendation="GO",
            before_risk_score=10,
            after_recommendation="NO_GO",
            reason="Security vulnerability found",
        )
        assert entry.actor_id is None
        assert entry.actor_name is None


class TestReleaseCouncilResponse:
    def test_default_values(self):
        from app.models.schemas import ReleaseCouncilResponse

        resp = ReleaseCouncilResponse(
            run_id="test-run-123",
            recommendation="GO",
            risk_score=15,
        )
        assert resp.blocking_issues == []
        assert resp.conditions_for_go == []
        assert resp.cluster_insights == []
        assert resp.open_defects_by_component == []
        assert resp.override_audit == []
        assert resp.human_override is None
        assert resp.original_recommendation is None

    def test_full_response(self):
        from app.models.schemas import (
            DimensionScore,
            OverrideAuditEntry,
            ReleaseCouncilResponse,
        )

        resp = ReleaseCouncilResponse(
            run_id="test-run-456",
            recommendation="CONDITIONAL_GO",
            risk_score=42,
            composite_risk=42.5,
            dimension_scores=[
                DimensionScore(name="user_impact", label="User Impact", score=60.0, weight=0.25, contribution=15.0),
            ],
            blocking_issues=["High user impact"],
            conditions_for_go=["Resolve product bugs"],
            reasoning="Elevated risk from product bugs.",
            score_model_version=1,
            open_defects_by_component=[{"component": "auth", "count": 3}],
            human_override="Approved after review",
            original_recommendation="NO_GO",
            original_risk_score=72,
            override_audit=[
                OverrideAuditEntry(
                    timestamp="2026-04-01T12:00:00Z",
                    before_recommendation="NO_GO",
                    before_risk_score=72,
                    after_recommendation="CONDITIONAL_GO",
                    reason="Hotfix deployed",
                ),
            ],
            pass_rate=85.5,
            build_number="build-123",
        )
        assert resp.dimension_scores[0].name == "user_impact"
        assert len(resp.override_audit) == 1
        assert resp.original_recommendation == "NO_GO"


class TestReleaseCouncilOverrideRequest:
    def test_valid_override(self):
        from app.models.schemas import ReleaseCouncilOverrideRequest

        req = ReleaseCouncilOverrideRequest(
            override_recommendation="GO",
            reason="All blocking issues resolved",
        )
        assert req.override_recommendation == "GO"
        assert req.reason == "All blocking issues resolved"

    def test_empty_reason_allowed_by_schema(self):
        from app.models.schemas import ReleaseCouncilOverrideRequest

        req = ReleaseCouncilOverrideRequest(
            override_recommendation="NO_GO",
            reason="",
        )
        assert req.reason == ""


class TestInputSnapshotAssembly:
    @pytest.mark.asyncio
    async def test_snapshot_from_state(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        state = {
            "pipeline_run_id": "pr-1",
            "project_id": "proj-1",
            "test_run_id": "run-1",
            "pass_rate": 85.0,
            "total_tests": 100,
            "is_regression": True,
            "regression_tests": ["t1", "t2"],
            "analyses": {"tc1": {}, "tc2": {}, "tc3": {}},
            "anomalies": [{"severity": "HIGH"}],
            "failure_clusters": [
                {"label": "Auth failures", "size": 5, "regression_classification": "new_regression"},
            ],
            "flaky_findings": [{"test_name": "flaky_test"}],
            "test_health_findings": [],
        }

        snapshot = await ReleaseRiskAgent._assemble_input_snapshot(state)

        assert snapshot["pass_rate"] == 85.0
        assert snapshot["total_tests"] == 100
        assert snapshot["is_regression"] is True
        assert snapshot["regression_test_count"] == 2
        assert snapshot["analysis_count"] == 3
        assert snapshot["anomaly_count"] == 1
        assert snapshot["cluster_count"] == 1
        assert snapshot["flaky_finding_count"] == 1
        assert snapshot["test_health_finding_count"] == 0
        assert "assembled_at" in snapshot
        assert snapshot["score_model_version"] == 1

    @pytest.mark.asyncio
    async def test_snapshot_empty_state(self):
        from app.agents.release_risk_agent import ReleaseRiskAgent

        state = {
            "pipeline_run_id": "pr-1",
            "project_id": "proj-1",
            "test_run_id": "run-1",
        }

        snapshot = await ReleaseRiskAgent._assemble_input_snapshot(state)

        assert snapshot["pass_rate"] == 0.0
        assert snapshot["cluster_count"] == 0
        assert snapshot["analysis_count"] == 0


class TestDeterministicScoring:
    def test_go_threshold(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(15.0, 95.0, 85.0) == "GO"

    def test_conditional_go_threshold(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(40.0, 90.0, 85.0) == "CONDITIONAL_GO"

    def test_no_go_threshold(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(60.0, 80.0, 85.0) == "NO_GO"

    def test_pass_rate_hard_floor(self):
        from app.services.criticality_service import score_to_recommendation

        assert score_to_recommendation(10.0, 50.0, 85.0) == "NO_GO"

    def test_compute_composite_clamped(self):
        from app.services.criticality_service import compute_composite

        scores = {k: 100.0 for k in [
            "user_impact", "env_sensitivity", "reproducibility",
            "regression_likely", "hist_recurrence", "blast_radius",
            "diagnosis_conf",
        ]}
        composite = compute_composite(scores)
        assert 0 <= composite <= 100

    def test_compute_composite_zero(self):
        from app.services.criticality_service import compute_composite

        scores = {k: 0.0 for k in [
            "user_impact", "env_sensitivity", "reproducibility",
            "regression_likely", "hist_recurrence", "blast_radius",
            "diagnosis_conf",
        ]}
        assert compute_composite(scores) == 0.0

    def test_dimension_scores_compute(self):
        from app.services.criticality_service import compute_dimension_scores

        scores = compute_dimension_scores(
            analyses={"tc1": {"failure_category": "PRODUCT_BUG", "confidence_score": 80}},
            anomalies=[],
            pass_rate=80.0,
            is_regression=False,
            regression_tests=[],
            failure_clusters=[],
            open_defects=0,
        )
        assert "user_impact" in scores
        assert "blast_radius" in scores
        assert all(0 <= v <= 100 for v in scores.values())

    def test_score_cluster_proportional(self):
        from app.services.criticality_service import score_cluster

        dim_scores = {"user_impact": 80.0, "blast_radius": 60.0}
        cluster_scores = score_cluster(
            cluster={},
            all_dim_scores=dim_scores,
            total_analyses=10,
            member_count=5,
        )
        # 50% of the run
        assert cluster_scores["user_impact"] == 40.0
        assert cluster_scores["blast_radius"] == 30.0

    def test_score_cluster_empty(self):
        from app.services.criticality_service import score_cluster

        result = score_cluster({}, {}, 0, 0)
        assert result == {}
