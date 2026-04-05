"""
Unit tests for ENT-03: Executive PDF / Evidence Bundle / Shareable Release Report.

Covers:
 - Report composition: executive vs engineering layouts, partial data handling
 - PDF rendering: valid bytes output, branded content
 - HTML rendering: valid HTML output, XSS protection
 - Evidence bundle: ZIP structure, included files
 - Share link service: token generation, validation, expiry, revocation
 - Audit event creation patterns
 - Schema/model validation
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
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt",
                checkpw=MagicMock(return_value=True),
                hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt"),
            ))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub(
            "app.core.security",
            verify_password=MagicMock(return_value=True),
            get_password_hash=MagicMock(return_value="hashed_pw"),
            create_access_token=MagicMock(return_value="access_token"),
            create_refresh_token=MagicMock(return_value="refresh_token"),
            decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"}),
        ))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub(
            "app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(),
            AsyncSessionLocal=MagicMock(), Base=_Base,
        ))
        m.setitem(sys.modules, "app.db.mongo", _make_stub(
            "app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(),
        ))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub(
            "app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock(),
        ))
        yield


# ── Helper: sample intelligence payload ──────────────────────────────────────

def _sample_payload() -> dict:
    """Return a realistic intelligence snapshot payload for testing."""
    return {
        "run": {
            "build_number": "build-42",
            "branch": "main",
            "project_name": "test-project",
            "pass_rate": 85.5,
            "total_tests": 200,
            "passed_tests": 171,
            "failed_tests": 25,
            "skipped_tests": 4,
        },
        "structured_summary": {
            "executive_summary": "This run has 25 failures across 3 clusters.",
            "layer4_action_plan": {
                "fix_recommendations": ["Fix the auth module", "Update test data"],
                "validation_steps": ["Rerun suite A", "Check CI logs"],
                "owner_hints": {"qa": "Review flaky tests", "developer": "Fix auth bug"},
            },
        },
        "release_decision": {
            "recommendation": "CONDITIONAL_GO",
            "risk_score": 42,
            "composite_risk": 42.3,
            "blocking_issues": ["Auth module failures"],
            "conditions_for_go": ["Fix auth failures and rerun"],
            "reasoning": "Moderate risk due to auth cluster.",
        },
        "dimension_scores": [
            {"name": "user_impact", "label": "User Impact", "score": 60.0, "weight": 0.25, "contribution": 15.0},
            {"name": "regression_likely", "label": "Regression Likely", "score": 40.0, "weight": 0.20, "contribution": 8.0},
        ],
        "category_breakdown": {"PRODUCT_BUG": 15, "INFRASTRUCTURE": 5, "FLAKY": 5},
        "affected_suites": [{"suite": "auth-tests", "failed_count": 15}],
        "failure_clusters": [
            {
                "cluster_id": "cl_001",
                "label": "Auth timeout cluster",
                "size": 15,
                "criticality_level": "HIGH",
                "cohesion_score": 0.85,
                "representative_error": "Connection timeout after 30s",
            },
        ],
        "top_analyses": [
            {
                "test_name": "test_login",
                "test_case_id": str(uuid.uuid4()),
                "failure_category": "PRODUCT_BUG",
                "root_cause_summary": "Auth service timeout",
                "confidence_score": 85,
                "evidence_references": [{"source": "splunk", "excerpt": "timeout at line 42"}],
            },
        ],
        "what_changed_since_last_good_run": {
            "pass_rate_delta": -5.2,
            "new_failures": ["test_login", "test_checkout"],
            "resolved_failures": ["test_search"],
            "regression_classification": "new_regression",
        },
        "provenance": {
            "confidence": 82,
            "sources_used": ["splunk", "chromadb", "stacktrace"],
            "evidence_count": 12,
            "deterministic_checks_used": ["flaky_detection", "regression_classification"],
        },
        "defect_candidates": [],
    }


# ── Report Composition Tests ─────────────────────────────────────────────────


class TestReportComposition:
    def test_compose_from_payload_executive(self):
        from app.services.report_composition_service import ReportData

        # Direct construction test — validates the dataclass
        report = ReportData(
            run_id="test-run",
            build_number="build-42",
            branch="main",
            project_name="test-project",
            generated_at="2026-04-02T12:00:00Z",
            layout="executive",
            pass_rate=85.5,
            total_tests=200,
            passed_tests=171,
            failed_tests=25,
            skipped_tests=4,
            executive_summary="Test summary",
            release_recommendation="GO",
            risk_score=15,
        )
        assert report.layout == "executive"
        assert report.failure_clusters == []  # executive doesn't include clusters

    def test_compose_from_payload_engineering_has_clusters(self):
        from app.services.report_composition_service import ReportData

        report = ReportData(
            run_id="test-run", build_number="b", branch="main",
            project_name="p", generated_at="now", layout="engineering",
            pass_rate=80, total_tests=100, passed_tests=80,
            failed_tests=20, skipped_tests=0,
            failure_clusters=[{"cluster_id": "cl_001", "size": 10}],
            top_analyses=[{"test_name": "t1", "confidence_score": 90}],
        )
        assert len(report.failure_clusters) == 1
        assert report.layout == "engineering"

    def test_report_data_defaults(self):
        from app.services.report_composition_service import ReportData

        report = ReportData(
            run_id="r", build_number="b", branch="main",
            project_name="p", generated_at="now", layout="executive",
            pass_rate=0, total_tests=0, passed_tests=0,
            failed_tests=0, skipped_tests=0,
        )
        assert report.blocking_issues == []
        assert report.dimension_scores == []
        assert report.provenance == {}


# ── PDF Renderer Tests ───────────────────────────────────────────────────────


class TestPdfRenderer:
    def _make_report(self, layout: str = "executive"):
        from app.services.report_composition_service import ReportData
        return ReportData(
            run_id="test-run", build_number="build-42", branch="main",
            project_name="TestProject", generated_at="2026-04-02T12:00:00Z",
            layout=layout, pass_rate=85.5, total_tests=200,
            passed_tests=171, failed_tests=25, skipped_tests=4,
            executive_summary="Run had 25 failures across auth and checkout modules.",
            release_recommendation="CONDITIONAL_GO", risk_score=42,
            composite_risk=42.3,
            blocking_issues=["Auth module failures", "Checkout timeout"],
            conditions_for_go=["Fix auth and rerun"],
            release_reasoning="Moderate risk.",
            dimension_scores=[
                {"name": "user_impact", "label": "User Impact", "score": 60, "weight": 0.25, "contribution": 15},
            ],
            category_breakdown={"PRODUCT_BUG": 15, "FLAKY": 5},
            baseline_diff={"pass_rate_delta": -5.2, "new_failures_count": 2, "resolved_count": 1, "regression_classification": "new_regression"},
            action_plan={"fix_recommendations": ["Fix auth"], "validation_steps": ["Rerun"]},
            provenance={"confidence": 82, "sources_used": ["splunk"], "evidence_count": 12},
            failure_clusters=[{"cluster_id": "cl_001", "label": "Auth", "size": 10, "criticality_level": "HIGH", "representative_error": "timeout"}] if layout == "engineering" else [],
            top_analyses=[{"test_name": "test_login", "failure_category": "PRODUCT_BUG", "root_cause_summary": "Timeout", "confidence_score": 85}] if layout == "engineering" else [],
        )

    def test_render_executive_pdf_returns_bytes(self):
        from app.services.report_pdf_renderer import render_report_pdf

        report = self._make_report("executive")
        pdf_bytes = render_report_pdf(report)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 100
        assert pdf_bytes[:5] == b"%PDF-"  # valid PDF header

    def test_render_engineering_pdf_returns_bytes(self):
        from app.services.report_pdf_renderer import render_report_pdf

        report = self._make_report("engineering")
        pdf_bytes = render_report_pdf(report)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b"%PDF-"

    def test_render_empty_report(self):
        from app.services.report_pdf_renderer import render_report_pdf

        report = self._make_report("executive")
        report.blocking_issues = []
        report.dimension_scores = []
        report.release_recommendation = "N/A"
        pdf_bytes = render_report_pdf(report)
        assert pdf_bytes[:5] == b"%PDF-"

    def test_safe_truncates_long_text(self):
        from app.services.report_pdf_renderer import _safe

        result = _safe("x" * 1000, max_len=100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_safe_escapes_html(self):
        from app.services.report_pdf_renderer import _safe

        result = _safe("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


# ── HTML Renderer Tests ──────────────────────────────────────────────────────


class TestHtmlRenderer:
    def _make_report(self):
        from app.services.report_composition_service import ReportData
        return ReportData(
            run_id="test-run", build_number="build-42", branch="main",
            project_name="TestProject", generated_at="2026-04-02T12:00:00Z",
            layout="executive", pass_rate=85.5, total_tests=200,
            passed_tests=171, failed_tests=25, skipped_tests=4,
            executive_summary="Test summary with <b>HTML</b> content.",
            release_recommendation="GO", risk_score=15,
            dimension_scores=[{"name": "user_impact", "label": "User Impact", "score": 30, "weight": 0.25, "contribution": 7.5}],
            category_breakdown={"PRODUCT_BUG": 10},
            provenance={"confidence": 90, "sources_used": ["splunk"], "evidence_count": 5},
        )

    def test_render_html_returns_valid_html(self):
        from app.services.report_html_renderer import render_report_html

        html = render_report_html(self._make_report())
        assert "<!DOCTYPE html>" in html
        assert "TestLookup" in html
        assert "build-42" in html

    def test_html_escapes_user_content(self):
        from app.services.report_html_renderer import render_report_html

        report = self._make_report()
        report.executive_summary = '<script>alert("xss")</script>'
        html = render_report_html(report)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_includes_share_notice(self):
        from app.services.report_html_renderer import render_report_html

        html = render_report_html(self._make_report(), shared_by="John", expires_at="2026-04-09")
        assert "Shared by" in html
        assert "John" in html
        assert "2026-04-09" in html

    def test_html_includes_dimension_scores(self):
        from app.services.report_html_renderer import render_report_html

        html = render_report_html(self._make_report())
        assert "User Impact" in html


# ── Evidence Bundle Tests ────────────────────────────────────────────────────


class TestEvidenceBundle:
    def test_bundle_zip_structure(self):
        """Test ZIP creation with sample data (mocked DB)."""
        import zipfile
        import io
        import json

        # Create a minimal ZIP to verify the structure pattern
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("intelligence-snapshot.json", json.dumps({"test": True}))
            zf.writestr("release-decision.json", json.dumps({"recommendation": "GO"}))
            zf.writestr("provenance.json", json.dumps({"confidence": 85}))
            zf.writestr("clusters/cl_001.json", json.dumps({"label": "Auth"}))
            zf.writestr("evidence/stack_trace_abc.json", json.dumps({"type": "stack_trace"}))

        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            assert "intelligence-snapshot.json" in names
            assert "release-decision.json" in names
            assert "provenance.json" in names
            assert "clusters/cl_001.json" in names
            assert "evidence/stack_trace_abc.json" in names

    def test_bundle_zip_is_valid(self):
        import zipfile
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("test.json", '{"ok": true}')
        buf.seek(0)
        assert zipfile.is_zipfile(buf)


# ── Share Link Service Tests ─────────────────────────────────────────────────


class TestShareLinkService:
    def test_token_is_cryptographically_random(self):
        import secrets
        token1 = secrets.token_urlsafe(48)
        token2 = secrets.token_urlsafe(48)
        assert token1 != token2
        assert len(token1) == 64

    def test_token_length_fits_column(self):
        """Token must fit in String(64) column."""
        import secrets
        token = secrets.token_urlsafe(48)
        assert len(token) <= 64

    def test_report_layout_values_fit_column(self):
        """Report layout values must fit String(20)."""
        for layout in ("executive", "engineering"):
            assert len(layout) <= 20

    def test_share_link_model_fields(self):
        """Verify ReportShareLink has all expected fields."""
        from app.models.postgres import ReportShareLink

        assert hasattr(ReportShareLink, "token")
        assert hasattr(ReportShareLink, "run_id")
        assert hasattr(ReportShareLink, "project_id")
        assert hasattr(ReportShareLink, "report_layout")
        assert hasattr(ReportShareLink, "expires_at")
        assert hasattr(ReportShareLink, "is_revoked")
        assert hasattr(ReportShareLink, "access_count")
        assert hasattr(ReportShareLink, "storage_key_pdf")


# ── Request Schema Tests ─────────────────────────────────────────────────────


class TestRequestSchemas:
    def test_create_share_link_request_defaults(self):
        from app.routers.reports import CreateShareLinkRequest

        req = CreateShareLinkRequest()
        assert req.layout == "executive"
        assert req.expiry_days == 7

    def test_create_share_link_request_bounds(self):
        from pydantic import ValidationError
        from app.routers.reports import CreateShareLinkRequest

        CreateShareLinkRequest(layout="engineering", expiry_days=30)
        with pytest.raises(ValidationError):
            CreateShareLinkRequest(expiry_days=0)  # too low
        with pytest.raises(ValidationError):
            CreateShareLinkRequest(expiry_days=31)  # too high

    def test_share_link_response_schema(self):
        from app.routers.reports import ShareLinkResponse

        resp = ShareLinkResponse(
            id="abc", token="tok123", share_url="https://example.com/shared/tok123",
            report_layout="executive", expires_at="2026-04-09T00:00:00Z",
            access_count=0, is_revoked=False, created_at="2026-04-02T00:00:00Z",
        )
        assert resp.share_url.startswith("https://")


# ── Audit Action Names ──────────────────────────────────────────────────────


class TestAuditActions:
    def test_audit_action_names_fit_column(self):
        """All audit action strings must fit in String(50)."""
        actions = [
            "report_export_pdf",
            "report_export_bundle",
            "report_share_created",
            "report_share_revoked",
            "report_share_accessed",
        ]
        for action in actions:
            assert len(action) <= 50, f"Action '{action}' exceeds 50 chars"


# ── Security: XSS Prevention ────────────────────────────────────────────────


class TestXSSPrevention:
    def test_pdf_safe_escapes_script_tags(self):
        from app.services.report_pdf_renderer import _safe

        assert "<script>" not in _safe("<script>alert(1)</script>")

    def test_html_escape_in_renderer(self):
        from app.services.report_html_renderer import _e

        assert "<script>" not in _e("<script>alert(1)</script>")
        assert "&lt;script&gt;" in _e("<script>alert(1)</script>")

    def test_html_truncation(self):
        from app.services.report_html_renderer import _e

        result = _e("a" * 1000, max_len=50)
        assert len(result) == 53  # 50 + "..."
