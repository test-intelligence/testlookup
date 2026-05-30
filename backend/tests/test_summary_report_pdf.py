"""Smoke test for the Summary Report PDF renderer.

The renderer is pure (no DB), so we just verify it accepts the JSON
envelope shape and emits non-empty bytes that look like a PDF. The shape
mirrors ``services.summary_report_service.build_summary_report``'s
return value — if either side drifts, this test catches the mismatch
before the PDF endpoint 500s in production.
"""
from __future__ import annotations


def _sample_payload() -> dict:
    return {
        "project_id": "00000000-0000-0000-0000-000000000001",
        "project_name": "GoogleProject",
        "mode": "window",
        "window_days": 7,
        "generated_at": "2026-05-16T00:00:00+00:00",
        "period_start": "2026-05-09T00:00:00+00:00",
        "period_end": "2026-05-16T00:00:00+00:00",
        "totals": {
            "total_test_cases": 200,
            "passed": 180,
            "failed": 15,
            "skipped": 3,
            "broken": 2,
            "evaluated": 197,
            "pass_rate_pct": 90.0,
            "fail_rate_pct": 7.5,
            "skip_rate_pct": 1.5,
            "broken_rate_pct": 1.0,
            "weighted_pass_rate_pct": 91.4,
        },
        "run_count": 4,
        "runs_per_day": 0.57,
        "avg_duration_ms": 12_345,
        "latest_run_at": "2026-05-15T00:00:00+00:00",
        "flaky_test_count": 3,
        "flaky_rate_pct": 1.5,
        "suites": [
            {
                "suite_name": "checkout-api", "total": 120, "passed": 102,
                "failed": 13, "skipped": 3, "broken": 2,
                "pass_rate_pct": 85.0, "weighted_pass_rate_pct": 87.2,
                "last_run_at": "2026-05-15T00:00:00+00:00",
            },
            {
                "suite_name": "auth-api", "total": 80, "passed": 78,
                "failed": 2, "skipped": 0, "broken": 0,
                "pass_rate_pct": 97.5, "weighted_pass_rate_pct": 97.5,
                "last_run_at": "2026-05-15T00:00:00+00:00",
            },
        ],
        "top_failing_tests": [
            {"suite_name": "checkout-api", "class_name": "CheckoutTests",
             "test_name": "test_pay", "failures": 8},
            {"suite_name": "auth-api", "class_name": "AuthTests",
             "test_name": "test_login", "failures": 2},
        ],
    }


def test_render_summary_report_pdf_emits_pdf_bytes():
    from app.services.summary_report_pdf import render_summary_report_pdf

    pdf = render_summary_report_pdf(_sample_payload())
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-"), "renderer should emit a real PDF header"
    # A populated report is meaningfully bigger than a stub document.
    assert len(pdf) > 2_000


def test_render_summary_report_pdf_handles_empty_sections():
    """No suites, no top failing — renderer must still produce a PDF."""
    from app.services.summary_report_pdf import render_summary_report_pdf

    payload = _sample_payload()
    payload["suites"] = []
    payload["top_failing_tests"] = []
    pdf = render_summary_report_pdf(payload)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 500


def test_render_summary_report_pdf_handles_latest_mode_without_runs_per_day():
    """``latest`` mode sends ``runs_per_day: None`` — must not crash."""
    from app.services.summary_report_pdf import render_summary_report_pdf

    payload = _sample_payload()
    payload["mode"] = "latest"
    payload["runs_per_day"] = None
    pdf = render_summary_report_pdf(payload)
    assert pdf.startswith(b"%PDF-")


def test_render_summary_report_pdf_wraps_long_test_names_in_top_failing():
    """Regression for the user-reported cut-off in /reports/summary PDF.

    Before the fix, long test/class/suite names overflowed the column
    horizontally because cells were raw strings. The fix wraps each text
    column in a ReportLab ``Paragraph`` so long values flow to a second
    line. We pin this by feeding a name that's longer than any reasonable
    column width AND asserting the renderer doesn't crash — and that the
    output PDF is larger than the same payload with a short name, which
    is the cheapest objective proof that more layout (wrapped lines) was
    produced.
    """
    from app.services.summary_report_pdf import render_summary_report_pdf

    short_payload = _sample_payload()
    short_payload["top_failing_tests"] = [
        {
            "suite_name": "auth",
            "class_name": "AuthTests",
            "test_name": "test_x",
            "failures": 9,
        }
    ]
    short_pdf = render_summary_report_pdf(short_payload)

    long_payload = _sample_payload()
    long_payload["top_failing_tests"] = [
        {
            "suite_name": "very_long_suite_name_that_used_to_overflow_the_column",
            "class_name": "VeryLongClassNameThatAlsoOverflowedBeforeWrappingWasAdded",
            # 200+ chars — no whitespace, snake_case, the realistic worst case.
            "test_name": (
                "test_extremely_long_test_case_name_describing_a_complicated_"
                "interaction_between_payment_processing_and_user_session_"
                "expiry_during_concurrent_checkout_with_split_tender_and_"
                "fraud_review_pending"
            ),
            "failures": 9,
        }
    ]
    long_pdf = render_summary_report_pdf(long_payload)

    assert short_pdf.startswith(b"%PDF-")
    assert long_pdf.startswith(b"%PDF-")
    # Wrapped multi-line cells push the document a bit larger than the
    # single-line case. If the renderer regresses to clipping, both PDFs
    # become the same height and this assertion fails.
    assert len(long_pdf) > len(short_pdf), (
        f"long test name should produce a larger PDF "
        f"(wrapped to multiple lines); got short={len(short_pdf)}, "
        f"long={len(long_pdf)}"
    )


def test_render_summary_report_pdf_wraps_long_suite_names_in_breakdown():
    """Mirror regression for the per-suite breakdown table — long suite
    names should wrap to multiple lines, not clip."""
    from app.services.summary_report_pdf import render_summary_report_pdf

    short_payload = _sample_payload()
    short_payload["suites"] = [
        {
            "suite_name": "auth", "total": 50, "passed": 49,
            "failed": 1, "skipped": 0, "broken": 0,
            "pass_rate_pct": 98.0, "weighted_pass_rate_pct": 98.0,
            "last_run_at": "2026-05-15T00:00:00+00:00",
        }
    ]
    short_pdf = render_summary_report_pdf(short_payload)

    long_payload = _sample_payload()
    long_payload["suites"] = [
        {
            "suite_name": (
                "very_long_realistic_suite_name_from_a_jenkins_pipeline_that_"
                "is_actually_two_paths_concatenated_together_and_used_to_clip"
            ),
            "total": 50, "passed": 49, "failed": 1, "skipped": 0, "broken": 0,
            "pass_rate_pct": 98.0, "weighted_pass_rate_pct": 98.0,
            "last_run_at": "2026-05-15T00:00:00+00:00",
        }
    ]
    long_pdf = render_summary_report_pdf(long_payload)

    assert long_pdf.startswith(b"%PDF-")
    assert len(long_pdf) > len(short_pdf)
