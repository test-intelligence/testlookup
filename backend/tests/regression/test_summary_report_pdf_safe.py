"""Regression pins for the summary_report_pdf review (review/summary-report-pdf).

Fix: ``_safe`` escaped first and truncated second, so slicing could cut through
an HTML entity (``&lt;`` → ``&``). The malformed token was then handed to
ReportLab's Paragraph parser, 500-ing the PDF export for any payload with a
``<`` / ``>`` / ``&`` near the truncation boundary (e.g. a long test name). It
now truncates the sanitized raw string BEFORE escaping, so every escaped entity
in the output is well-formed; markup injection stays escaped.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.regression

# Matches an ``&`` that does NOT begin a complete entity reference.
_BARE_AMP = re.compile(r"&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)")


def test_safe_truncation_never_splits_an_entity():
    from app.services.summary_report_pdf import _safe

    # ``<`` lands exactly at the truncation boundary; escaping it first would
    # have left a dangling ``&``.
    out = _safe("x" * 18 + "<script>", max_len=20)
    assert out.endswith("…")
    assert "&lt;" in out  # the '<' became a COMPLETE entity
    assert not _BARE_AMP.search(out)


def test_safe_truncation_with_many_ampersands_is_well_formed():
    from app.services.summary_report_pdf import _safe

    out = _safe("&" * 200, max_len=25)
    assert not _BARE_AMP.search(out)  # no half-written &amp;


def test_safe_escapes_markup_to_prevent_paragraph_injection():
    from app.services.summary_report_pdf import _safe

    out = _safe("<b>boom</b> & <i>x</i>")
    assert "<" not in out and ">" not in out
    assert "&lt;b&gt;" in out
    assert not _BARE_AMP.search(out)


def test_safe_handles_none_and_short_values():
    from app.services.summary_report_pdf import _safe

    assert _safe(None) == ""
    assert _safe("plain") == "plain"


def test_fmt_helpers():
    from app.services.summary_report_pdf import _fmt_int, _fmt_pct

    assert _fmt_pct(None) == "—"
    assert _fmt_pct(95.5) == "95.5%"
    assert _fmt_pct(100) == "100.0%"
    assert _fmt_int(None) == "0"
    assert _fmt_int(1234567) == "1,234,567"


def test_render_does_not_raise_on_markup_heavy_payload():
    """End-to-end: a long test name with markup at the boundary must not 500
    the renderer (the actual symptom the _safe fix prevents)."""
    pytest.importorskip("reportlab")
    from app.services.summary_report_pdf import render_summary_report_pdf

    payload = {
        "project_name": "Acme & Co <prod>",
        "mode": "window",
        "window_days": 7,
        "generated_at": "2026-06-03T00:00:00Z",
        "totals": {
            "total_test_cases": 100, "pass_rate_pct": 95.0, "fail_rate_pct": 5.0,
            "skip_rate_pct": 0.0, "broken_rate_pct": 0.0, "weighted_pass_rate_pct": 94.0,
            "passed": 95, "failed": 5, "skipped": 0, "broken": 0, "evaluated": 100,
        },
        "flaky_test_count": 2,
        "run_count": 10,
        "suites": [{"suite_name": "S<x>&" + "y" * 250, "total": 10, "passed": 9,
                    "failed": 1, "skipped": 0, "broken": 0, "pass_rate_pct": 90.0}],
        "top_failing_tests": [{
            "test_name": "t_" + "a" * 295 + "<&>", "suite_name": "s&<x>",
            "class_name": "C<&>", "failures": 3,
        }],
    }
    out = render_summary_report_pdf(payload)
    assert isinstance(out, bytes) and out[:4] == b"%PDF"
