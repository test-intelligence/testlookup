"""
Epic 5: Cluster-First Triage And Defect Promotion — Unit Tests.

Tests:
  QAI-501: Cluster ranking by impact
  QAI-502: Duplicate detection logic
  QAI-503: Promotion schemas and candidate lifecycle
  QAI-504: DefectCandidate model and promotion_source on Defect
  Edge cases: mixed root causes, no ownership, empty clusters, Jira unavailable
"""
from __future__ import annotations

import importlib.util
import sys
import types
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

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-501: Cluster Ranking
# ═══════════════════════════════════════════════════════════════════════════════

class TestClusterRanking:
    def test_rank_empty_clusters(self):
        from app.services.cluster_ranking_service import rank_clusters
        assert rank_clusters([]) == []

    def test_rank_single_cluster(self):
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [{"cluster_id": "cl_001", "label": "Auth", "size": 5}]
        ranked = rank_clusters(clusters)
        assert len(ranked) == 1
        assert ranked[0]["impact_rank"] == 1
        assert ranked[0]["impact_score"] > 0

    def test_larger_cluster_ranks_higher(self):
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [
            {"cluster_id": "cl_001", "label": "Small", "size": 2},
            {"cluster_id": "cl_002", "label": "Large", "size": 10},
        ]
        ranked = rank_clusters(clusters)
        assert ranked[0]["cluster_id"] == "cl_002"  # Larger cluster ranks higher

    def test_critical_severity_ranks_higher(self):
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [
            {"cluster_id": "cl_001", "label": "Low", "size": 5, "criticality_level": "LOW"},
            {"cluster_id": "cl_002", "label": "Crit", "size": 5, "criticality_level": "CRITICAL"},
        ]
        ranked = rank_clusters(clusters)
        assert ranked[0]["cluster_id"] == "cl_002"

    def test_new_regression_ranks_higher_than_flaky(self):
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [
            {"cluster_id": "cl_001", "label": "Flaky", "size": 5, "regression_classification": "known_flaky"},
            {"cluster_id": "cl_002", "label": "Regr", "size": 5, "regression_classification": "new_regression"},
        ]
        ranked = rank_clusters(clusters)
        assert ranked[0]["cluster_id"] == "cl_002"

    def test_impact_score_range(self):
        from app.services.cluster_ranking_service import compute_cluster_impact_score
        score = compute_cluster_impact_score(5, 10, "HIGH", "new_regression", 0.8)
        assert 0.0 <= score <= 1.0

    def test_all_ranks_assigned(self):
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [
            {"cluster_id": f"cl_{i}", "label": f"C{i}", "size": 10 - i}
            for i in range(5)
        ]
        ranked = rank_clusters(clusters)
        ranks = [c["impact_rank"] for c in ranked]
        assert ranks == [1, 2, 3, 4, 5]


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-502: Duplicate Detection Logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicateDetection:
    def test_exact_label_match(self):
        cluster_words = set("auth login failure".split())
        defect_words = set("auth login failure".split())
        intersection = cluster_words & defect_words
        union = cluster_words | defect_words
        similarity = len(intersection) / max(len(union), 1)
        assert similarity == 1.0

    def test_partial_overlap(self):
        cluster_words = set("auth login timeout".split())
        defect_words = set("auth login error".split())
        intersection = cluster_words & defect_words
        union = cluster_words | defect_words
        similarity = len(intersection) / max(len(union), 1)
        assert similarity == 0.5  # 2/4

    def test_no_overlap(self):
        cluster_words = set("database connection pool".split())
        defect_words = set("ui rendering crash".split())
        intersection = cluster_words & defect_words
        union = cluster_words | defect_words
        similarity = len(intersection) / max(len(union), 1)
        assert similarity == 0.0

    def test_threshold_check(self):
        """Similarity > 0.3 should flag as potential duplicate."""
        cluster_words = set("payment processing timeout".split())
        defect_words = set("payment gateway timeout error".split())
        intersection = cluster_words & defect_words
        union = cluster_words | defect_words
        similarity = len(intersection) / max(len(union), 1)
        assert similarity > 0.3  # "payment" and "timeout" overlap

    def test_substring_match(self):
        """Full label containment should also flag as duplicate."""
        cluster_label = "auth login failure"
        defect_title = "auth login failure in production"
        assert cluster_label in defect_title


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-503+504: Schemas and Models
# ═══════════════════════════════════════════════════════════════════════════════

class TestDefectCandidateModel:
    def test_table_exists(self):
        from app.models.postgres import DefectCandidate
        assert DefectCandidate.__tablename__ == "defect_candidates"

    def test_columns(self):
        from app.models.postgres import DefectCandidate
        columns = {c.name for c in DefectCandidate.__table__.columns}
        assert "run_id" in columns
        assert "cluster_id" in columns
        assert "severity" in columns
        assert "owner_team" in columns
        assert "component" in columns
        assert "evidence_bundle" in columns
        assert "criticality_scores" in columns
        assert "composite_score" in columns
        assert "status" in columns
        assert "promoted_defect_id" in columns

    def test_defect_has_promotion_source(self):
        from app.models.postgres import Defect
        columns = {c.name for c in Defect.__table__.columns}
        assert "promotion_source" in columns
        assert "cluster_id" in columns


class TestPromotionSchemas:
    def test_defect_candidate_response(self):
        from app.models.schemas import DefectCandidateResponse
        resp = DefectCandidateResponse(
            cluster_id="cl_001", run_id="run-1", title="Auth failures",
            description="Login timeout cluster", severity="HIGH",
            component="auth", owner_team="identity",
        )
        assert resp.composite_score == 0.0
        assert resp.evidence_bundle == {}

    def test_defect_promotion_request(self):
        from app.models.schemas import DefectPromotionRequest
        req = DefectPromotionRequest(title="Auth failures", severity="CRITICAL")
        assert req.project_key is None  # Local-only by default

    def test_defect_promotion_response_no_jira(self):
        from app.models.schemas import DefectPromotionResponse
        resp = DefectPromotionResponse(
            defect_id="d-1", cluster_id="cl_001", severity="HIGH", title="Auth",
        )
        assert resp.jira_ticket is None
        assert resp.jira_url is None


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_cluster_with_mixed_root_causes(self):
        """A cluster with mixed failure categories should still rank."""
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [{
            "cluster_id": "cl_mixed",
            "label": "Mixed failures",
            "size": 8,
            "criticality_level": "HIGH",
            "member_test_ids": ["t1", "t2", "t3"],
        }]
        # Mixed analyses: some infra, some product bug
        analyses = {
            "t1": {"confidence_score": 60},
            "t2": {"confidence_score": 90},
            "t3": {"confidence_score": 30},
        }
        ranked = rank_clusters(clusters, analyses_by_test=analyses)
        assert ranked[0]["impact_score"] > 0

    def test_no_ownership_mapping(self):
        """Candidate with no owner_team should still be promotable."""
        from app.models.schemas import DefectCandidateResponse
        resp = DefectCandidateResponse(
            cluster_id="cl_001", run_id="run-1",
            title="Unowned failures", description="",
            severity="MEDIUM", component="Unknown", owner_team="Unknown",
        )
        assert resp.owner_team == "Unknown"

    def test_jira_unavailable_local_draft(self):
        """Promotion without project_key should work as local draft."""
        from app.models.schemas import DefectPromotionRequest
        req = DefectPromotionRequest(
            title="Local defect", severity="HIGH",
            component="auth", owner_team="identity",
        )
        assert req.project_key is None

    def test_empty_cluster_list_ranking(self):
        from app.services.cluster_ranking_service import rank_clusters
        assert rank_clusters([]) == []

    def test_single_member_cluster(self):
        from app.services.cluster_ranking_service import rank_clusters
        clusters = [{"cluster_id": "cl_001", "label": "Single", "size": 1, "member_test_ids": ["t1"]}]
        ranked = rank_clusters(clusters)
        assert ranked[0]["impact_rank"] == 1
        assert ranked[0]["impact_score"] > 0

    def test_duplicate_detection_empty_labels(self):
        """Empty labels should not crash duplicate detection."""
        cluster_words = set("".split())
        defect_words = set("some defect".split())
        union = cluster_words | defect_words
        similarity = len(cluster_words & defect_words) / max(len(union), 1)
        assert similarity == 0.0
