"""The Trends report email: its HTML and its one send (W08 TRND-01).

The email renders ``trends_report_service.build_trends_report``'s payload --
the same payload the Trends PDF renders, read through the page's own service
functions -- so the mailed numbers are the page's (evaluated basis, run
aggregates, UTC days). Its earlier private query (``fetch_trend_data``) and
its stdlib ``smtplib`` sender, which ignored the relay saved in Settings ›
Notifications, are gone: :func:`send_trends_email` hands ONE message for every
recipient to ``email_service.send_html_email`` with the resolved relay, where
the offline egress gate runs on the send itself, bounded by
:data:`SEND_TIMEOUT_S`.

The router builds, gates and sends; nothing here raises ``HTTPException``.
Every value in the HTML is ``html.escape``d (project, release, suite and chart
names are untrusted text); the HTML carries no script and no remote image.
"""
from __future__ import annotations

import asyncio
import html as html_lib
from datetime import date, datetime, timezone
from typing import Any, Final, Optional

from app.services import trends_report_service as report
from app.services.report_context import context_fields, has_context, n_of_m_text

#: A relay that has not accepted the message in this long is a 504: "the
#: report may still arrive" (a timeout is not a failure).
SEND_TIMEOUT_S: Final = 20

CHART_LABELS = {
    "daily_breakdown": "Daily Breakdown",
    "pass_rate_trend": "Pass Rate Trend",
    "cumulative_volume": "Cumulative Volume",
    "failure_rate": "Failure Rate",
    "broken_trend": "Broken Tests Trend",
    "skipped_trend": "Skipped Tests Trend",
    "status_pie": "Status Distribution",
}

_CELL = "padding:6px 12px;border-bottom:1px solid #e5e7eb;"
_HEAD = "padding:8px 12px;text-align:left;font-weight:600;"


def _e(value: Any) -> str:
    return html_lib.escape("" if value is None else str(value))


def _context_html(meta: Optional[dict]) -> str:
    """VIZ-308: the report's context block (Project, Release, Test Suite,
    Window, basis, Generated) and its "N of M" line, from ``meta``. Every value
    is escaped: release and suite names are untrusted text."""
    if meta is None or not has_context(meta):
        return ""
    cell = "padding:4px 12px;border-bottom:1px solid #e5e7eb;vertical-align:top;"
    rows = "".join(
        f'<tr><td style="{cell}font-weight:600;white-space:nowrap;">{_e(f.label)}</td>'
        f'<td style="{cell}">{_e(f.value)}</td></tr>'
        for f in context_fields(meta)
    )
    return (
        '<h2 style="font-size:16px;margin:0 0 8px;">Report context</h2>'
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:8px;">{rows}</table>'
        f'<p style="font-size:13px;color:#6b7280;margin:0 0 20px;">{_e(n_of_m_text(meta))}</p>'
    )


def _kpis_html(payload: dict) -> str:
    cells = "".join(
        '<td style="padding:12px;background:#f8fafc;border:1px solid #e5e7eb;vertical-align:top;">'
        f'<div style="font-size:11px;color:#6b7280;">{_e(label)}</div>'
        f'<div style="font-size:22px;font-weight:700;color:#111827;">{_e(value)}</div>'
        + (f'<div style="font-size:11px;color:#6b7280;">{_e(note)}</div>' if note else "")
        + "</td>"
        for label, value, note in report.kpis(payload)
    )
    return (
        '<h2 style="font-size:16px;margin:0 0 12px;">Key figures</h2>'
        f'<table style="width:100%;border-collapse:collapse;margin-bottom:12px;"><tr>{cells}</tr></table>'
    )


def _verdict_html(payload: dict) -> str:
    lines = [f'<p style="font-size:14px;font-weight:600;margin:0 0 4px;">{_e(report.verdict_text(payload))}</p>']
    gap = report.gap_text(payload)
    if gap:
        lines.append(f'<p style="font-size:13px;color:#b45309;margin:0 0 4px;">{_e(gap)}</p>')
    return "".join(lines)


def _daily_html(payload: dict) -> str:
    rows = "".join(
        "<tr>"
        f'<td style="{_CELL}">{_e(d["date"])}</td>'
        f'<td style="{_CELL}color:#059669;">{d["passed"]:,}</td>'
        f'<td style="{_CELL}color:#dc2626;">{d["failed"]:,}</td>'
        f'<td style="{_CELL}color:#ea580c;">{d["broken"]:,}</td>'
        f'<td style="{_CELL}color:#d97706;">{d["skipped"]:,}</td>'
        f'<td style="{_CELL}font-weight:600;">{_e(report.pct_text(d["pass_rate"]))}</td>'
        "</tr>"
        for d in report.window_days(payload)
    )
    head = "".join(
        f'<th style="{_HEAD}">{label}</th>'
        for label in ("Date (UTC)", "Passed", "Failed", "Broken", "Skipped", "Pass rate")
    )
    return (
        '<h2 style="font-size:16px;margin:24px 0 12px;">Daily Breakdown</h2>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        f'<thead><tr style="background:#f3f4f6;">{head}</tr></thead><tbody>{rows}</tbody></table>'
    )


def _suites_html(payload: dict) -> str:
    suites = report.suite_rows(payload)
    if not suites:
        return ""
    rows = "".join(
        "<tr>"
        f'<td style="{_CELL}">{_e(s["suite_name"])}</td>'
        f'<td style="{_CELL}">{s["passed"]:,}</td>'
        f'<td style="{_CELL}">{s["failed"]:,}</td>'
        f'<td style="{_CELL}">{s["skipped"]:,}</td>'
        f'<td style="{_CELL}font-weight:600;">{_e(report.pct_text(s["pass_rate"]))}</td>'
        "</tr>"
        for s in suites
    )
    head = "".join(
        f'<th style="{_HEAD}">{label}</th>' for label in ("Suite", "Passed", "Failed", "Skipped", "Pass rate")
    )
    return (
        '<h2 style="font-size:16px;margin:24px 0 12px;">Suites</h2>'
        '<p style="font-size:12px;color:#6b7280;margin:0 0 8px;">Failed includes broken.</p>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        f'<thead><tr style="background:#f3f4f6;">{head}</tr></thead><tbody>{rows}</tbody></table>'
    )


def _charts_html(chart_ids: Any) -> str:
    """The legacy ``chart_ids`` a caller named (an integration's own list); none, nothing."""
    if not chart_ids:
        return ""
    items = "".join(f"<li>{_e(CHART_LABELS.get(str(c), str(c)))}</li>" for c in chart_ids)
    return (
        '<p style="font-size:13px;color:#6b7280;margin:16px 0 4px;">Charts included:</p>'
        f'<ul style="margin:4px 0 0;padding-left:20px;font-size:13px;">{items}</ul>'
    )


def _generated(payload: dict) -> str:
    raw = payload.get("generated_at")
    try:
        moment = datetime.fromisoformat(str(raw)) if raw else datetime.now(timezone.utc)
    except ValueError:
        moment = datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_trends_email_html(payload: dict) -> str:
    """The Trends report email: the PDF's sections in HTML, every value escaped."""
    project = _e(payload.get("project_name") or "—")
    days = int(payload.get("window_days") or 0)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Trends report - {project}</title></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:20px;background:#f9fafb;color:#111827;">
  <div style="max-width:800px;margin:0 auto;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <div style="background:#1d4ed8;padding:24px 32px;">
      <h1 style="color:white;margin:0;font-size:22px;">TestLookup - Trends report</h1>
      <p style="color:#bfdbfe;margin:4px 0 0;">Project: <strong>{project}</strong> - Last {days} days</p>
      <p style="color:#bfdbfe;margin:2px 0 0;font-size:12px;">Generated: {_e(_generated(payload))}</p>
    </div>
    <div style="padding:24px 32px;">
      {_context_html(payload.get("meta"))}
      {_kpis_html(payload)}
      {_verdict_html(payload)}
      {_charts_html(payload.get("chart_ids"))}
      {_daily_html(payload)}
      {_suites_html(payload)}
    </div>
    <div style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;font-size:11px;color:#9ca3af;">
      This report was generated automatically by TestLookup. Do not reply to this email.
    </div>
  </div>
</body>
</html>"""


def build_html_report(
    project_name: str,
    days: int,
    chart_ids: list[str],
    trend_data: list[dict],
    meta: dict | None = None,
) -> str:
    """The legacy entry point, now a thin wrapper over :func:`build_trends_email_html`.

    The window ends on the newest day of ``trend_data`` (today when there is
    none) and the summary is computed over those rows alone (no suites): the
    pass rate is the evaluated rate over the totals, never a mean of daily
    rates.
    """
    from app.services.trend_summary_service import summarize, utc_today  # noqa: PLC0415

    dates = sorted(str(row.get("date") or "")[:10] for row in trend_data if row.get("date"))
    try:
        today = date.fromisoformat(dates[-1]) if dates else utc_today()
    except ValueError:
        today = utc_today()
    payload = {
        "project_name": project_name,
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trend": list(trend_data),
        "suites": [],
        "summary": summarize(trend_data, [], days=days, today=today),
        "pass_rate_delta_pp": None,
        "meta": meta,
        "chart_ids": list(chart_ids or []),
    }
    return build_trends_email_html(payload)


async def send_trends_email(cfg: dict, recipients: list[str], subject: str, html: str) -> None:
    """ONE message to every recipient (``To`` lists them all) through the
    resolved relay ``cfg``; ``send_html_email`` gates the relay and the
    recipients where it opens the connection. Raises ``asyncio.TimeoutError``
    after :data:`SEND_TIMEOUT_S`, and whatever the relay raises."""
    from app.services.notification import email_service  # noqa: PLC0415

    await asyncio.wait_for(
        email_service.send_html_email(", ".join(recipients), subject, html, smtp_cfg=cfg),
        timeout=SEND_TIMEOUT_S,
    )
