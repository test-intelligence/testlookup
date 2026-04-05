"""
Epic 4: Trustworthy AI With Provenance And Evidence — Unit Tests.

Tests:
  QAI-401: Enriched provenance construction (confidence, sources, checks)
  QAI-402: Provenance schema validation
  QAI-403: Evidence/provenance ORM models
  QAI-404: Confidence computation and reason generation
  Edge cases: zero evidence, conflicting sources, fallback mode, high score low confidence
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


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-401: Enriched Provenance Construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildEnrichedProvenance:
    def test_minimal_provenance(self):
        from app.services.evidence_service import build_enriched_provenance

        prov = build_enriched_provenance(
            pipeline_meta=None, analyses=[], clusters=[],
            has_baseline=False, has_release_decision=False,
            fallback_used=False, generated_at=None,
        )
        assert prov["schema_version"] == 2
        assert prov["generated_by"] == "deterministic"
        assert prov["confidence"] is not None
        assert 0 <= prov["confidence"] <= 100

    def test_rich_provenance_with_evidence(self):
        from app.services.evidence_service import build_enriched_provenance

        analyses = [
            {
                "test_case_id": "tc-1",
                "evidence_references": [
                    {"source": "splunk", "excerpt": "Error in auth"},
                    {"source": "stacktrace", "excerpt": "NullPointerException"},
                ],
                "is_flaky": False,
            },
            {
                "test_case_id": "tc-2",
                "evidence_references": [{"source": "ocp", "excerpt": "Pod restart"}],
                "is_flaky": True,
            },
        ]
        clusters = [{"cluster_id": "cl_001", "representative_error": "Auth error"}]

        prov = build_enriched_provenance(
            pipeline_meta={"tools_used": ["fetch_stacktrace", "query_splunk"]},
            analyses=analyses,
            clusters=clusters,
            has_baseline=True,
            has_release_decision=True,
            fallback_used=False,
            generated_at="2026-04-01T12:00:00Z",
        )

        assert prov["evidence_count"] >= 3  # 3 evidence refs + 1 cluster
        assert "splunk" in prov["sources_used"]
        assert "stacktrace" in prov["sources_used"]
        assert "ocp" in prov["sources_used"]
        assert "flaky_detection" in prov["deterministic_checks_used"]
        assert "semantic_clustering" in prov["deterministic_checks_used"]
        assert "baseline_comparison" in prov["deterministic_checks_used"]
        assert "criticality_scoring" in prov["deterministic_checks_used"]
        assert prov["confidence"] > 50  # Rich evidence → good confidence

    def test_fallback_reduces_confidence(self):
        from app.services.evidence_service import build_enriched_provenance

        normal = build_enriched_provenance(
            pipeline_meta=None, analyses=[{"test_case_id": "x", "evidence_references": [{"source": "s"}]}],
            clusters=[], has_baseline=True, has_release_decision=False,
            fallback_used=False, generated_at=None,
        )
        fallback = build_enriched_provenance(
            pipeline_meta=None, analyses=[{"test_case_id": "x", "evidence_references": [{"source": "s"}]}],
            clusters=[], has_baseline=True, has_release_decision=False,
            fallback_used=True, generated_at=None,
        )
        assert fallback["confidence"] < normal["confidence"]
        assert "llm was unavailable" in fallback["confidence_reason"].lower()

    def test_baseline_increases_confidence(self):
        from app.services.evidence_service import build_enriched_provenance

        no_baseline = build_enriched_provenance(
            pipeline_meta=None, analyses=[], clusters=[],
            has_baseline=False, has_release_decision=False,
            fallback_used=False, generated_at=None,
        )
        with_baseline = build_enriched_provenance(
            pipeline_meta=None, analyses=[], clusters=[],
            has_baseline=True, has_release_decision=False,
            fallback_used=False, generated_at=None,
        )
        assert with_baseline["confidence"] > no_baseline["confidence"]


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-402: Provenance Schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvenanceSchema:
    def test_defaults(self):
        from app.models.schemas import Provenance

        p = Provenance()
        assert p.confidence is None
        assert p.confidence_reason is None
        assert p.evidence_count == 0
        assert p.sources_used == []
        assert p.deterministic_checks_used == []

    def test_full_provenance(self):
        from app.models.schemas import Provenance

        p = Provenance(
            schema_version=2,
            fallback_used=False,
            generated_by="ai_pipeline",
            tools_used_count=5,
            confidence=78,
            confidence_reason="High confidence: 8 evidence items from 3 source(s); baseline comparison available.",
            evidence_count=8,
            sources_used=["splunk", "stacktrace", "chromadb"],
            deterministic_checks_used=["flaky_detection", "regression_classification"],
        )
        assert p.confidence == 78
        assert len(p.sources_used) == 3
        assert len(p.deterministic_checks_used) == 2

    def test_backward_compatible(self):
        """Existing code creating Provenance without new fields should still work."""
        from app.models.schemas import Provenance

        p = Provenance(schema_version=1, fallback_used=True)
        assert p.confidence is None
        assert p.sources_used == []


class TestEvidenceArtifactResponseSchema:
    def test_schema(self):
        from app.models.schemas import EvidenceArtifactResponse

        e = EvidenceArtifactResponse(
            id="x", artifact_type="stack_trace", source_system="mongodb",
            summary_excerpt="NullPointerException at AuthService.login",
        )
        assert e.relevance_score is None
        assert e.cluster_id is None


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-403: ORM Models
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceArtifactModel:
    def test_table_exists(self):
        from app.models.postgres import EvidenceArtifact
        assert EvidenceArtifact.__tablename__ == "evidence_artifacts"

    def test_columns(self):
        from app.models.postgres import EvidenceArtifact
        columns = {c.name for c in EvidenceArtifact.__table__.columns}
        assert "run_id" in columns
        assert "cluster_id" in columns
        assert "artifact_type" in columns
        assert "source_system" in columns
        assert "summary_excerpt" in columns
        assert "relevance_score" in columns


class TestAIProvenanceRecordModel:
    def test_table_exists(self):
        from app.models.postgres import AIProvenanceRecord
        assert AIProvenanceRecord.__tablename__ == "ai_provenance_records"

    def test_columns(self):
        from app.models.postgres import AIProvenanceRecord
        columns = {c.name for c in AIProvenanceRecord.__table__.columns}
        assert "entity_type" in columns
        assert "entity_id" in columns
        assert "model_name" in columns
        assert "confidence" in columns
        assert "confidence_reason" in columns
        assert "evidence_count" in columns
        assert "sources_used" in columns
        assert "deterministic_checks_used" in columns


# ═══════════════════════════════════════════════════════════════════════════════
# QAI-404: Confidence Computation
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceComputation:
    def test_minimum_confidence(self):
        from app.services.evidence_service import _compute_confidence
        conf = _compute_confidence(0, 0, 0, False, True)
        assert conf >= 0

    def test_maximum_confidence(self):
        from app.services.evidence_service import _compute_confidence
        conf = _compute_confidence(20, 10, 10, True, False)
        assert conf <= 100

    def test_clamped_to_range(self):
        from app.services.evidence_service import _compute_confidence
        assert 0 <= _compute_confidence(0, 0, 0, False, False) <= 100
        assert 0 <= _compute_confidence(100, 100, 100, True, False) <= 100

    def test_more_evidence_higher_confidence(self):
        from app.services.evidence_service import _compute_confidence
        low = _compute_confidence(1, 1, 1, False, False)
        high = _compute_confidence(10, 4, 8, True, False)
        assert high > low


class TestConfidenceReason:
    def test_no_evidence(self):
        from app.services.evidence_service import _build_confidence_reason
        reason = _build_confidence_reason(20, 0, 0, False, False)
        assert "No evidence" in reason
        assert "Low confidence" in reason

    def test_limited_evidence(self):
        from app.services.evidence_service import _build_confidence_reason
        reason = _build_confidence_reason(40, 2, 1, False, False)
        assert "Limited evidence" in reason

    def test_high_confidence_with_baseline(self):
        from app.services.evidence_service import _build_confidence_reason
        reason = _build_confidence_reason(85, 10, 3, False, True)
        assert "High confidence" in reason
        assert "baseline" in reason

    def test_fallback_mentioned(self):
        from app.services.evidence_service import _build_confidence_reason
        reason = _build_confidence_reason(30, 5, 2, True, False)
        assert "LLM was unavailable" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_zero_evidence_still_produces_provenance(self):
        from app.services.evidence_service import build_enriched_provenance

        prov = build_enriched_provenance(
            pipeline_meta=None, analyses=[], clusters=[],
            has_baseline=False, has_release_decision=False,
            fallback_used=True, generated_at=None,
        )
        assert prov["evidence_count"] == 0
        assert prov["confidence"] >= 0
        assert "No evidence" in prov["confidence_reason"]

    def test_conflicting_sources(self):
        """Multiple sources for same evidence type should all be recorded."""
        from app.services.evidence_service import build_enriched_provenance

        analyses = [
            {"test_case_id": "t1", "evidence_references": [
                {"source": "splunk", "excerpt": "Error A"},
                {"source": "stacktrace", "excerpt": "Error B"},
            ]},
        ]
        prov = build_enriched_provenance(
            pipeline_meta=None, analyses=analyses, clusters=[],
            has_baseline=False, has_release_decision=False,
            fallback_used=False, generated_at=None,
        )
        assert "splunk" in prov["sources_used"]
        assert "stacktrace" in prov["sources_used"]

    def test_deterministic_high_ai_low(self):
        """High deterministic checks but fallback used → moderate confidence."""
        from app.services.evidence_service import build_enriched_provenance

        prov = build_enriched_provenance(
            pipeline_meta=None, analyses=[], clusters=[{"cluster_id": "x"}],
            has_baseline=True, has_release_decision=True,
            fallback_used=True, generated_at=None,
        )
        # Has baseline + release + clusters → deterministic checks present
        assert "semantic_clustering" in prov["deterministic_checks_used"]
        assert "baseline_comparison" in prov["deterministic_checks_used"]
        # But fallback reduces confidence
        assert prov["fallback_used"] is True
        assert prov["confidence"] < 70  # Moderate due to fallback

    def test_tool_name_mapping(self):
        """Pipeline tool names should map to source systems."""
        from app.services.evidence_service import build_enriched_provenance

        prov = build_enriched_provenance(
            pipeline_meta={"tools_used": [
                "fetch_stacktrace", "query_splunk", "analyze_ocp",
                "embed_and_cluster", "fetch_app_metrics", "fetch_build_changes",
                "validate_api_contract",
            ]},
            analyses=[], clusters=[], has_baseline=False,
            has_release_decision=False, fallback_used=False, generated_at=None,
        )
        assert "splunk" in prov["sources_used"]
        assert "stacktrace" in prov["sources_used"]
        assert "ocp" in prov["sources_used"]
        assert "chromadb" in prov["sources_used"]
        assert "prometheus" in prov["sources_used"]
        assert "github" in prov["sources_used"]
        assert "api_contract" in prov["sources_used"]
