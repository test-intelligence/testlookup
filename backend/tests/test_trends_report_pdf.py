"""W08 TRND-01: the Trends PDF says what the page shows, read back out of the file.

The text is extracted with pypdf (what a reader of the file sees, the
``test_exports_carry_report_context.py`` method): the context block names the
window, the suite and the release; the KPIs carry the evaluated pass rate the
page prints; a day without evaluated executions reads ``—`` (never 0 %); the
verdict and the gap sentence are there; a hostile suite name is TEXT; every
page's footer names project, release and suite. The route (VIEWER 200, the
422, the 403, the audit row) runs over real Postgres in
``tests/integration/test_trends_report_postgres.py``.
"""
from __future__ import annotations

import io
import re
from datetime import date, timedelta

import pytest

from app.services.trend_summary_service import summarize
from app.services.trends_report_pdf import render_trends_report_pdf

HOSTILE = "<img src=x onerror=alert(1)>"
TODAY = date(2026, 9, 18)


def _row(days_ago: int, passed: int, failed: int = 0, skipped: int = 0, broken: int = 0) -> dict:
    return {"date": (TODAY - timedelta(days=days_ago)).isoformat(), "passed": passed, "failed": failed,
            "skipped": skipped, "broken": broken, "total": passed + failed + skipped + broken, "pass_rate": 0.0}


def _meta() -> dict:
    return {
        "schema_version": 3,
        "scope": {
            "projects": [{"id": "p1", "name": "Payment Service"}],
            "releases": [{"id": "r1", "name": "2.4", "status": "active"}],
            "suites": ["payments", HOSTILE],
            "window": {"from": "2026-09-05", "to": "2026-09-18", "days": 14, "timezone": "UTC"},
        },
        "totals": {"matched_runs": 9, "total_runs": 30, "matched_executions": 120, "total_executions": 400},
        "pass_rate_basis": "executions",
        "ignored_filters": [],
        "generated_at": "2026-09-18T12:00:00+00:00",
    }


def _payload(meta=None, days: int = 14, trend=None) -> dict:
    trend = trend if trend is not None else [
        _row(13, 10), _row(12, 9, 1), _row(11, 0, skipped=4),  # a day with nothing evaluated
        _row(5, 8, 2, broken=1), _row(4, 10), _row(3, 6, 4), _row(2, 10), _row(1, 7, 3), _row(0, 10),
    ]
    suites = [
        {"suite_name": "payments", "passed": 50, "failed": 11, "skipped": 4, "pass_rate": 82.0},
        {"suite_name": HOSTILE, "passed": 20, "failed": 0, "skipped": 0, "pass_rate": None},
    ]
    return {
        "project_name": "Payment Service",
        "window_days": days,
        "generated_at": "2026-09-18T12:00:00+00:00",
        "trend": trend,
        "suites": suites,
        "summary": summarize(trend, suites, days=days, today=TODAY),
        "pass_rate_delta_pp": -2.3,
        "meta": meta,
    }


def _pages(pdf: bytes) -> list[str]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    return [re.sub(r"\s+", " ", page.extract_text() or "") for page in reader.pages]


def test_the_pdf_carries_the_context_the_kpis_and_the_verdict():
    payload = _payload(_meta())
    pdf = render_trends_report_pdf(payload)
    assert pdf.startswith(b"%PDF")
    text = " ".join(_pages(pdf))
    assert "TestLookup — Trends report" in text
    for expected in ("Payment Service", "2.4", "payments", "Last 14 days (2026-09-05 to 2026-09-18 UTC)",
                     "Pass rate of executions", "Showing 9 of 30 runs"):
        assert expected in text, expected
    summary = payload["summary"]
    # The evaluated basis, as the page's KPI: 70 passed of 81 evaluated (the 4 skips are out).
    assert summary["pass_rate"] == 86.4
    assert "Pass rate" in text and "86.4%" in text and "pass rate per test execution" in text
    assert "-2.3 pp vs previous 14 days" in text
    assert f"{summary['days_with_runs']} / 14" in text
    assert "Trend confidence" in text and "/ 100" in text
    assert "No executions from 2026-09-08 to 2026-09-12" in text
    assert "Pass rate per day" in text and "Executions per day" in text


def test_a_day_without_evaluated_executions_is_a_dash_never_zero():
    text = " ".join(_pages(render_trends_report_pdf(_payload(_meta()))))
    # 2026-09-07: four skips only; 2026-09-10: no run at all.
    assert re.search(r"2026-09-07 0 0 0 4 —", text), text
    assert re.search(r"2026-09-10 0 0 0 0 —", text), text
    assert "2026-09-07 0 0 0 4 0.0%" not in text


def test_a_hostile_suite_name_is_text_in_the_context_block_and_the_suite_table():
    text = " ".join(_pages(render_trends_report_pdf(_payload(_meta()))))
    assert text.count(HOSTILE) >= 2, "listed as text in the context block and the suite table"


def test_every_page_footer_names_the_scope():
    pages = _pages(render_trends_report_pdf(_payload(_meta(), days=90)))
    assert len(pages) >= 2, "90 daily rows run past one page"
    for page in pages:
        assert "Project: Payment Service" in page and "Release: 2.4" in page and "Suite: payments" in page


def test_without_meta_the_header_still_names_the_project_and_window():
    text = " ".join(_pages(render_trends_report_pdf(_payload(None))))
    assert "Project: Payment Service" in text and "Window: last 14 days" in text


def test_an_empty_window_renders_pending_with_no_rate():
    payload = _payload(_meta(), trend=[])
    text = " ".join(_pages(render_trends_report_pdf(payload)))
    assert "Trend confidence — / 100 · Pending" in text
    assert "Pass rate" in text and "0.0%" not in text
