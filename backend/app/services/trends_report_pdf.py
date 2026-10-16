"""W08 TRND-01: the Trends report as a PDF, from ``trends_report_service.build_trends_report``.

The Summary report's look (ReportLab, A4, its colours and styles,
``summary_report_pdf``): the title, the context block (every release and suite
listed, wrapping), the KPI strip, the trend verdict and the run-gap sentence,
two drawings (pass rate per day -- a day without runs is a GAP, never 0 % --
and executions per day), the daily table and the suite table, and a footer on
every page naming the project, release and suite (``report_context.footer_text``).

Every user string (project, release and suite names) goes through ``_safe()``
(sanitise, truncate, then escape); the drawings take plain text only
(``String``), sanitised. Synchronous and CPU-bound: call it through
``run_in_threadpool``.
"""
from __future__ import annotations

import io
from typing import Any, Optional

from app.services import trends_report_service as report
from app.services.privacy_service import sanitize_for_report
from app.services.report_context import FOOTER_INLINE_MAX, context_fields, footer_text, has_context, n_of_m_text
from app.services.summary_report_pdf import _safe

_BRAND_BLUE = "#1E40AF"
_HEADER_BG = "#1E3A5F"
_GOOD = "#059669"
_BAD = "#DC2626"
_WARN = "#D97706"
_LIGHT_BG = "#F8FAFC"
_BORDER = "#CBD5E1"
_CONTEXT_MAX_CHARS = 12_000
#: Category labels on a daily axis: every n-th day, so 90 days stay legible.
_MAX_AXIS_LABELS = 15


def _axis_names(rows: list[dict]) -> list[str]:
    step = max(1, -(-len(rows) // _MAX_AXIS_LABELS))
    return [row["date"][5:] if i % step == 0 else "" for i, row in enumerate(rows)]


def _daily_bars(rows: list[dict], values: list[Optional[float]], *, value_max: Optional[float], colour: str) -> Any:
    """Vertical bars, one per UTC day. A ``None`` value draws NO bar (a gap)."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    drawing = Drawing(500, 150)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 40, 30, 440, 105
    chart.data = [values]
    chart.categoryAxis.categoryNames = _axis_names(rows)
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.angle = 45
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.valueAxis.valueMin = 0
    if value_max is not None:
        chart.valueAxis.valueMax = value_max
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.bars[0].fillColor = colors.HexColor(colour)
    chart.bars[0].strokeColor = None
    drawing.add(chart)
    return drawing


def render_trends_report_pdf(payload: dict) -> bytes:
    """Render the trends report payload into PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="TestLookup — Trends report",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"], textColor=colors.HexColor(_BRAND_BLUE), fontSize=18, spaceAfter=4,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], textColor=colors.HexColor(_BRAND_BLUE), fontSize=12,
        spaceAfter=4, spaceBefore=10,
    )
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12, spaceAfter=4)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.grey)
    cell = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8.5, leading=11, wordWrap="CJK")

    story: list = []
    story.append(Paragraph("TestLookup — Trends report", title_style))

    # ── Context block (VIZ-308): what the server applied, every value listed ──
    raw_meta = payload.get("meta")
    context_meta: Optional[dict] = raw_meta if has_context(raw_meta) else None
    if context_meta is not None:
        rows = [
            [Paragraph(f"<b>{_safe(f.label)}</b>", cell), Paragraph(_safe(f.value, max_len=_CONTEXT_MAX_CHARS), cell)]
            for f in context_fields(context_meta)
        ]
        context_tbl = Table(rows, colWidths=[95, doc.width - 95])
        context_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_LIGHT_BG)),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(context_tbl)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(_safe(n_of_m_text(context_meta)), body))
    else:
        story.append(Paragraph(
            f"Project: <b>{_safe(payload.get('project_name'))}</b> &nbsp; | &nbsp; "
            f"Window: <b>last {int(payload.get('window_days') or 0)} days</b>", body,
        ))
    story.append(Spacer(1, 3 * mm))

    # ── KPIs ──────────────────────────────────────────────────────────────
    story.append(Paragraph("Key figures", h2))
    items = report.kpis(payload)
    kpi_tbl = Table(
        [[_safe(label) for label, _v, _n in items],
         [Paragraph(f"<b>{_safe(value)}</b>", cell) for _l, value, _n in items],
         [Paragraph(_safe(note, max_len=400), cell) for _l, _v, note in items]],
        colWidths=[doc.width / len(items)] * len(items),
    )
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor(_LIGHT_BG)),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"<b>{_safe(report.verdict_text(payload))}</b>", body))
    gap = report.gap_text(payload)
    if gap:
        story.append(Paragraph(_safe(gap), body))

    # ── The two drawings ─────────────────────────────────────────────────
    days = report.window_days(payload)
    if days:
        story.append(Paragraph("Pass rate per day", h2))
        story.append(Paragraph("A day without evaluated executions is a gap, not 0 %.", small))
        story.append(_daily_bars(days, [d["pass_rate"] for d in days], value_max=100, colour=_BRAND_BLUE))
        story.append(Paragraph("Executions per day", h2))
        story.append(_daily_bars(days, [float(d["executions"]) for d in days], value_max=None, colour=_HEADER_BG))

    # ── The daily table ──────────────────────────────────────────────────
    story.append(Paragraph("Daily breakdown", h2))
    daily = [["Date (UTC)", "Passed", "Failed", "Broken", "Skipped", "Pass rate"]] + [
        [d["date"], f"{d['passed']:,}", f"{d['failed']:,}", f"{d['broken']:,}", f"{d['skipped']:,}",
         report.pct_text(d["pass_rate"])]
        for d in days
    ]
    daily_tbl = Table(daily, colWidths=[90, 70, 70, 70, 70, 80], repeatRows=1)
    daily_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(_BORDER)),
        ("TEXTCOLOR", (1, 1), (1, -1), colors.HexColor(_GOOD)),
        ("TEXTCOLOR", (2, 1), (2, -1), colors.HexColor(_BAD)),
        ("TEXTCOLOR", (3, 1), (3, -1), colors.HexColor(_WARN)),
    ]))
    story.append(daily_tbl)

    # ── The suite table ──────────────────────────────────────────────────
    suites = report.suite_rows(payload)
    story.append(Paragraph("Suites", h2))
    if suites:
        story.append(Paragraph("Failed includes broken. At most 50 suites, the most tests first.", small))
        suite_tbl = Table(
            [["Suite", "Passed", "Failed", "Skipped", "Pass rate"]] + [
                [Paragraph(_safe(s["suite_name"], max_len=200), cell), f"{s['passed']:,}", f"{s['failed']:,}",
                 f"{s['skipped']:,}", report.pct_text(s["pass_rate"])]
                for s in suites
            ],
            colWidths=[doc.width - 280, 70, 70, 70, 70], repeatRows=1,
        )
        suite_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(_BORDER)),
        ]))
        story.append(suite_tbl)
    else:
        story.append(Paragraph("No suite executions in this window.", body))

    # Every page repeats Project, Release and Suite (VIZ-308).
    def _footer_for(canvas: Any, room: float) -> Optional[str]:
        if context_meta is None:
            return None
        text = ""
        for inline_max in range(FOOTER_INLINE_MAX, -1, -1):
            text = sanitize_for_report(footer_text(context_meta, inline_max))
            if canvas.stringWidth(text, "Helvetica", 7) <= room:
                return text
        tail = "... (full context on page 1)"
        while text and canvas.stringWidth(text + tail, "Helvetica", 7) > room:
            text = text[:-1]
        return text.rstrip() + tail

    def _draw_footer(canvas: Any, page_doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        page_label = f"Page {page_doc.page}"
        footer = _footer_for(canvas, page_doc.width - canvas.stringWidth(page_label, "Helvetica", 7) - 12)
        if footer:
            canvas.drawString(page_doc.leftMargin, 10 * mm, footer)
        canvas.drawRightString(page_doc.leftMargin + page_doc.width, 10 * mm, page_label)
        canvas.restoreState()

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buf.getvalue()
