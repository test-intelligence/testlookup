"""PDF renderer for the project Summary Report.

Mirrors the style choices in ``services/report_pdf_renderer`` (brand
blue, A4, ReportLab) so both reports look like they came from the same
product. Takes the JSON payload produced by
``services.summary_report_service.build_summary_report`` and emits PDF
bytes.

The function is synchronous + CPU-bound by design — call it via
``fastapi.concurrency.run_in_threadpool`` from request handlers so the
event loop stays responsive.
"""
from __future__ import annotations

import html
import io
from typing import Any

from app.services.privacy_service import sanitize_for_report

_BRAND_BLUE = "#1E40AF"
_HEADER_BG = "#1E3A5F"
_GOOD = "#059669"
_BAD = "#DC2626"
_WARN = "#D97706"
_LIGHT_BG = "#F8FAFC"
_BORDER = "#CBD5E1"


def _safe(value: Any, max_len: int = 300) -> str:
    s = html.escape(sanitize_for_report(str(value or "")))
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def _fmt_int(value: int | None) -> str:
    return f"{int(value or 0):,}"


def render_summary_report_pdf(payload: dict) -> bytes:
    """Render the Summary Report JSON envelope into PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        textColor=colors.HexColor(_BRAND_BLUE),
        fontSize=18,
        spaceAfter=4,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        textColor=colors.HexColor(_BRAND_BLUE),
        fontSize=12,
        spaceAfter=4,
        spaceBefore=10,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=9, leading=12, spaceAfter=4
    )
    small = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.grey,
    )
    # Wrapping cell style — used for every column that can hold a long
    # name (test_name, suite_name, class_name). Without ``Paragraph`` the
    # Table widget clips overflow horizontally; a long test name like
    # ``test_payment_processing_with_invalid_card_number_and_expired_token``
    # disappears off the right edge of the cell. The Paragraph wrapper
    # lets ReportLab break the line on word boundaries inside the column.
    cell_wrap = ParagraphStyle(
        "CellWrap",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        wordWrap="CJK",
        # ``CJK`` wraps on any character (not just whitespace), so a long
        # snake_case test name without spaces still wraps. ``LTR`` would
        # leave such a name as a single overflowing token.
    )

    story: list = []

    totals = payload.get("totals", {})
    project_name = payload.get("project_name") or "—"
    mode = payload.get("mode", "window")
    days = payload.get("window_days", 0)
    window_label = "24h" if days == 1 else f"{days}d"
    mode_label = (
        "All runs in window" if mode == "window" else "Latest run per suite"
    )

    # ── Header ───────────────────────────────────────────────────────────
    story.append(Paragraph("TestLookup — Summary Report", title_style))
    meta = (
        f"Project: <b>{_safe(project_name)}</b> &nbsp; | &nbsp; "
        f"Window: <b>{window_label}</b> &nbsp; | &nbsp; "
        f"Aggregation: <b>{_safe(mode_label)}</b>"
    )
    story.append(Paragraph(meta, body))
    story.append(
        Paragraph(
            f"Generated {_safe(payload.get('generated_at') or '')}", small
        )
    )
    story.append(Spacer(1, 4 * mm))

    # ── Headline KPIs ────────────────────────────────────────────────────
    story.append(Paragraph("Headline", h2))
    kpi_rows = [
        [
            "Total tests",
            "Pass %",
            "Fail %",
            "Skip %",
            "Broken %",
            "Weighted pass %",
            "Flaky",
        ],
        [
            _fmt_int(totals.get("total_test_cases")),
            _fmt_pct(totals.get("pass_rate_pct")),
            _fmt_pct(totals.get("fail_rate_pct")),
            _fmt_pct(totals.get("skip_rate_pct")),
            _fmt_pct(totals.get("broken_rate_pct")),
            _fmt_pct(totals.get("weighted_pass_rate_pct")),
            _fmt_int(payload.get("flaky_test_count")),
        ],
    ]
    kpi_tbl = Table(kpi_rows, colWidths=[60, 50, 50, 50, 50, 70, 40])
    kpi_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor(_LIGHT_BG)),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(kpi_tbl)

    # ── Pass/fail/skip/broken counts ────────────────────────────────────
    story.append(Spacer(1, 3 * mm))
    counts_rows = [
        ["Passed", "Failed", "Skipped", "Broken", "Evaluated"],
        [
            _fmt_int(totals.get("passed")),
            _fmt_int(totals.get("failed")),
            _fmt_int(totals.get("skipped")),
            _fmt_int(totals.get("broken")),
            _fmt_int(totals.get("evaluated")),
        ],
    ]
    counts_tbl = Table(counts_rows, colWidths=[60, 60, 60, 60, 70])
    counts_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_LIGHT_BG)),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                ("TEXTCOLOR", (0, 1), (0, 1), colors.HexColor(_GOOD)),
                ("TEXTCOLOR", (1, 1), (1, 1), colors.HexColor(_BAD)),
                ("TEXTCOLOR", (2, 1), (2, 1), colors.HexColor(_WARN)),
                ("TEXTCOLOR", (3, 1), (3, 1), colors.HexColor(_BAD)),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(counts_tbl)

    # ── Run velocity / age ──────────────────────────────────────────────
    story.append(Spacer(1, 3 * mm))
    velocity_text = (
        f"Runs in window: <b>{_fmt_int(payload.get('run_count'))}</b>"
    )
    if payload.get("runs_per_day") is not None:
        velocity_text += (
            f" &nbsp;|&nbsp; Avg / day: <b>{float(payload['runs_per_day']):.2f}</b>"
        )
    velocity_text += (
        f" &nbsp;|&nbsp; Avg duration: <b>{int(payload.get('avg_duration_ms') or 0):,} ms</b>"
    )
    if payload.get("latest_run_at"):
        velocity_text += f" &nbsp;|&nbsp; Latest run: {_safe(payload['latest_run_at'])}"
    story.append(Paragraph(velocity_text, body))

    # ── Per-suite breakdown ─────────────────────────────────────────────
    story.append(Paragraph("Per-suite breakdown", h2))
    suites = payload.get("suites") or []
    if not suites:
        story.append(
            Paragraph("No suites with executions in this window.", small)
        )
    else:
        rows = [["Suite", "Total", "Pass", "Fail", "Skip", "Broken", "Pass %"]]
        for s in suites:
            rows.append(
                [
                    # Wrap the suite name so long ones flow to a second
                    # line instead of overflowing the column. Numeric
                    # cells stay plain strings — they're short and benefit
                    # from the column's right/center alignment.
                    Paragraph(_safe(s.get("suite_name"), max_len=200), cell_wrap),
                    _fmt_int(s.get("total")),
                    _fmt_int(s.get("passed")),
                    _fmt_int(s.get("failed")),
                    _fmt_int(s.get("skipped")),
                    _fmt_int(s.get("broken")),
                    _fmt_pct(s.get("pass_rate_pct")),
                ]
            )
        suite_tbl = Table(rows, colWidths=[170, 40, 40, 40, 40, 50, 50])
        suite_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    # Multi-line cells look misaligned when the numeric
                    # column anchors to bottom and the wrapped name fills
                    # two rows; pin everything to TOP so the relationship
                    # ``name → counts`` reads from the top of the row.
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_LIGHT_BG)]),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(suite_tbl)

    # ── Top failing tests ───────────────────────────────────────────────
    story.append(Paragraph("Top failing tests", h2))
    top = payload.get("top_failing_tests") or []
    if not top:
        story.append(Paragraph("No failures in this window.", small))
    else:
        rows = [["Test", "Suite", "Class", "Failures"]]
        for t in top:
            # Wrap all three text columns so long test/class names flow
            # to multiple lines within the column rather than clipping.
            # ``max_len`` is bumped higher (the previous tight cap was a
            # workaround for the no-wrap clipping); the cell will simply
            # use more vertical space when needed.
            rows.append(
                [
                    Paragraph(_safe(t.get("test_name"), max_len=300), cell_wrap),
                    Paragraph(_safe(t.get("suite_name") or "—", max_len=200), cell_wrap),
                    Paragraph(_safe(t.get("class_name") or "—", max_len=200), cell_wrap),
                    _fmt_int(t.get("failures")),
                ]
            )
        # Slightly rebalance widths: give Test more room (where the long
        # names live) and trim Class to compensate.
        top_tbl = Table(rows, colWidths=[200, 90, 90, 50])
        top_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
                    # See per-suite table comment — multi-line rows align
                    # better when every cell anchors to the top.
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_LIGHT_BG)]),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(top_tbl)

    doc.build(story)
    return buf.getvalue()
