"""
Unit tests for defect_promotion_service.py — pure function tests, no DB required.
"""
import sys
import types

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Tests for _dim_scores_from_analyses ───────────────────────────────────────

def test_dim_scores_empty_analyses():
    from app.services.defect_promotion_service import _dim_scores_from_analyses
    scores = _dim_scores_from_analyses([])
    assert isinstance(scores, dict)
    # With no analyses, total=1, product_bugs=0, infra=0, flaky=0
    assert scores["user_impact"] == 0.0
    assert scores["env_sensitivity"] == 0.0
    assert 0.0 <= scores["reproducibility"] <= 100.0
    assert "blast_radius" in scores
    assert "diagnosis_conf" in scores


def test_dim_scores_product_bug_analyses():
    from app.services.defect_promotion_service import _dim_scores_from_analyses
    from app.models.postgres import FailureCategory

    mock_a = MagicMock()
    mock_a.failure_category = FailureCategory.PRODUCT_BUG
    mock_a.is_flaky = False
    mock_a.confidence_score = 80

    scores = _dim_scores_from_analyses([mock_a])
    assert scores["user_impact"] > 0.0
    assert scores["env_sensitivity"] == 0.0
    assert scores["regression_likely"] == 50.0


def test_dim_scores_infrastructure_analysis():
    from app.services.defect_promotion_service import _dim_scores_from_analyses
    from app.models.postgres import FailureCategory

    mock_a = MagicMock()
    mock_a.failure_category = FailureCategory.INFRASTRUCTURE
    mock_a.is_flaky = False
    mock_a.confidence_score = 60

    scores = _dim_scores_from_analyses([mock_a])
    assert scores["env_sensitivity"] > 0.0
    # No product bugs so regression_likely should be 20
    assert scores["regression_likely"] == 20.0


def test_dim_scores_flaky_analysis():
    from app.services.defect_promotion_service import _dim_scores_from_analyses
    from app.models.postgres import FailureCategory

    mock_a = MagicMock()
    mock_a.failure_category = FailureCategory.FLAKY
    mock_a.is_flaky = True
    mock_a.confidence_score = 50

    scores = _dim_scores_from_analyses([mock_a])
    # Flaky test reduces reproducibility (non_flaky_frac = 0)
    assert scores["reproducibility"] == 0.0


def test_dim_scores_clamped_to_100():
    from app.services.defect_promotion_service import _dim_scores_from_analyses
    from app.models.postgres import FailureCategory

    # Create 50 analyses to push blast_radius beyond 100
    analyses = []
    for _ in range(50):
        mock_a = MagicMock()
        mock_a.failure_category = FailureCategory.PRODUCT_BUG
        mock_a.is_flaky = False
        mock_a.confidence_score = 90
        analyses.append(mock_a)

    scores = _dim_scores_from_analyses(analyses)
    for v in scores.values():
        assert v <= 100.0
        assert v >= 0.0


def test_failure_category_value_accepts_enum_and_string():
    from app.services.defect_promotion_service import _failure_category_value
    from app.models.postgres import FailureCategory

    assert _failure_category_value(FailureCategory.PRODUCT_BUG) == "PRODUCT_BUG"
    assert _failure_category_value("INFRASTRUCTURE") == "INFRASTRUCTURE"
    assert _failure_category_value(None) == "UNKNOWN"


# ── Tests for _composite_to_severity ─────────────────────────────────────────

def test_composite_to_severity_critical():
    from app.services.defect_promotion_service import _composite_to_severity
    assert _composite_to_severity(70.0) == "CRITICAL"
    assert _composite_to_severity(100.0) == "CRITICAL"


def test_composite_to_severity_high():
    from app.services.defect_promotion_service import _composite_to_severity
    assert _composite_to_severity(50.0) == "HIGH"
    assert _composite_to_severity(69.9) == "HIGH"


def test_composite_to_severity_medium():
    from app.services.defect_promotion_service import _composite_to_severity
    assert _composite_to_severity(30.0) == "MEDIUM"
    assert _composite_to_severity(49.9) == "MEDIUM"


def test_composite_to_severity_low():
    from app.services.defect_promotion_service import _composite_to_severity
    assert _composite_to_severity(0.0) == "LOW"
    assert _composite_to_severity(29.9) == "LOW"


# ── Tests for _build_evidence_bundle ─────────────────────────────────────────

def test_build_evidence_bundle_empty():
    from app.services.defect_promotion_service import _build_evidence_bundle
    bundle = _build_evidence_bundle([], None)
    assert bundle == {"stack_traces": [], "log_anomalies": [], "data_sources": []}


def test_build_evidence_bundle_with_analyses():
    from app.services.defect_promotion_service import _build_evidence_bundle

    mock_a = MagicMock()
    mock_a.evidence_references = [
        {"source": "stacktrace", "excerpt": "NullPointerException at line 42"},
        {"source": "splunk", "excerpt": "ERROR: connection timeout"},
    ]

    bundle = _build_evidence_bundle([mock_a], None)
    assert len(bundle["stack_traces"]) > 0
    assert "stacktrace" in bundle["data_sources"]
    assert "splunk" in bundle["data_sources"]


def test_build_evidence_bundle_with_finding():
    from app.services.defect_promotion_service import _build_evidence_bundle

    mock_finding = MagicMock()
    mock_finding.evidence = [
        {"source": "log_anomaly", "excerpt": "High error rate detected"},
    ]

    bundle = _build_evidence_bundle([], mock_finding)
    assert len(bundle["log_anomalies"]) > 0
    assert "log_anomaly" in bundle["data_sources"]


def test_build_evidence_bundle_no_finding():
    from app.services.defect_promotion_service import _build_evidence_bundle
    # None finding should not crash
    bundle = _build_evidence_bundle([], None)
    assert bundle["log_anomalies"] == []


# ── Tests for _find_duplicate_semantic ────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_duplicate_empty_open_defects():
    from app.services.defect_promotion_service import _find_duplicate_semantic

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    dup_id, found = await _find_duplicate_semantic(
        project_id="00000000-0000-0000-0000-000000000001",
        duplicate_hint="Login page timeout",
        db=mock_db,
    )
    assert dup_id is None
    assert found is False


@pytest.mark.asyncio
async def test_find_duplicate_difflib_match():
    from app.services.defect_promotion_service import _find_duplicate_semantic
    import uuid

    existing_id = uuid.uuid4()
    mock_defect = MagicMock()
    mock_defect.id = existing_id
    mock_defect.title = "Login page timeout on checkout flow"
    mock_defect.cluster_id = "cluster-1"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_defect]
    mock_db.execute.return_value = mock_result

    fake_chromadb = types.ModuleType("chromadb")
    fake_chromadb.HttpClient = MagicMock(side_effect=ImportError("no chromadb"))
    with patch.dict(sys.modules, {"chromadb": fake_chromadb}):
        dup_id, found = await _find_duplicate_semantic(
            project_id="00000000-0000-0000-0000-000000000001",
            duplicate_hint="Login page timeout on checkout flow",
            db=mock_db,
        )
    assert found is True
    assert dup_id == str(existing_id)


@pytest.mark.asyncio
async def test_find_duplicate_no_match():
    from app.services.defect_promotion_service import _find_duplicate_semantic
    import uuid

    mock_defect = MagicMock()
    mock_defect.id = uuid.uuid4()
    mock_defect.title = "Completely unrelated database migration error"
    mock_defect.cluster_id = "cluster-2"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_defect]
    mock_db.execute.return_value = mock_result

    fake_chromadb = types.ModuleType("chromadb")
    fake_chromadb.HttpClient = MagicMock(side_effect=ImportError("no chromadb"))
    with patch.dict(sys.modules, {"chromadb": fake_chromadb}):
        dup_id, found = await _find_duplicate_semantic(
            project_id="00000000-0000-0000-0000-000000000001",
            duplicate_hint="Login page timeout",
            db=mock_db,
        )
    assert found is False
    assert dup_id is None


@pytest.mark.asyncio
async def test_generate_jira_content_accepts_string_failure_category():
    from app.services.defect_promotion_service import _generate_jira_content

    cluster = MagicMock()
    cluster.label = "Checkout failures"
    cluster.size = 3
    cluster.representative_error = "Timeout while calling payment service"

    analysis = MagicMock()
    analysis.failure_category = "INFRASTRUCTURE"
    analysis.root_cause_summary = "Connection pool exhausted."
    analysis.confidence_score = 82
    analysis.evidence_references = [{"source": "splunk", "excerpt": "ConnectionTimeoutException"}]

    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='{"title":"Checkout timeout","description":"desc","severity":"HIGH","component":"payments","owner_team":"backend","labels":["cluster-promoted"],"duplicate_hint":"checkout timeout"}'
        )
    )

    with patch("app.services.defect_promotion_service.get_llm", return_value=fake_llm):
        content = await _generate_jira_content(cluster, [analysis])

    assert content["title"] == "Checkout timeout"
    assert content["component"] == "payments"
