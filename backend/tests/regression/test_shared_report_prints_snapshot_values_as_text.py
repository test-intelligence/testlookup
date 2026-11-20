"""A shared report prints every snapshot value as text or as a number, never as markup.

The rule: a run-intelligence snapshot can arrive through a project import, so
its payload is untrusted. Whatever a field holds (a string where a number is
expected, markup inside a label) the share page shows it as escaped text or as
a coerced number, and the PDF never parses it as ReportLab markup (which could
fetch a remote image). ``compose_report`` hands the renderers real numbers.
Both share responses, and the refusal page, carry a content security policy
that allows inline styles only, plus ``nosniff``, ``no-referrer``,
``no-store`` and ``noindex``.

The page itself must keep working under that policy: it uses inline styles and
nothing else (no script, image, font, frame or external stylesheet).
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.postgres import get_db
from app.routers import shared_reports
from app.services import report_distribution_policy as policy
from app.services.report_composition_service import ReportData, compose_report
from app.services.report_html_renderer import _render_executive_panel_html, render_report_html
from app.services.report_pdf_renderer import render_report_pdf
from app.services.review_envelope import ReviewEnvelope

pytestmark = pytest.mark.regression

# One value that is markup for a browser AND for ReportLab's paragraph parser.
MARKUP = '<img src="http://127.0.0.1:9/x.png" onerror="alert(1)"/><script>alert(2)</script>'
# The same idea with nothing ReportLab rejects: on the unfixed renderer this one is
# parsed and the image is fetched.
PDF_IMAGE = '<img src="http://127.0.0.1:9/x.png"/>'
RAW_TAGS = ("<img", "<script", "<svg", 'onerror="')

EXPECTED_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-robots-tag": "noindex",
}


def _report(layout: str = "engineering", **overrides) -> ReportData:
    values: dict = dict(
        run_id="run-1", build_number="build-42", branch="main", project_name="Proj",
        generated_at="2026-11-20T10:00:00+00:00", layout=layout,
        pass_rate=85.5, total_tests=200, passed_tests=171, failed_tests=25, skipped_tests=4,
        executive_summary="Summary.", release_recommendation="GO", risk_score=15,
        composite_risk=42.3, release_reasoning="Fine.",
        dimension_scores=[{"label": "User Impact", "score": 30, "weight": 0.25, "contribution": 7.5}],
        category_breakdown={"PRODUCT_BUG": 10, "FLAKY": 3},
        baseline_diff={"pass_rate_delta": -5.2, "new_failures_count": 2, "resolved_count": 1,
                       "regression_classification": "new_regression"},
        action_plan={"fix_recommendations": ["Fix auth"], "validation_steps": ["Rerun"]},
        provenance={"confidence": 90, "sources_used": ["splunk"], "evidence_count": 5},
        failure_clusters=[{"label": "Auth", "size": 7, "criticality_level": "HIGH",
                           "representative_error": "timeout"}],
        top_analyses=[{"test_name": "t", "failure_category": "PRODUCT_BUG",
                       "root_cause_summary": "Timeout", "confidence_score": 85}],
    )
    values.update(overrides)
    return ReportData(**values)


def _assert_no_markup(page: str) -> None:
    lowered = page.lower()
    for tag in RAW_TAGS:
        assert tag not in lowered, f"{tag!r} reached the page unescaped"


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a URL or a socket is recorded and refused."""
    attempts: list[str] = []

    def _refuse(*args, **kwargs):
        attempts.append(repr(args[:1]))
        raise AssertionError("the renderer tried to open a network resource")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)
    monkeypatch.setattr("socket.create_connection", _refuse)
    return attempts


# ── the HTML renderer ────────────────────────────────────────────────────────

def _poison_html(field: str) -> ReportData:
    report = _report()
    if field in ("total_tests", "passed_tests", "failed_tests", "skipped_tests", "risk_score",
                 "pass_rate", "layout"):
        setattr(report, field, MARKUP)
        if field == "layout":
            report.failure_clusters = []
    elif field == "cluster_size":
        report.failure_clusters[0]["size"] = MARKUP
    elif field == "confidence":
        report.provenance["confidence"] = MARKUP
    elif field == "category_count":
        report.category_breakdown["PRODUCT_BUG"] = MARKUP  # type: ignore[assignment]
    elif field == "dimension_score":
        report.dimension_scores[0]["score"] = MARKUP
    elif field == "pass_rate_delta":
        report.baseline_diff["pass_rate_delta"] = MARKUP
    elif field in ("new_failures_count", "resolved_count"):
        report.baseline_diff[field] = MARKUP
    else:  # pragma: no cover - a typo in the parameter list
        raise AssertionError(field)
    return report


HTML_FIELDS = [
    "total_tests", "passed_tests", "failed_tests", "skipped_tests", "risk_score", "pass_rate",
    "layout", "cluster_size", "confidence", "category_count", "dimension_score",
    "pass_rate_delta", "new_failures_count", "resolved_count",
]


@pytest.mark.parametrize("field", HTML_FIELDS)
def test_the_share_page_never_prints_a_snapshot_value_as_markup(field):
    page = render_report_html(_poison_html(field), shared_by=MARKUP, expires_at=MARKUP)
    _assert_no_markup(page)


def test_the_executive_panel_never_prints_a_value_as_markup():
    panel = {
        "headline": MARKUP,
        "status_signal": MARKUP,
        "risk_score": MARKUP,
        "metrics": {key: MARKUP for key in (
            "pass_rate", "total_tests", "failed", "skipped", "failure_clusters", "anomaly_count")},
        "dominant_failure": {"category": MARKUP, "count": MARKUP, "percentage": MARKUP},
        "key_takeaways": [MARKUP],
        "baseline_comparison": {"pass_rate_delta": MARKUP, "new_failures": MARKUP, "resolved": MARKUP},
        "next_actions": [MARKUP],
    }
    _assert_no_markup(_render_executive_panel_html(panel))
    _assert_no_markup(render_report_html(_report(executive_panel=panel)))


def test_a_normal_report_prints_the_same_figures_as_before():
    page = render_report_html(_report(), shared_by="Dana", expires_at="2026-12-01")
    assert '<div style="font-size:28px;font-weight:bold;color:#059669">85.5%</div>' in page
    assert '<div style="font-size:20px;font-weight:bold">200</div>' in page
    assert "Total | 171 passed | 25 failed | 4 skipped" in page
    assert "Risk Score: <b>15</b>/100" in page
    assert '<td style="padding:4px 8px;text-align:center">7</td>' in page
    assert "Confidence: 90% |" in page
    assert '<td style="padding:4px 12px;text-align:center">10</td>' in page
    assert '<td style="padding:6px 12px;text-align:center">30.0</td>' in page
    assert "Pass rate delta: <b>-5.2%</b>" in page
    assert "New failures: <b>2</b>" in page and "Resolved: <b>1</b>" in page
    assert "| Engineering Report</p>" in page
    assert "Shared by <b>Dana</b>" in page


def test_a_normal_executive_panel_prints_the_same_figures_as_before():
    panel = {
        "headline": "Mostly green", "status_signal": "GO", "risk_score": 12,
        "metrics": {"pass_rate": 97.25, "total_tests": 400, "failed": 11, "skipped": 2,
                    "failure_clusters": 3, "anomaly_count": 1},
        "dominant_failure": {"category": "product_bug", "count": 8, "percentage": 72.7},
        "baseline_comparison": {"pass_rate_delta": 1.5, "new_failures": 2, "resolved": 4},
    }
    html = _render_executive_panel_html(panel)
    assert "Risk 12/100" in html
    assert "<b>97.2%</b><br><small>Pass Rate</small>" in html or "<b>97.3%</b><br><small>Pass Rate</small>" in html
    assert "<b>400</b><br><small>Total</small>" in html
    assert "<b>11</b><br><small>Failed</small>" in html
    assert "<b>Product Bug</b> — 8 failures (73%)" in html
    assert "Pass rate: <b>+1.5%</b> | New failures: <b>2</b> | Resolved: <b>4</b>" in html


def test_the_share_page_needs_nothing_the_policy_forbids():
    """The policy allows inline styles only: the page must not rely on a script,
    an image, a font, a frame, a form or an external stylesheet."""
    panel = {"headline": "h", "status_signal": "GO", "risk_score": 1, "metrics": {},
             "dominant_failure": {"category": "x"}, "key_takeaways": ["a"],
             "baseline_comparison": {"pass_rate_delta": 1}, "next_actions": ["b"]}
    pages = [
        render_report_html(_report(draft_watermark="DRAFT"), shared_by="Dana", expires_at="2026-12-01"),
        render_report_html(_report(layout="executive", executive_panel=panel)),
        shared_reports._refusal_page("Not reviewed yet."),
    ]
    forbidden = re.compile(
        r"<(script|img|link|iframe|object|embed|form|base|audio|video|svg)\b|\bsrc\s*=|url\(|@import|@font-face"
        r"|\son[a-z]+\s*=|javascript:",
        re.IGNORECASE,
    )
    for page in pages:
        assert forbidden.search(page) is None, forbidden.search(page)


# ── the PDF renderer ─────────────────────────────────────────────────────────

PDF_FIELDS = ["confidence", "evidence_count", "generated_at", "layout", "new_failures_count",
              "resolved_count", "pass_rate_delta", "pass_rate", "composite_risk", "dimension_score",
              "category_count"]


@pytest.mark.parametrize("markup", [MARKUP, PDF_IMAGE], ids=["script", "image"])
@pytest.mark.parametrize("field", PDF_FIELDS)
def test_the_pdf_never_parses_a_snapshot_value_as_markup(field, markup, no_network):
    report = _report()
    if field in ("confidence", "evidence_count"):
        report.provenance[field] = markup
    elif field in ("generated_at", "layout", "pass_rate", "composite_risk"):
        setattr(report, field, markup)
    elif field == "dimension_score":
        report.dimension_scores[0]["score"] = markup
    elif field == "category_count":
        report.category_breakdown["PRODUCT_BUG"] = markup  # type: ignore[assignment]
    else:
        report.baseline_diff[field] = markup

    pdf = render_report_pdf(report)

    assert pdf[:5] == b"%PDF-"
    assert no_network == []


def test_a_normal_report_still_renders_as_a_pdf(no_network):
    assert render_report_pdf(_report())[:5] == b"%PDF-"
    assert render_report_pdf(_report(layout="executive"))[:5] == b"%PDF-"
    assert no_network == []


# ── compose_report: the renderers receive numbers ────────────────────────────

def _hostile_payload() -> dict:
    """Every string field carries markup; every numeric field is a string with markup."""
    return {
        "run": {"build_number": MARKUP, "branch": MARKUP, "project_name": MARKUP,
                "pass_rate": MARKUP, "total_tests": MARKUP, "passed_tests": MARKUP,
                "failed_tests": MARKUP, "skipped_tests": MARKUP},
        "structured_summary": {
            "executive_summary": MARKUP,
            "layer4_action_plan": {"immediate_mitigation": [MARKUP], "fix_recommendations": [MARKUP],
                                   "validation_steps": [MARKUP], "owner_hints": {"qa": MARKUP}},
        },
        "release_decision": {"recommendation": MARKUP, "risk_score": MARKUP, "composite_risk": MARKUP,
                             "blocking_issues": [MARKUP], "conditions_for_go": [MARKUP],
                             "reasoning": MARKUP},
        "dimension_scores": [{"name": MARKUP, "label": MARKUP, "score": MARKUP, "weight": MARKUP,
                              "contribution": MARKUP}],
        "category_breakdown": {MARKUP: MARKUP, "FLAKY": MARKUP},
        "affected_suites": [{"suite": MARKUP, "failed_count": MARKUP}],
        "failure_clusters": [{"cluster_id": MARKUP, "label": MARKUP, "size": MARKUP,
                              "criticality_level": MARKUP}],
        "top_analyses": [{"test_name": MARKUP, "failure_category": MARKUP,
                          "root_cause_summary": MARKUP, "confidence_score": MARKUP}],
        "what_changed_since_last_good_run": {"pass_rate_delta": MARKUP, "new_failures": [MARKUP],
                                             "resolved_failures": [MARKUP],
                                             "regression_classification": MARKUP},
        "provenance": {"confidence": MARKUP, "sources_used": [MARKUP], "evidence_count": MARKUP,
                       "deterministic_checks_used": [MARKUP]},
        "defect_candidates": [],
    }


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


async def _compose(monkeypatch, payload: dict, *, layout: str, run=None) -> ReportData:
    monkeypatch.setattr(
        "app.services.intelligence_snapshot_service.get_or_compute", AsyncMock(return_value=payload),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(run)))
    return await compose_report(db, uuid.uuid4(), layout=layout)  # type: ignore[arg-type]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@pytest.mark.parametrize("layout", ["executive", "engineering"])
async def test_a_hostile_snapshot_composes_to_numbers_and_renders_as_text(monkeypatch, layout, no_network):
    run = SimpleNamespace(build_number="b-9", branch="main", pass_rate=91.0, total_tests=50,
                          passed_tests=45, failed_tests=4, skipped_tests=1)
    report = await _compose(monkeypatch, _hostile_payload(), layout=layout, run=run)

    # A value that is not a number falls back to the run row (or to zero).
    assert (report.pass_rate, report.total_tests, report.passed_tests,
            report.failed_tests, report.skipped_tests) == (91.0, 50, 45, 4, 1)
    assert report.risk_score == 0 and report.composite_risk == 0.0
    for dimension in report.dimension_scores:
        assert all(_is_number(dimension[key]) for key in ("score", "weight", "contribution"))
    assert all(_is_number(count) for count in report.category_breakdown.values())
    assert all(_is_number(cluster["size"]) for cluster in report.failure_clusters)
    assert all(_is_number(item["confidence_score"]) for item in report.top_analyses)
    assert report.provenance["confidence"] is None
    assert report.provenance["evidence_count"] == 0
    assert report.baseline_diff["pass_rate_delta"] is None
    assert isinstance(report.build_number, str) and isinstance(report.project_name, str)

    report.draft_watermark = MARKUP
    _assert_no_markup(render_report_html(report, shared_by=MARKUP, expires_at=MARKUP))
    assert render_report_pdf(report)[:5] == b"%PDF-"
    assert no_network == []


async def test_a_normal_snapshot_composes_to_the_same_values(monkeypatch):
    payload = {
        "run": {"build_number": "build-42", "branch": "main", "project_name": "Proj", "pass_rate": 85.5,
                "total_tests": 200, "passed_tests": 171, "failed_tests": 25, "skipped_tests": 4},
        "structured_summary": {"executive_summary": "Summary."},
        "release_decision": {"recommendation": "CONDITIONAL_GO", "risk_score": 42, "composite_risk": 42.3,
                             "blocking_issues": ["Auth"], "conditions_for_go": ["Rerun"],
                             "reasoning": "Moderate."},
        "dimension_scores": [{"name": "user_impact", "label": "User Impact", "score": 60.0,
                              "weight": 0.25, "contribution": 15.0}],
        "category_breakdown": {"PRODUCT_BUG": 15, "FLAKY": 5},
        "failure_clusters": [{"cluster_id": "cl_001", "label": "Auth", "size": 15,
                              "criticality_level": "HIGH"}],
        "top_analyses": [{"test_name": "t", "failure_category": "PRODUCT_BUG",
                          "root_cause_summary": "Timeout", "confidence_score": 85}],
        "what_changed_since_last_good_run": {"pass_rate_delta": -5.2, "new_failures": ["a", "b"],
                                             "resolved_failures": ["c"],
                                             "regression_classification": "new_regression"},
        "provenance": {"confidence": 82, "sources_used": ["splunk"], "evidence_count": 12,
                       "deterministic_checks_used": ["flaky_detection"]},
    }
    report = await _compose(monkeypatch, payload, layout="engineering")

    assert (report.build_number, report.branch, report.project_name) == ("build-42", "main", "Proj")
    assert (report.pass_rate, report.total_tests, report.passed_tests,
            report.failed_tests, report.skipped_tests) == (85.5, 200, 171, 25, 4)
    assert (report.release_recommendation, report.risk_score, report.composite_risk) == (
        "CONDITIONAL_GO", 42, 42.3)
    assert report.blocking_issues == ["Auth"] and report.conditions_for_go == ["Rerun"]
    assert report.dimension_scores == payload["dimension_scores"]
    assert report.category_breakdown == {"PRODUCT_BUG": 15, "FLAKY": 5}
    assert report.failure_clusters == payload["failure_clusters"]
    assert report.top_analyses == payload["top_analyses"]
    assert report.baseline_diff == {
        "pass_rate_delta": -5.2, "new_failures_count": 2, "resolved_count": 1,
        "regression_classification": "new_regression", "new_failures": ["a", "b"],
    }
    assert report.provenance == {"confidence": 82, "sources_used": ["splunk"], "evidence_count": 12,
                                 "deterministic_checks": ["flaky_detection"]}


# ── the share responses' headers ─────────────────────────────────────────────

class _DB:
    def add(self, row) -> None:
        pass

    async def commit(self) -> None:
        pass


def _client(monkeypatch, *, allowed: bool) -> AsyncClient:
    link = SimpleNamespace(run_id=uuid.uuid4(), project_id=uuid.uuid4(), report_layout="engineering",
                           created_by_name="qa", expires_at=None)
    monkeypatch.setattr("app.services.share_link_service.validate_share_link", AsyncMock(return_value=link))
    monkeypatch.setattr("app.services.report_composition_service.compose_report",
                        AsyncMock(return_value=_report()))
    envelope = ReviewEnvelope(True, "accepted" if allowed else "pending_review", "")
    decision = policy.DistributionDecision(
        allowed, policy.REVIEWED if allowed else policy.REFUSED, envelope, None, True,
    )
    monkeypatch.setattr(policy, "decide_run_distribution", AsyncMock(return_value=decision))
    monkeypatch.setattr(policy, "record_distribution", AsyncMock())
    app = FastAPI()
    app.include_router(shared_reports.router)

    async def _db():
        yield _DB()

    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _assert_share_headers(resp) -> None:
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(name) == value, name


async def test_the_share_page_carries_the_protective_headers(monkeypatch):
    async with _client(monkeypatch, allowed=True) as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert "Release Report" in resp.text
    _assert_share_headers(resp)


async def test_the_shared_pdf_carries_the_protective_headers(monkeypatch):
    async with _client(monkeypatch, allowed=True) as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith('attachment; filename="shared-report-')
    assert resp.content[:5] == b"%PDF-"
    _assert_share_headers(resp)


async def test_the_refusal_page_carries_the_protective_headers(monkeypatch):
    async with _client(monkeypatch, allowed=False) as client:
        resp = await client.get("/api/v1/shared/reports/tok-abc", headers={"Accept": "text/html"})
    assert resp.status_code == 409
    assert "Report awaiting review" in resp.text
    _assert_share_headers(resp)
