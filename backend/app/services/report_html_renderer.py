"""
HTML Renderer — generates standalone HTML report pages from ReportData.

Used for time-limited share link views. All CSS is inline (no external deps).
"""
from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger("services.report_html_renderer")

_REC_COLORS = {
    "GO": "#059669",
    "NO_GO": "#DC2626",
    "CONDITIONAL_GO": "#D97706",
}


def _e(text: Any, max_len: int = 500) -> str:
    """HTML-escape, redact PII, and truncate."""
    from app.services.privacy_service import sanitize_for_report
    s = html.escape(sanitize_for_report(str(text or "")))
    return s[:max_len] + "..." if len(s) > max_len else s


def render_report_html(report: Any, shared_by: str = "", expires_at: str = "") -> str:
    """Build a standalone HTML page from composed ReportData."""
    rec = report.release_recommendation or "N/A"
    rec_color = _REC_COLORS.get(rec, "#6B7280")

    sections: list[str] = []

    # Share notice
    if shared_by or expires_at:
        sections.append(f"""
        <div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:8px;padding:12px;margin-bottom:20px;font-size:13px;color:#92400E">
            Shared by <b>{_e(shared_by)}</b>{f' · Expires: {_e(expires_at)}' if expires_at else ''}
        </div>""")

    # Test summary
    sections.append(f"""
    <div style="display:flex;gap:16px;margin-bottom:20px">
        <div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:8px;padding:16px;flex:1;text-align:center">
            <div style="font-size:28px;font-weight:bold;color:#059669">{report.pass_rate:.1f}%</div>
            <div style="font-size:12px;color:#6B7280">Pass Rate</div>
        </div>
        <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:16px;flex:1;text-align:center">
            <div style="font-size:20px;font-weight:bold">{report.total_tests}</div>
            <div style="font-size:12px;color:#6B7280">Total | {report.passed_tests} passed | {report.failed_tests} failed | {report.skipped_tests} skipped</div>
        </div>
    </div>""")

    # Release decision
    if rec != "N/A":
        sections.append(f"""
        <div style="background:{rec_color}15;border:2px solid {rec_color};border-radius:12px;padding:20px;margin-bottom:20px;display:flex;align-items:center;gap:20px">
            <div style="font-size:36px;font-weight:900;color:{rec_color}">{rec.replace('_', ' ')}</div>
            <div style="flex:1">
                <div style="font-size:14px;color:#6B7280">Risk Score: <b>{report.risk_score}</b>/100</div>
                {f'<div style="font-size:13px;color:#374151;margin-top:4px"><i>{_e(report.release_reasoning, 500)}</i></div>' if report.release_reasoning else ''}
            </div>
        </div>""")

    # Executive summary — structured panel or plain-text fallback
    if report.executive_panel:
        sections.append(_render_executive_panel_html(report.executive_panel))
    else:
        sections.append(f"""
    <h2 style="color:#1E40AF;border-bottom:2px solid #DBEAFE;padding-bottom:4px">Executive Summary</h2>
    <p style="font-size:14px;line-height:1.6;color:#374151">{_e(report.executive_summary, 2000)}</p>""")

    # Blocking issues
    if report.blocking_issues:
        items = "".join(f'<li style="color:#DC2626;margin-bottom:4px">{_e(i, 300)}</li>' for i in report.blocking_issues)
        sections.append(f'<h2 style="color:#1E40AF">Blocking Issues</h2><ul>{items}</ul>')

    # Dimension scores table
    if report.dimension_scores:
        rows = "".join(
            f'<tr><td style="padding:6px 12px">{_e(d.get("label", d.get("name", "")))}</td>'
            f'<td style="padding:6px 12px;text-align:center">{d.get("score", 0):.1f}</td>'
            f'<td style="padding:6px 12px;text-align:center">{d.get("weight", 0):.2f}</td>'
            f'<td style="padding:6px 12px;text-align:center">{d.get("contribution", 0):.2f}</td></tr>'
            for d in report.dimension_scores
        )
        sections.append(f"""
        <h2 style="color:#1E40AF">Risk Dimensions</h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="background:#1E3A5F;color:white">
                <th style="padding:8px 12px;text-align:left">Dimension</th>
                <th style="padding:8px 12px">Score</th>
                <th style="padding:8px 12px">Weight</th>
                <th style="padding:8px 12px">Contribution</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    # Category breakdown
    if report.category_breakdown:
        cat_rows = "".join(
            f'<tr><td style="padding:4px 12px">{_e(cat)}</td><td style="padding:4px 12px;text-align:center">{cnt}</td></tr>'
            for cat, cnt in sorted(report.category_breakdown.items(), key=lambda x: -x[1])
        )
        sections.append(f"""
        <h2 style="color:#1E40AF">Failure Categories</h2>
        <table style="border-collapse:collapse;font-size:13px">
            <thead><tr style="background:#1E3A5F;color:white">
                <th style="padding:6px 12px;text-align:left">Category</th><th style="padding:6px 12px">Count</th>
            </tr></thead>
            <tbody>{cat_rows}</tbody>
        </table>""")

    # Baseline diff
    diff = report.baseline_diff
    if diff and diff.get("pass_rate_delta") is not None:
        delta = diff["pass_rate_delta"]
        delta_str = f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%"
        sections.append(f"""
        <h2 style="color:#1E40AF">Baseline Comparison</h2>
        <p style="font-size:13px">
            Pass rate delta: <b>{delta_str}</b> |
            New failures: <b>{diff.get('new_failures_count', 0)}</b> |
            Resolved: <b>{diff.get('resolved_count', 0)}</b> |
            Classification: <b>{_e(diff.get('regression_classification', 'unclassified'))}</b>
        </p>""")

    # Engineering sections
    if report.layout == "engineering" and report.failure_clusters:
        cl_rows = "".join(
            f'<tr><td style="padding:4px 8px">{_e(c.get("label", ""), 80)}</td>'
            f'<td style="padding:4px 8px;text-align:center">{c.get("size", 0)}</td>'
            f'<td style="padding:4px 8px">{_e(c.get("criticality_level", "—"))}</td>'
            f'<td style="padding:4px 8px;font-size:11px">{_e(c.get("representative_error", ""), 150)}</td></tr>'
            for c in report.failure_clusters[:15]
        )
        sections.append(f"""
        <h2 style="color:#1E40AF">Failure Clusters</h2>
        <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead><tr style="background:#1E3A5F;color:white">
                <th style="padding:6px 8px;text-align:left">Cluster</th>
                <th style="padding:6px 8px">Size</th>
                <th style="padding:6px 8px;text-align:left">Criticality</th>
                <th style="padding:6px 8px;text-align:left">Error</th>
            </tr></thead>
            <tbody>{cl_rows}</tbody>
        </table>""")

    # Provenance footer
    prov = report.provenance
    sections.append(f"""
    <div style="margin-top:30px;padding-top:12px;border-top:1px solid #E2E8F0;font-size:11px;color:#9CA3AF">
        Generated by TestLookup |
        Confidence: {prov.get('confidence', 'N/A')}% |
        Sources: {', '.join(prov.get('sources_used', []))} |
        {_e(report.generated_at)}
    </div>""")

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Release Report — Build {_e(report.build_number)}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #F9FAFB; color: #1F2937; }}
        h1 {{ color: #1E40AF; margin-bottom: 4px; }}
        h2 {{ margin-top: 24px; font-size: 16px; }}
        table {{ margin-top: 8px; }}
        td, th {{ border: 1px solid #E2E8F0; }}
        tr:nth-child(even) {{ background: #F8FAFC; }}
    </style>
</head>
<body>
    <h1>TestLookup — Release Report</h1>
    <p style="color:#6B7280;font-size:13px">Build: <b>{_e(report.build_number)}</b> | Branch: <b>{_e(report.branch)}</b> | {report.layout.title()} Report</p>
    {body}
</body>
</html>"""


def _render_executive_panel_html(panel: dict) -> str:
    """Render the structured executive panel as inline-styled HTML."""
    metrics = panel.get("metrics", {})
    signal = panel.get("status_signal", "CONDITIONAL_GO")
    signal_color = _REC_COLORS.get(signal, "#D97706")
    risk = panel.get("risk_score")

    parts: list[str] = []

    # Headline + status
    risk_html = f' <span style="color:#6B7280;font-size:13px;margin-left:12px">Risk {risk}/100</span>' if risk is not None else ""
    parts.append(f"""
    <div style="background:{signal_color}10;border:2px solid {signal_color};border-radius:12px;padding:16px;margin-bottom:20px">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
            <h2 style="margin:0;color:#1E40AF;font-size:16px">{_e(panel.get('headline', ''))}</h2>
            <span style="background:{signal_color};color:white;padding:4px 10px;border-radius:6px;font-size:12px;font-weight:bold">{signal.replace('_', ' ')}</span>
            {risk_html}
        </div>""")

    # Metric strip as table
    parts.append(f"""
        <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;text-align:center">
            <tr style="background:#F0F9FF">
                <td style="padding:8px;border:1px solid #E2E8F0"><b>{metrics.get('pass_rate', 0):.1f}%</b><br><small>Pass Rate</small></td>
                <td style="padding:8px;border:1px solid #E2E8F0"><b>{metrics.get('total_tests', 0)}</b><br><small>Total</small></td>
                <td style="padding:8px;border:1px solid #E2E8F0"><b>{metrics.get('failed', 0)}</b><br><small>Failed</small></td>
                <td style="padding:8px;border:1px solid #E2E8F0"><b>{metrics.get('skipped', 0)}</b><br><small>Skipped</small></td>
                <td style="padding:8px;border:1px solid #E2E8F0"><b>{metrics.get('failure_clusters', 0)}</b><br><small>Clusters</small></td>
                <td style="padding:8px;border:1px solid #E2E8F0"><b>{metrics.get('anomaly_count', 0)}</b><br><small>Anomalies</small></td>
            </tr>
        </table>""")

    # Dominant failure
    dom = panel.get("dominant_failure")
    if dom:
        cat = str(dom.get("category", "")).replace("_", " ").title()
        parts.append(f"""
        <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;padding:8px 12px;margin-top:10px;font-size:13px">
            <b>{_e(cat)}</b> — {dom.get('count', 0)} failures ({dom.get('percentage', 0):.0f}%)
        </div>""")

    # Key takeaways
    takeaways = panel.get("key_takeaways", [])
    if takeaways:
        items = "".join(f"<li>{_e(t, 200)}</li>" for t in takeaways)
        parts.append(f'<div style="margin-top:10px;font-size:13px"><b>Key Takeaways</b><ul style="margin-top:4px">{items}</ul></div>')

    # Baseline comparison
    bl = panel.get("baseline_comparison")
    if bl:
        delta = bl.get("pass_rate_delta", 0)
        delta_str = f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%"
        parts.append(f"""
        <div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:8px;padding:8px 12px;margin-top:10px;font-size:13px">
            Pass rate: <b>{delta_str}</b> | New failures: <b>{bl.get('new_failures', 0)}</b> | Resolved: <b>{bl.get('resolved', 0)}</b>
        </div>""")

    # Next actions
    actions = panel.get("next_actions", [])
    if actions:
        items = "".join(f"<li>{_e(a, 200)}</li>" for a in actions)
        parts.append(f'<div style="margin-top:10px;font-size:13px"><b>Next Actions</b><ol style="margin-top:4px">{items}</ol></div>')

    parts.append("</div>")
    return "\n".join(parts)
