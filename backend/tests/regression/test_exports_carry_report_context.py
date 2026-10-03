"""VIZ-308: a report that leaves the product says which data it shows.

The Summary PDF's first page carries the report chrome's context block --
Project, Release, Test Suite, Window, Aggregation, Pass-rate basis, Generated
(UTC) and the "N of M" line -- and EVERY page repeats Project, Release and
Suite in its footer. Long suite lists are listed in full on page 1 and, in the
one-line footer, end in "+N more (listed on page 1)": never cut silently. The
emailed trends report carries the same block, escaped.

Text is read back out of the PDF (pypdf), as
``test_summary_pdf_states_pass_rate_basis.py`` does: the assertion is on what
a reader of the file sees, not on the renderer's inputs.
"""
from __future__ import annotations

import io
import re

import pytest

pytest.importorskip("reportlab")

from app.services import report_service  # noqa: E402
from app.services.report_context import (  # noqa: E402
    context_fields,
    footer_text,
    n_of_m_text,
)
from app.services.summary_report_pdf import render_summary_report_pdf  # noqa: E402

SUITES = [
    "PaymentSuite",
    "CheckoutSuite",
    "GatewayIntegrationSuite",
    "RefundSuite",
    "FraudSuite",
    "LedgerSuite",
    "SettlementSuite",
]


def _meta(**over) -> dict:
    meta = {
        "schema_version": 3,
        "scope": {
            "projects": [{"id": "p1", "name": "Payment Service"}],
            "releases": [
                {"id": "r1", "name": "2026.09", "status": "active"},
                {"id": "r2", "name": "2026.08", "status": "archived"},
            ],
            "suites": SUITES,
            "window": {"from": "2026-08-20", "to": "2026-09-19", "days": 30, "timezone": "UTC"},
        },
        "totals": {"matched_runs": 18, "total_runs": 143, "matched_executions": 412, "total_executions": 3960},
        "pass_rate_basis": "unique_tests",
        "ignored_filters": [],
        "generated_at": "2026-09-19T17:57:27.878055+00:00",
    }
    meta.update(over)
    return meta


def _payload(meta: dict | None, *, suites: int = 0) -> dict:
    payload = {
        "project_id": "p1",
        "project_name": "Payment Service",
        "mode": "window",
        "window_days": 30,
        "generated_at": "2026-09-19T17:57:27+00:00",
        "totals": {"total_test_cases": 129, "passed": 91, "failed": 28, "skipped": 9, "broken": 1,
                   "evaluated": 120, "pass_rate_pct": 70.5, "pass_rate_basis": "unique_tests",
                   "pass_rate_basis_label": "per unique test", "fail_rate_pct": 21.7,
                   "skip_rate_pct": 7.0, "broken_rate_pct": 0.8, "weighted_pass_rate_pct": 75.8},
        "run_count": 213,
        "runs_per_day": 7.1,
        "avg_duration_ms": 65023,
        "latest_run_at": "2026-09-19T17:30:07+00:00",
        "flaky_test_count": 4,
        "flaky_rate_pct": 3.1,
        # Enough suite rows to push the report onto several pages.
        "suites": [
            {"suite_name": f"suite_{i:03d}", "total": 10, "passed": 8, "failed": 2, "skipped": 0,
             "broken": 0, "pass_rate_pct": 80.0, "last_run_at": "2026-09-19T10:00:00+00:00"}
            for i in range(suites)
        ],
        "top_failing_tests": [],
    }
    if meta is not None:
        payload["meta"] = meta
    return payload


def _pages(pdf: bytes) -> list[str]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    return [re.sub(r"\s+", " ", page.extract_text() or "") for page in reader.pages]


class TestTheSummaryPdfSaysWhatItShows:
    def test_page_one_carries_every_context_field(self):
        first = _pages(render_summary_report_pdf(_payload(_meta())))[0]
        for label in ("Project", "Release", "Test Suite", "Window", "Aggregation",
                      "Pass-rate basis", "Generated (UTC)"):
            assert label in first, f"{label} is missing from the PDF's context block"
        assert "Payment Service" in first
        assert "2026.09" in first and "2026.08 (archived)" in first
        assert "Last 30 days (2026-08-20 to 2026-09-19 UTC)" in first
        assert "All runs in window" in first
        assert "Pass rate per unique test" in first
        assert "2026-09-19 17:57 UTC" in first
        assert "Showing 18 of 143 runs · 412 of 3,960 executions" in first

    def test_a_long_suite_list_is_listed_in_full_never_cut(self):
        first = _pages(render_summary_report_pdf(_payload(_meta())))[0]
        for suite in SUITES:
            assert suite in first, f"{suite} was dropped from the context block"

    def test_every_page_repeats_project_release_and_suite_in_the_footer(self):
        pages = _pages(render_summary_report_pdf(_payload(_meta(), suites=120)))
        assert len(pages) >= 3, "the fixture must span several pages for this to mean anything"
        for number, text in enumerate(pages, start=1):
            assert "Project: Payment Service" in text, f"page {number} has no project footer"
            assert "Release: 2026.09, 2026.08 (archived)" in text, f"page {number} has no release footer"
            # The footer names as many suites as fit and COUNTS the rest.
            assert re.search(r"Suite: (PaymentSuite(, CheckoutSuite)*)? ?\+?\d+ (more )?\(listed on page 1\)", text), (
                f"page {number}: the footer must say how many suites it did not name"
            )
            assert f"Page {number}" in text

    def test_an_unfiltered_report_says_all(self):
        meta = _meta(
            scope={**_meta()["scope"], "releases": [], "suites": []},
            totals={"matched_runs": 143, "total_runs": 143, "matched_executions": 3960, "total_executions": 3960},
        )
        first = _pages(render_summary_report_pdf(_payload(meta)))[0]
        assert "All releases" in first and "All suites" in first
        assert "Showing all 143 runs · 3,960 executions" in first

    def test_a_payload_without_meta_keeps_the_old_header(self):
        # An older caller (or a cached payload) has no envelope: the export must
        # still render, with the one-line header it always had.
        first = _pages(render_summary_report_pdf(_payload(None)))[0]
        assert "Project: Payment Service" in first
        assert "Aggregation: All runs in window" in first

    def test_names_are_text_not_markup(self):
        meta = _meta(scope={**_meta()["scope"], "suites": ["<b>bold</b> & co"]})
        pdf = render_summary_report_pdf(_payload(meta))
        assert "<b>bold</b> & co" in _pages(pdf)[0]


class TestTheContextModel:
    def test_footer_counts_what_it_does_not_name(self):
        assert footer_text(_meta()).endswith(
            "Suite: PaymentSuite, CheckoutSuite, GatewayIntegrationSuite +4 more (listed on page 1)"
        )
        # Shorter on request: fewer names, the rest still counted.
        assert footer_text(_meta(), 1).endswith("Suite: PaymentSuite +6 more (listed on page 1)")
        assert footer_text(_meta(), 0).endswith("Suite: 7 (listed on page 1)")
        assert " | " in footer_text(_meta())

    def test_an_ignored_filter_is_stated_with_its_reason(self):
        fields = context_fields(_meta(ignored_filters=[{"dimension": "release", "reason": "Defects are per project."}]))
        assert ("Not filtered by", "release: Defects are per project.") in [(f.label, f.value) for f in fields]

    def test_unattributed_runs_are_named(self):
        meta = _meta(scope={**_meta()["scope"], "releases": [{"id": "unattributed", "name": "", "status": ""}]})
        assert dict((f.label, f.value) for f in context_fields(meta))["Release"] == "Unattributed runs"

    def test_one_run_is_singular(self):
        meta = _meta(totals={"matched_runs": 1, "total_runs": 1, "matched_executions": 1, "total_executions": 1})
        assert n_of_m_text(meta) == "Showing all 1 run · 1 execution"


class TestTheEmailedReportCarriesTheContext:
    def test_the_context_block_is_in_the_email_and_escaped(self):
        meta = _meta(scope={**_meta()["scope"], "suites": ["<script>x</script>"]})
        html = report_service.build_html_report(
            project_name="<i>Pay</i>", days=30, chart_ids=[], trend_data=[], meta=meta,
        )
        assert "Report context" in html
        assert "Test Suite" in html and "&lt;script&gt;x&lt;/script&gt;" in html
        assert "<script>" not in html
        assert "&lt;i&gt;Pay&lt;/i&gt;" in html and "<i>Pay</i>" not in html
        assert "Showing 18 of 143 runs" in html

    def test_without_meta_the_email_is_unchanged(self):
        html = report_service.build_html_report(project_name="Checkout", days=7, chart_ids=[], trend_data=[])
        assert "Report context" not in html
