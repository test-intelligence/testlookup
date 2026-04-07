"""
PDF Renderer — generates branded PDF reports from ReportData using ReportLab.

Follows the exact patterns from test_management_exports.py.
"""
from __future__ import annotations

import html
import io
import logging
from typing import Any

logger = logging.getLogger("services.report_pdf_renderer")

# Color palette (matches existing brand)
_BRAND_BLUE = "#1E40AF"
_HEADER_BG = "#1E3A5F"
_GO_GREEN = "#059669"
_NO_GO_RED = "#DC2626"
_CONDITIONAL_AMBER = "#D97706"
_LIGHT_BG = "#F8FAFC"
_BORDER = "#CBD5E1"


def _safe(text: Any, max_len: int = 500) -> str:
    """HTML-escape, redact PII, and truncate text for ReportLab Paragraphs."""
    from app.services.privacy_service import sanitize_for_report
    s = html.escape(sanitize_for_report(str(text or "")))
    if len(s) > max_len:
        s = s[:max_len] + "..."
    return s


def render_report_pdf(report: Any) -> bytes:
    """Build a PDF document from composed ReportData. Returns raw PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Heading1"],
                                  textColor=colors.HexColor(_BRAND_BLUE), fontSize=18, spaceAfter=4)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"],
                               textColor=colors.HexColor(_BRAND_BLUE), fontSize=13, spaceAfter=4, spaceBefore=10)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12, spaceAfter=4)
    small_style = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.grey)

    story: list = []

    # ── Header ──────────────────────────────────────────────────────────
    story.append(Paragraph("TestLookup — Release Report", title_style))
    meta_text = (
        f"Build: <b>{_safe(report.build_number)}</b> | "
        f"Branch: <b>{_safe(report.branch)}</b> | "
        f"Layout: <b>{report.layout.title()}</b>"
    )
    story.append(Paragraph(meta_text, body_style))
    story.append(Spacer(1, 4 * mm))

    # ── Test Summary ────────────────────────────────────────────────────
    summary_data = [
        ["Pass Rate", "Total", "Passed", "Failed", "Skipped"],
        [
            f"{report.pass_rate:.1f}%",
            str(report.total_tests),
            str(report.passed_tests),
            str(report.failed_tests),
            str(report.skipped_tests),
        ],
    ]
    tbl = Table(summary_data, colWidths=[80, 60, 60, 60, 60])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor(_LIGHT_BG)),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Release Decision ────────────────────────────────────────────────
    if report.release_recommendation and report.release_recommendation != "N/A":
        rec = report.release_recommendation
        rec_color = _GO_GREEN if rec == "GO" else _NO_GO_RED if rec == "NO_GO" else _CONDITIONAL_AMBER
        story.append(Paragraph("Release Decision", h2_style))
        decision_data = [
            ["Recommendation", "Risk Score", "Composite Risk"],
            [rec, str(report.risk_score), f"{report.composite_risk:.1f}"],
        ]
        dtbl = Table(decision_data, colWidths=[150, 80, 80])
        dtbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TEXTCOLOR", (0, 1), (0, 1), colors.HexColor(rec_color)),
            ("FONTNAME", (0, 1), (0, 1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
        ]))
        story.append(dtbl)
        story.append(Spacer(1, 2 * mm))

        if report.release_reasoning:
            story.append(Paragraph(f"<i>{_safe(report.release_reasoning, 800)}</i>", body_style))

    # ── Executive Summary ───────────────────────────────────────────────
    if report.executive_panel:
        panel = report.executive_panel
        headline = _safe(panel.get("headline", ""), 200)
        signal = panel.get("status_signal", "N/A").replace("_", " ")
        story.append(Paragraph(f"{headline} — <b>{signal}</b>", h2_style))

        # Key takeaways
        for t in panel.get("key_takeaways", []):
            story.append(Paragraph(f"• {_safe(t, 200)}", body_style))

        # Next actions
        actions = panel.get("next_actions", [])
        if actions:
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph("Next Actions", h2_style))
            for i, a in enumerate(actions, 1):
                story.append(Paragraph(f"{i}. {_safe(a, 200)}", body_style))
    else:
        story.append(Paragraph("Executive Summary", h2_style))
        story.append(Paragraph(_safe(report.executive_summary, 2000), body_style))

    # ── Blocking Issues ─────────────────────────────────────────────────
    if report.blocking_issues:
        story.append(Paragraph("Blocking Issues", h2_style))
        for issue in report.blocking_issues:
            story.append(Paragraph(f"• {_safe(issue, 300)}", body_style))

    # ── Conditions for GO ───────────────────────────────────────────────
    if report.conditions_for_go:
        story.append(Paragraph("Conditions for GO", h2_style))
        for cond in report.conditions_for_go:
            story.append(Paragraph(f"→ {_safe(cond, 300)}", body_style))

    # ── Dimension Scores ────────────────────────────────────────────────
    if report.dimension_scores:
        story.append(Paragraph("Risk Dimension Scores", h2_style))
        dim_data = [["Dimension", "Score", "Weight", "Contribution"]]
        for d in report.dimension_scores:
            dim_data.append([
                _safe(d.get("label", d.get("name", ""))),
                f"{d.get('score', 0):.1f}",
                f"{d.get('weight', 0):.2f}",
                f"{d.get('contribution', 0):.2f}",
            ])
        dtbl = Table(dim_data, colWidths=[130, 60, 60, 80])
        dtbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_LIGHT_BG)]),
        ]))
        story.append(dtbl)

    # ── Baseline Diff ───────────────────────────────────────────────────
    if report.baseline_diff and report.baseline_diff.get("pass_rate_delta") is not None:
        story.append(Paragraph("Baseline Comparison", h2_style))
        diff = report.baseline_diff
        delta = diff.get("pass_rate_delta", 0)
        delta_str = f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%"
        story.append(Paragraph(
            f"Pass rate delta: <b>{delta_str}</b> | "
            f"New failures: <b>{diff.get('new_failures_count', 0)}</b> | "
            f"Resolved: <b>{diff.get('resolved_count', 0)}</b> | "
            f"Classification: <b>{diff.get('regression_classification', 'unclassified')}</b>",
            body_style,
        ))

    # ── Category Breakdown ──────────────────────────────────────────────
    if report.category_breakdown:
        story.append(Paragraph("Failure Category Breakdown", h2_style))
        cat_data = [["Category", "Count"]]
        for cat, count in sorted(report.category_breakdown.items(), key=lambda x: -x[1]):
            cat_data.append([_safe(cat), str(count)])
        ctbl = Table(cat_data, colWidths=[200, 60])
        ctbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
        ]))
        story.append(ctbl)

    # ── Action Plan ─────────────────────────────────────────────────────
    if any(report.action_plan.get(k) for k in ("fix_recommendations", "validation_steps")):
        story.append(Paragraph("Action Plan", h2_style))
        for rec in report.action_plan.get("fix_recommendations", []):
            story.append(Paragraph(f"• {_safe(rec, 300)}", body_style))
        for step in report.action_plan.get("validation_steps", []):
            story.append(Paragraph(f"✓ {_safe(step, 300)}", body_style))

    # ── Engineering-only sections ───────────────────────────────────────
    if report.layout == "engineering":
        # Failure clusters
        if report.failure_clusters:
            story.append(Paragraph("Failure Clusters", h2_style))
            cl_data = [["Cluster", "Size", "Criticality", "Representative Error"]]
            for cl in report.failure_clusters[:15]:
                cl_data.append([
                    _safe(cl.get("label", cl.get("cluster_id", "")), 100),
                    str(cl.get("size", 0)),
                    _safe(cl.get("criticality_level", "—")),
                    _safe(cl.get("representative_error", ""), 150),
                ])
            cltbl = Table(cl_data, colWidths=[100, 40, 70, 250])
            cltbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_LIGHT_BG)]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(cltbl)

        # Top analyses
        if report.top_analyses:
            story.append(Paragraph("Top Failure Analyses", h2_style))
            an_data = [["Test", "Category", "Root Cause", "Confidence"]]
            for an in report.top_analyses[:10]:
                an_data.append([
                    _safe(an.get("test_name", an.get("test_case_id", "")), 80),
                    _safe(an.get("failure_category", "")),
                    _safe(an.get("root_cause_summary", ""), 200),
                    f"{an.get('confidence_score', 0)}%",
                ])
            antbl = Table(an_data, colWidths=[80, 70, 250, 50])
            antbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_HEADER_BG)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(_BORDER)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(antbl)

    # ── Provenance footer ───────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    prov = report.provenance
    prov_text = (
        f"Generated by TestLookup | "
        f"Confidence: {prov.get('confidence', 'N/A')}% | "
        f"Sources: {', '.join(prov.get('sources_used', []))} | "
        f"Evidence: {prov.get('evidence_count', 0)} artifacts | "
        f"{report.generated_at}"
    )
    story.append(Paragraph(prov_text, small_style))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
