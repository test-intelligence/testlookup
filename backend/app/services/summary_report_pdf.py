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
    # Truncate the sanitized text BEFORE escaping. Escaping first and then
    # slicing can cut through an HTML entity (e.g. ``&lt;`` → ``&``), feeding a
    # malformed token to ReportLab's Paragraph parser and 500-ing the PDF for
    # any value with ``<``/``>``/``&`` near the boundary. Truncating the raw
    # string keeps every escaped entity in the output well-formed.
    s = sanitize_for_report(str(value or ""))
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return html.escape(s)


def _fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def _fmt_int(value: int | None) -> str:
    return f"{int(value or 0):,}"


# Cap the per-test step rows rendered into the engineering PDF section. A
# single test can carry up to ``ingestion._MAX_STEP_NODES`` (2000) steps, and
# the outer loop renders up to 10 top-failing tests — an unbounded render would
# build ~20K ReportLab rows into one in-memory flowable. The failure-location
# goal only needs the failing step + surrounding context, so we render a window
# of at most this many steps centred on the first failed/broken step.
_MAX_PDF_STEPS = 50


def _select_pdf_steps(
    steps: list[dict],
) -> list[tuple[int, dict]]:
    """Pick a bounded window of steps to render, preserving original indices.

    Returns ``[(orig_index, step), ...]`` (``orig_index`` is 0-based into
    ``steps``). If the test has ``<= _MAX_PDF_STEPS`` steps, all are returned.
    Otherwise a window of ``_MAX_PDF_STEPS`` steps is centred on the first
    FAILED/BROKEN step (the failure location), so the failing step and its
    neighbouring context are always shown; when no failed step is found the
    head of the list is used.
    """
    n = len(steps)
    if n <= _MAX_PDF_STEPS:
        return list(enumerate(steps))

    pivot = next(
        (
            i
            for i, s in enumerate(steps)
            if str(s.get("status") or "").upper() in ("FAILED", "BROKEN")
        ),
        0,
    )
    half = _MAX_PDF_STEPS // 2
    start = max(0, pivot - half)
    end = start + _MAX_PDF_STEPS
    if end > n:
        end = n
        start = max(0, end - _MAX_PDF_STEPS)
    return [(i, steps[i]) for i in range(start, end)]


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

    # The basis is part of the number, not decoration.
    #
    # The page renders "Pass %" with a subtitle carrying
    # ``pass_rate_basis_label`` ("per unique test"), because a pass rate is not
    # interpretable without knowing what it divides. The PDF dropped it, and the
    # PDF is the DETACHED surface -- emailed, attached to compliance packs, read
    # with no app beside it. It showed "Pass % 70.5%" and "Weighted pass % 75.8%"
    # side by side with nothing saying why two pass rates differ.
    #
    # `tests/regression/test_pass_rate_basis_is_published.py` exists for the same
    # reason one layer down: a computed field must survive its response_model.
    # It survives the API and then died at the export.
    basis_label = (totals.get("pass_rate_basis_label") or "").strip()
    if basis_label:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                f"Pass % is measured {basis_label}. "
                "Weighted pass % divides by executed tests only "
                "(passed + failed + broken), so skipped tests are excluded.",
                small,
            )
        )

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

    # ── Engineering: failure step breakdown ─────────────────────────────
    # Phase 5: for failing tests that have a captured granular step snapshot
    # (LATEST-RUN-ONLY), show where the test broke — the ordered step list
    # with status, and the first failed/broken step's assertion message
    # (truncated). Skips gracefully when no test in the report has steps.
    step_style = ParagraphStyle(
        "StepCellWrap",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        wordWrap="CJK",
    )
    tests_with_steps = [
        t for t in top if t.get("step_breakdown")
    ]
    if tests_with_steps:
        story.append(Paragraph("Engineering — failure step breakdown", h2))
        story.append(
            Paragraph(
                "Where each failing test broke. The first failed/broken step "
                "is the failure location; status legend: PASSED / FAILED / "
                "BROKEN / SKIPPED.",
                small,
            )
        )
        for t in tests_with_steps:
            story.append(Spacer(1, 2 * mm))
            heading = _safe(t.get("test_name"), max_len=200)
            failure_step = t.get("failure_step")
            if failure_step:
                heading += (
                    f" &nbsp;—&nbsp; failed at: <b>{_safe(failure_step, 160)}</b>"
                )
            story.append(Paragraph(heading, body))

            # Bound the rendered steps per test. ``step_breakdown`` carries the
            # FULL snapshot (up to ``_MAX_STEP_NODES`` = 2000 steps per test from
            # ingestion), so an unbounded render could build ~20K ReportLab rows
            # (10 tests × 2000) into one in-memory flowable. The failure-location
            # goal only needs the failing step + nearby context, so we render a
            # window centred on the first failed/broken step (or the head when
            # none is found), capped at ``_MAX_PDF_STEPS`` rows, with a footer
            # noting how many steps were elided. ``orig_idx`` preserves the real
            # step number so the "#" column stays meaningful.
            all_steps = list(t.get("step_breakdown") or [])
            rendered = _select_pdf_steps(all_steps)
            step_rows = [["#", "Step", "Status", "Message"]]
            for orig_idx, s in rendered:
                status = str(s.get("status") or "").upper()
                # Only the failed/broken steps carry a useful message; keep
                # the cell tidy for passing steps.
                msg = (
                    s.get("assertion_message")
                    if status in ("FAILED", "BROKEN")
                    else ""
                )
                step_rows.append(
                    [
                        str(orig_idx + 1),
                        Paragraph(_safe(s.get("name"), max_len=200), step_style),
                        status or "—",
                        Paragraph(_safe(msg or "", max_len=240), step_style),
                    ]
                )
            elided = len(all_steps) - len(rendered)
            if elided > 0:
                step_rows.append(
                    [
                        "…",
                        Paragraph(
                            f"… {elided} more step{'s' if elided != 1 else ''} "
                            "not shown (truncated)",
                            step_style,
                        ),
                        "—",
                        Paragraph("", step_style),
                    ]
                )
            step_tbl = Table(step_rows, colWidths=[22, 175, 55, 178])
            step_styles = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_LIGHT_BG)]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
            # Colour the status cell red for failed/broken rows so the
            # failure location pops in the engineering view. Iterate the
            # RENDERED subset (row index aligns to the table, not the full
            # snapshot) so the colour lands on the right row after truncation.
            for r_idx, (_orig, s) in enumerate(rendered, start=1):
                st = str(s.get("status") or "").upper()
                if st in ("FAILED", "BROKEN"):
                    step_styles.append(
                        ("TEXTCOLOR", (2, r_idx), (2, r_idx), colors.HexColor(_BAD))
                    )
                elif st == "PASSED":
                    step_styles.append(
                        ("TEXTCOLOR", (2, r_idx), (2, r_idx), colors.HexColor(_GOOD))
                    )
            step_tbl.setStyle(TableStyle(step_styles))
            story.append(step_tbl)

    doc.build(story)
    return buf.getvalue()
