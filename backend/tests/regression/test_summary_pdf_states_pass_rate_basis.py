"""The exported PDF must say what its pass rate divides by.

JR-05 step 5. The page renders "Pass %" with a subtitle carrying
``pass_rate_basis_label`` -- "per unique test" -- because a pass rate is not
interpretable without knowing its denominator. The PDF dropped it.

Measured against the running stack (project E-Commerce Platform, 30-day window).
Every one of the thirteen published numbers appeared in the PDF, so the export is
numerically faithful::

    total 129  passed 91  failed 28  skipped 9  broken 1  evaluated 120
    pass 70.5%  fail 21.7%  skip 7.0%  broken 0.8%  weighted 75.8%
    runs 213  flaky 74

But the strings "basis", "unique test" and "per test execution" were all absent.
The PDF showed **"Pass % 70.5%"** and **"Weighted pass % 75.8%"** side by side
with nothing explaining why two pass rates differ.

That matters more in the PDF than anywhere else, because the PDF is the
*detached* surface: it is emailed, attached to compliance packs, and read with no
application beside it to supply the missing caption.
``tests/regression/test_pass_rate_basis_is_published.py`` already pins this one
layer down -- a computed field must survive its response_model. It survived the
API and then died at the export.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("reportlab")

from app.services.summary_report_pdf import render_summary_report_pdf  # noqa: E402


def _payload(**overrides) -> dict:
    """A payload shaped like the real 30-day response measured for this journey."""
    totals = {
        "total_test_cases": 129,
        "passed": 91,
        "failed": 28,
        "skipped": 9,
        "broken": 1,
        "evaluated": 120,
        "pass_rate_pct": 70.5,
        "pass_rate_basis": "unique_tests",
        "pass_rate_basis_label": "per unique test",
        "fail_rate_pct": 21.7,
        "skip_rate_pct": 7.0,
        "broken_rate_pct": 0.8,
        "weighted_pass_rate_pct": 75.8,
    }
    totals.update(overrides.pop("totals", {}))
    payload = {
        "project_id": "852a9cf9-ee92-4b34-aa19-3076407b395c",
        "project_name": "E-Commerce Platform",
        "mode": "window",
        "window_days": 30,
        "generated_at": "2026-09-19T17:57:27.878055+00:00",
        "period_start": "2026-08-20T17:57:27+00:00",
        "period_end": "2026-09-19T17:57:27+00:00",
        "totals": totals,
        "run_count": 213,
        "runs_per_day": 7.1,
        "avg_duration_ms": 65023,
        "latest_run_at": "2026-09-19T17:30:07.990316+00:00",
        "flaky_test_count": 74,
        "flaky_rate_pct": 57.4,
        "suites": [],
        "top_failing_tests": [],
    }
    payload.update(overrides)
    return payload


def _text(pdf_bytes: bytes) -> str:
    """Extracted PDF text, whitespace-normalised.

    Normalising is not cosmetic. The generated PDF wraps the basis sentence
    across a line break between "are" and "excluded", so a naive substring check
    on the raw extraction reports a false negative -- which it did on the first
    run of this very assertion, against a PDF that was correct.
    """
    pypdf = pytest.importorskip("pypdf")
    import io

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)
    return re.sub(r"\s+", " ", raw)


class TestTheBasisSurvivesTheExport:
    def test_the_pdf_renders_at_all(self):
        pdf = render_summary_report_pdf(_payload())
        assert pdf.startswith(b"%PDF-"), "not a PDF"
        assert len(pdf) > 1000

    def test_the_pass_rate_basis_is_stated(self):
        text = _text(render_summary_report_pdf(_payload()))
        assert "per unique test" in text, (
            "the export publishes a pass rate without saying what it divides by; "
            "the PDF is read detached from the app that would caption it"
        )

    def test_it_explains_why_two_pass_rates_differ(self):
        # "Pass % 70.5" beside "Weighted pass % 75.8" is unreadable without this.
        text = _text(render_summary_report_pdf(_payload()))
        assert "skipped tests are excluded" in text
        assert "passed + failed + broken" in text

    def test_the_numbers_are_still_all_there(self):
        # The basis line must be additive: a caption that displaced a number
        # would be a worse defect than the one being fixed.
        text = _text(render_summary_report_pdf(_payload()))
        for value in ("129", "91", "28", "9", "120", "70.5%", "75.8%", "213"):
            assert value in text, f"{value} vanished from the export"

    def test_a_different_basis_is_reported_as_itself(self):
        # Not hardcoded to the unique-test wording: the other basis this product
        # defines is per-execution, and the export must say whichever applies.
        text = _text(render_summary_report_pdf(_payload(
            totals={"pass_rate_basis": "executions",
                    "pass_rate_basis_label": "per test execution"},
        )))
        assert "per test execution" in text
        assert "per unique test" not in text

    def test_a_missing_basis_label_does_not_break_the_export(self):
        # Older cached payloads predate the field. Rendering must degrade to the
        # previous behaviour rather than raise -- an export that 500s is worse
        # than one missing a caption.
        pdf = render_summary_report_pdf(_payload(
            totals={"pass_rate_basis_label": ""},
        ))
        assert pdf.startswith(b"%PDF-")
        text = _text(pdf)
        assert "Pass % is measured" not in text
        assert "70.5%" in text
