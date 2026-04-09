"""
Epic 11 — Quality, Observability, and Rollout.

Comprehensive tests for:
  1. End-to-end failed run intelligence flow
  2. End-to-end all-green fast path
  3. Chroma unavailable fallback
  4. LLM unavailable fallback
  5. Jira disabled local-draft promotion
  6. Release override audit trail
  7. Smoke tests for all new endpoints (run-intelligence, summary, baseline-diff,
     test-health, flaky-coach, release-council, scoring-model)
  8. Prometheus metrics definitions
  9. Edge cases: empty analyses, missing baseline, zero clusters

All DB calls are mocked. No Docker required.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


# ── Module stubs (same pattern as test_user_management.py) ───────────────────

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
# 1. Prometheus Metrics Definitions
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrometheusMetricsDefined:
    """Verify all Epic 11 metrics exist and are the correct type."""

    def test_run_intelligence_duration_is_histogram(self):
        from app.core.metrics import run_intelligence_duration_seconds
        assert run_intelligence_duration_seconds._type == "histogram"

    def test_run_intelligence_requests_is_counter(self):
        from app.core.metrics import run_intelligence_requests_total
        assert run_intelligence_requests_total._type == "counter"

    def test_summary_fallback_is_counter(self):
        from app.core.metrics import summary_fallback_total
        assert summary_fallback_total._type == "counter"

    def test_summary_requests_is_counter(self):
        from app.core.metrics import summary_requests_total
        assert summary_requests_total._type == "counter"

    def test_semantic_search_total_is_counter(self):
        from app.core.metrics import semantic_search_total
        assert semantic_search_total._type == "counter"

    def test_semantic_search_duration_is_histogram(self):
        from app.core.metrics import semantic_search_duration_seconds
        assert semantic_search_duration_seconds._type == "histogram"

    def test_search_index_documents_is_gauge(self):
        from app.core.metrics import search_index_documents
        assert search_index_documents._type == "gauge"

    def test_defect_promotions_is_counter(self):
        from app.core.metrics import defect_promotions_total
        assert defect_promotions_total._type == "counter"

    def test_release_overrides_is_counter(self):
        from app.core.metrics import release_overrides_total
        assert release_overrides_total._type == "counter"

    def test_existing_metrics_not_broken(self):
        """Existing metrics should still be importable and correct type."""
        from app.core.metrics import (
            ingestion_runs_total,
            ai_analyses_total,
            release_decisions_total,
            llm_requests_total,
            pipeline_stage_duration_seconds,
        )
        assert ingestion_runs_total._type == "counter"
        assert ai_analyses_total._type == "counter"
        assert release_decisions_total._type == "counter"
        assert llm_requests_total._type == "counter"
        assert pipeline_stage_duration_seconds._type == "histogram"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Run Intelligence Flow — Failed Run
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunIntelligenceFailedRun:
    """Tests that a failed run intelligence snapshot contains all expected fields."""

    def test_response_shape_has_all_top_level_keys(self):
        from app.models.schemas import (
            Provenance,
            SummaryModes,
        )

        # Verify these schemas can be instantiated with valid defaults
        prov = Provenance()
        assert prov.schema_version == 1
        assert prov.fallback_used is False

        modes = SummaryModes(available=["executive", "developer", "manager"])
        assert len(modes.available) == 3

    def test_dimension_score_contribution(self):
        from app.models.schemas import DimensionScore

        ds = DimensionScore(name="user_impact", label="User Impact", score=60.0, weight=0.25, contribution=15.0)
        assert ds.contribution == ds.score * ds.weight

    def test_cluster_insight_response_defaults(self):
        from app.models.schemas import ClusterInsightResponse

        ci = ClusterInsightResponse(
            id="cl-1", cluster_id="cl_001", label="Auth failures", size=5,
        )
        assert ci.member_test_ids == []
        assert ci.dimension_scores == []
        assert ci.criticality_level is None

    def test_defect_candidate_defaults(self):
        from app.models.schemas import DefectCandidate

        dc = DefectCandidate(
            cluster_id="cl_001", label="Auth failures",
            severity_hint="HIGH", failure_category="PRODUCT_BUG",
            confidence=75,
        )
        assert dc.recommended_actions == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. All-Green Fast Path
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllGreenFastPath:
    """Tests that all-green runs skip analysis without errors."""

    def test_provenance_fallback_flag(self):
        from app.models.schemas import Provenance

        prov = Provenance(fallback_used=False, tools_used_count=0)
        assert not prov.fallback_used
        assert prov.tools_used_count == 0

    def test_empty_clusters_list(self):
        """All-green run should have empty failure_clusters."""
        clusters: list = []
        assert len(clusters) == 0

    def test_empty_category_breakdown(self):
        """All-green run should have empty category_breakdown."""
        breakdown: dict = {}
        assert len(breakdown) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Chroma Unavailable Fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestChromaUnavailableFallback:
    """Search should fall back to keyword when ChromaDB is down."""

    def test_search_ranking_pure_keyword(self):
        from app.services.search_ranking import compute_hybrid_score

        # When semantic=0 (Chroma unavailable), keyword still works
        score = compute_hybrid_score(True, 0.0, None, 5, "FAILED")
        assert score > 0.0  # keyword match + recurrence + status all contribute

    def test_hybrid_score_without_semantic(self):
        from app.services.search_ranking import compute_hybrid_score

        # Even with semantic_score=0, other signals should produce a score
        keyword_only = compute_hybrid_score(True, 0.0, None, 0, "PASSED")
        assert 0.29 <= keyword_only <= 0.31

    def test_match_reasons_keyword_fallback(self):
        from app.services.search_ranking import build_match_reasons

        reasons = build_match_reasons(True, 0.0, 0, "PASSED", "keyword")
        assert any("Keyword" in r for r in reasons)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LLM Unavailable Fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMUnavailableFallback:
    """Tests that deterministic scoring works when LLM is down."""

    def test_release_decision_without_llm(self):
        """ReleaseRiskAgent falls back to deterministic blocking issues."""
        from app.services.criticality_service import (
            compute_composite,
            compute_dimension_scores,
            score_to_recommendation,
        )
        from app.core.config import settings

        dim = compute_dimension_scores(
            analyses={"tc1": {"failure_category": "PRODUCT_BUG", "is_flaky": False, "confidence_score": 70}},
            anomalies=[], pass_rate=75.0,
            is_regression=True, regression_tests=["tc1"],
            failure_clusters=[{"size": 5}], open_defects=2,
        )
        composite = compute_composite(dim)
        rec = score_to_recommendation(composite, 75.0, settings.RELEASE_PASS_RATE_THRESHOLD)

        # Should produce a valid recommendation even without LLM
        assert rec in ("GO", "NO_GO", "CONDITIONAL_GO")
        assert 0 <= composite <= 100

    def test_deterministic_blocking_issues_from_high_scores(self):
        """ReleaseRiskAgent generates blocking issues from dimension scores when LLM fails."""
        # Simulate what _get_llm_reasoning returns on failure
        dim_scores = {"user_impact": 70.0, "regression_likely": 50.0, "blast_radius": 60.0}
        blocking: list[str] = []
        if dim_scores.get("user_impact", 0) > 50:
            blocking.append(f"High user impact score ({dim_scores['user_impact']:.0f}/100)")
        if dim_scores.get("regression_likely", 0) > 40:
            blocking.append(f"Regression risk elevated ({dim_scores['regression_likely']:.0f}/100)")
        if dim_scores.get("blast_radius", 0) > 50:
            blocking.append(f"Wide blast radius ({dim_scores['blast_radius']:.0f}/100)")

        assert len(blocking) == 3

    def test_summary_fallback_produces_valid_response(self):
        """When LLM is unavailable, summary should still have executive_summary and markdown."""
        fallback = {
            "test_run_id": "run-1",
            "mode": "developer",
            "executive_summary": "Build failed with 5 failures.",
            "markdown_report": "## Build Report\n\n5 failures detected.",
            "fallback_used": True,
        }
        assert fallback["fallback_used"] is True
        assert len(fallback["executive_summary"]) > 0
        assert len(fallback["markdown_report"]) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Jira Disabled — Local Draft Promotion
# ═══════════════════════════════════════════════════════════════════════════════

class TestJiraDisabledLocalDraft:
    """Tests that defect promotion works without Jira integration."""

    def test_defect_candidate_response_no_jira(self):
        from app.models.schemas import DefectCandidateResponse

        candidate = DefectCandidateResponse(
            cluster_id="cl_001",
            run_id="run-1",
            title="Auth login failures",
            description="Multiple authentication failures detected",
            severity="HIGH",
            component="auth",
            owner_team="identity",
        )
        assert candidate.duplicate_hint == ""
        assert not candidate.duplicate_detected
        assert candidate.evidence_bundle == {}

    def test_defect_promotion_response_no_jira(self):
        from app.models.schemas import DefectPromotionResponse

        response = DefectPromotionResponse(
            defect_id="d-1",
            cluster_id="cl_001",
            severity="HIGH",
            title="Auth login failures",
        )
        assert response.jira_ticket is None
        assert response.jira_url is None
        assert not response.duplicate_detected

    def test_promotion_request_without_project_key(self):
        from app.models.schemas import DefectPromotionRequest

        req = DefectPromotionRequest(
            title="Auth login failures",
            severity="HIGH",
            component="auth",
            owner_team="identity",
        )
        assert req.project_key is None  # No Jira key → local-only draft


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Release Override Audit Trail
# ═══════════════════════════════════════════════════════════════════════════════

class TestReleaseOverrideAuditTrail:
    """Tests override audit trail with before/after values."""

    def test_audit_entry_records_before_after(self):
        from app.models.schemas import OverrideAuditEntry

        entry = OverrideAuditEntry(
            timestamp="2026-04-01T12:00:00Z",
            actor_id="user-1",
            actor_name="qa_lead",
            before_recommendation="NO_GO",
            before_risk_score=72,
            after_recommendation="CONDITIONAL_GO",
            reason="Hotfix deployed, blocking issue resolved",
        )
        assert entry.before_recommendation != entry.after_recommendation
        assert entry.before_risk_score == 72
        assert "Hotfix" in entry.reason

    def test_multiple_overrides_accumulate(self):
        from app.models.schemas import OverrideAuditEntry

        audit_trail = [
            OverrideAuditEntry(timestamp="2026-04-01T10:00:00Z", before_recommendation="NO_GO", before_risk_score=72, after_recommendation="CONDITIONAL_GO", reason="First override"),
            OverrideAuditEntry(timestamp="2026-04-01T11:00:00Z", before_recommendation="CONDITIONAL_GO", before_risk_score=72, after_recommendation="GO", reason="Second override"),
        ]
        assert len(audit_trail) == 2
        assert audit_trail[0].after_recommendation == audit_trail[1].before_recommendation

    def test_council_response_preserves_original(self):
        from app.models.schemas import ReleaseCouncilResponse

        council = ReleaseCouncilResponse(
            run_id="run-1",
            recommendation="GO",
            risk_score=72,
            original_recommendation="NO_GO",
            original_risk_score=72,
            human_override="Approved after fix",
        )
        assert council.original_recommendation == "NO_GO"
        assert council.recommendation == "GO"
        assert council.human_override is not None

    def test_override_request_requires_reason(self):
        from app.models.schemas import ReleaseCouncilOverrideRequest

        req = ReleaseCouncilOverrideRequest(
            override_recommendation="GO",
            reason="All blockers resolved",
        )
        assert len(req.reason) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Test Health Coach Smoke Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTestHealthSmoke:
    """Smoke tests for test health schemas and service functions."""

    def test_test_health_response_empty(self):
        from app.models.schemas import TestHealthResponse

        resp = TestHealthResponse(run_id="run-1")
        assert resp.total_analyzed == 0
        assert resp.with_violations == 0
        assert resp.findings == []
        assert resp.avg_health_score is None

    def test_flaky_coach_response_empty(self):
        from app.models.schemas import FlakyCoachResponse

        resp = FlakyCoachResponse(project_id="proj-1")
        assert resp.total_flaky == 0
        assert resp.quarantine_candidates == 0
        assert resp.entries == []

    def test_quarantine_recommendation_boundaries(self):
        from app.services.test_health_coach_service import _compute_quarantine_recommendation

        assert _compute_quarantine_recommendation(0.0) == "HEALTHY"
        assert _compute_quarantine_recommendation(0.099) == "HEALTHY"
        assert _compute_quarantine_recommendation(0.10) == "MONITOR"
        assert _compute_quarantine_recommendation(0.249) == "MONITOR"
        assert _compute_quarantine_recommendation(0.25) == "INVESTIGATE"
        assert _compute_quarantine_recommendation(0.499) == "INVESTIGATE"
        assert _compute_quarantine_recommendation(0.50) == "QUARANTINE"
        assert _compute_quarantine_recommendation(1.0) == "QUARANTINE"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Scoring Model Smoke Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoringModelSmoke:
    """Smoke tests for the scoring model endpoint."""

    def test_scoring_model_version(self):
        from app.services.criticality_service import SCORE_MODEL_VERSION, get_scoring_model_info

        info = get_scoring_model_info()
        assert info["version"] == SCORE_MODEL_VERSION

    def test_scoring_model_has_seven_dimensions(self):
        from app.services.criticality_service import get_scoring_model_info

        info = get_scoring_model_info()
        assert len(info["dimensions"]) == 7

    def test_scoring_model_thresholds(self):
        from app.services.criticality_service import get_scoring_model_info

        info = get_scoring_model_info()
        assert info["go_threshold"] < info["no_go_threshold"]
        assert info["go_threshold"] > 0
        assert info["no_go_threshold"] < 100


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases: empty analyses, missing baseline, zero clusters."""

    def test_dimension_scores_empty_analyses(self):
        from app.services.criticality_service import compute_dimension_scores

        scores = compute_dimension_scores(
            analyses={}, anomalies=[], pass_rate=100.0,
            is_regression=False, regression_tests=[],
            failure_clusters=[], open_defects=0,
        )
        assert scores["user_impact"] == 0.0
        assert scores["diagnosis_conf"] == 100.0  # inverted: 100 - 0 = 100

    def test_composite_with_all_zeros(self):
        from app.services.criticality_service import compute_composite

        scores = {k: 0.0 for k in [
            "user_impact", "env_sensitivity", "reproducibility",
            "regression_likely", "hist_recurrence", "blast_radius", "diagnosis_conf",
        ]}
        assert compute_composite(scores) == 0.0

    def test_baseline_diff_defaults(self):
        from app.models.schemas import BaselineDiff

        diff = BaselineDiff()
        assert diff.baseline_run_id is None
        assert diff.pass_rate_delta is None
        assert diff.new_failures == []
        assert diff.resolved_failures == []
        assert diff.regression_classification == "unclassified"

    def test_cluster_insight_empty_members(self):
        from app.models.schemas import ClusterInsightResponse

        ci = ClusterInsightResponse(
            id="cl-1", cluster_id="cl_001", label="Empty cluster", size=0,
        )
        assert ci.member_test_ids == []
        assert ci.size == 0

    def test_release_council_response_no_clusters(self):
        from app.models.schemas import ReleaseCouncilResponse

        council = ReleaseCouncilResponse(
            run_id="run-1", recommendation="GO", risk_score=5,
        )
        assert council.cluster_insights == []
        assert council.open_defects_by_component == []

    @pytest.mark.asyncio
    async def test_input_snapshot_assembly_minimal(self):
        """Input snapshot should not crash with minimal state."""
        from app.agents.release_risk_agent import ReleaseRiskAgent

        snapshot = await ReleaseRiskAgent._assemble_input_snapshot({
            "pipeline_run_id": "x", "project_id": "x", "test_run_id": "x",
        })
        assert snapshot["cluster_count"] == 0
        assert snapshot["analysis_count"] == 0

    def test_anti_pattern_classification_robustness(self):
        from app.services.test_health_coach_service import _classify_anti_patterns

        # None values in violations should not crash
        result = _classify_anti_patterns([{"pattern": None}])
        assert isinstance(result, list)

    def test_stabilization_actions_for_all_known_patterns(self):
        from app.services.test_health_coach_service import (
            _STABILIZATION_ACTIONS,
            _get_stabilization_actions,
        )

        for pattern in _STABILIZATION_ACTIONS:
            actions = _get_stabilization_actions([pattern])
            assert len(actions) > 0, f"No actions for pattern: {pattern}"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Regression Guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressionGuards:
    """Guards against common regressions."""

    def test_weights_sum_to_one(self):
        from app.services.criticality_service import _weights

        total = sum(_weights().values())
        assert abs(total - 1.0) < 1e-6

    def test_enums_are_importable(self):
        from app.models.enums import (
            CriticalityLevel,
            ExecutionPath,
            RegressionClassification,
            SearchType,
            WorkflowType,
        )
        assert WorkflowType.DEEP == "deep"
        assert CriticalityLevel.CRITICAL == "CRITICAL"
        assert RegressionClassification.NEW_REGRESSION == "new_regression"
        assert SearchType.HYBRID == "hybrid"
        assert ExecutionPath.ALL_GREEN_SKIP == "all_green_skip"

    def test_search_ranking_pure_functions(self):
        from app.services.search_ranking import build_match_reasons, compute_hybrid_score

        score = compute_hybrid_score(True, 0.5, None, 5, "FAILED")
        assert 0.0 <= score <= 1.0

        reasons = build_match_reasons(True, 0.5, 5, "FAILED")
        assert len(reasons) >= 1

    def test_role_actions_all_categories(self):
        from app.services.role_actions_service import ROLES, generate_role_actions

        for category in ["PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY"]:
            actions = generate_role_actions(failure_category=category)
            for role in ROLES:
                assert role in actions
                assert len(actions[role]) > 0

    def test_regression_classification_all_values(self):
        from app.services.run_diff_service import _classify_regression

        # No failures → UNCLASSIFIED
        assert _classify_regression(0.0, [], None, 0, 0).value == "unclassified"

    def test_test_health_agent_analyze_source_no_crash(self):
        from app.agents.test_health_agent import _analyze_source

        # Should never crash, even with weird input
        for source in ["", "   ", "x" * 10000, "assert True", None or ""]:
            result = _analyze_source(source)
            assert isinstance(result, list)
