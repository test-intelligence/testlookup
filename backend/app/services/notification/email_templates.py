"""
Shared email template helpers — reusable HTML sections for all email types.

Used by email_service.py for notification emails and digest_content_service.py
for scheduled digest emails. All functions return inline-styled HTML strings.
"""
import html


def _e(text: str, max_len: int = 500) -> str:
    """HTML-escape and truncate."""
    s = html.escape(str(text or ""))
    return s[:max_len] + "..." if len(s) > max_len else s


_SIGNAL_COLOURS = {
    "GO": "#059669",
    "CONDITIONAL_GO": "#D97706",
    "NO_GO": "#DC2626",
}

_SIGNAL_BG = {
    "GO": "#064e3b",
    "CONDITIONAL_GO": "#78350f",
    "NO_GO": "#7f1d1d",
}


def render_executive_panel_email(panel: dict) -> str:
    """Render the full executive panel as an inline-styled HTML block for email."""
    if not panel:
        return ""

    metrics = panel.get("metrics", {})
    signal = panel.get("status_signal", "CONDITIONAL_GO")
    signal_colour = _SIGNAL_COLOURS.get(signal, "#D97706")
    signal_bg = _SIGNAL_BG.get(signal, "#78350f")
    risk = panel.get("risk_score")
    headline = _e(panel.get("headline", ""), 200)

    parts: list[str] = []

    # Header: headline + status badge
    risk_html = f'<span style="color:#94a3b8;font-size:12px;margin-left:8px;">Risk {risk}/100</span>' if risk is not None else ""
    parts.append(f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
      <tr>
        <td style="font-size:16px;font-weight:700;color:#f1f5f9;padding-bottom:8px;">
          {headline}
        </td>
      </tr>
      <tr>
        <td>
          <span style="display:inline-block;background:{signal_bg};color:{signal_colour};border:1px solid {signal_colour};padding:4px 10px;border-radius:6px;font-size:12px;font-weight:700;">
            {_e(str(signal).replace('_', ' '))}
          </span>
          {risk_html}
        </td>
      </tr>
    </table>""")

    # Metrics strip
    parts.append(render_metrics_strip_email(metrics))

    # Dominant failure
    dom = panel.get("dominant_failure")
    if dom:
        cat = _e(str(dom.get("category", "")).replace("_", " ").title())
        parts.append(f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#1c1917;border:1px solid #44403c;border-radius:8px;margin-bottom:12px;">
      <tr>
        <td style="padding:8px 12px;font-size:13px;color:#fbbf24;">
          ⚠ <b>{cat}</b> — {dom.get('count', 0)} failure{'s' if dom.get('count', 0) != 1 else ''} ({dom.get('percentage', 0):.0f}%)
        </td>
      </tr>
    </table>""")

    # Key takeaways
    takeaways = panel.get("key_takeaways", [])
    if takeaways:
        parts.append(render_key_takeaways_email(takeaways))

    # Baseline comparison
    bl = panel.get("baseline_comparison")
    if bl and bl.get("pass_rate_delta") is not None:
        delta = bl["pass_rate_delta"]
        delta_colour = "#059669" if delta >= 0 else "#DC2626"
        delta_str = f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%"
        parts.append(f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;border-radius:8px;margin-bottom:12px;">
      <tr>
        <td style="padding:8px 12px;font-size:13px;color:#94a3b8;">
          Baseline: <span style="color:{delta_colour};font-weight:600;">{delta_str}</span> pass rate
          · {bl.get('new_failures', 0)} new failures
          · {bl.get('resolved', 0)} resolved
        </td>
      </tr>
    </table>""")

    # Next actions
    actions = panel.get("next_actions", [])
    if actions:
        parts.append(render_action_items_email(actions))

    return "\n".join(parts)


def render_metrics_strip_email(metrics: dict) -> str:
    """Render a 3-column metrics table for email."""
    pass_rate = metrics.get("pass_rate", 0)
    pr_colour = "#059669" if pass_rate >= 90 else "#D97706" if pass_rate >= 70 else "#DC2626"
    failed = metrics.get("failed", 0)
    fail_colour = "#059669" if failed == 0 else "#ef4444"

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;border-radius:8px;margin-bottom:12px;">
      <tr>
        <td style="padding:10px;text-align:center;width:33%;">
          <div style="font-size:22px;font-weight:700;color:{pr_colour};">{pass_rate:.1f}%</div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">Pass Rate</div>
        </td>
        <td style="padding:10px;text-align:center;width:33%;">
          <div style="font-size:22px;font-weight:700;color:#f1f5f9;">{metrics.get('total_tests', 0)}</div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">Total Tests</div>
        </td>
        <td style="padding:10px;text-align:center;width:33%;">
          <div style="font-size:22px;font-weight:700;color:{fail_colour};">{failed}</div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">{'All Passed' if failed == 0 else 'Failed'}</div>
        </td>
      </tr>
    </table>"""


def render_key_takeaways_email(takeaways: list[str]) -> str:
    """Render key takeaways as a bulleted list."""
    items = "".join(
        f'<tr><td style="padding:2px 0;font-size:13px;color:#cbd5e1;">• {_e(t, 200)}</td></tr>'
        for t in takeaways[:4]
    )
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
      <tr><td style="font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;padding-bottom:4px;">Key Takeaways</td></tr>
      {items}
    </table>"""


def render_action_items_email(actions: list[str]) -> str:
    """Render next actions as a numbered list."""
    items = "".join(
        f'<tr><td style="padding:2px 0;font-size:13px;color:#cbd5e1;">{i}. {_e(a, 200)}</td></tr>'
        for i, a in enumerate(actions[:3], 1)
    )
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
      <tr><td style="font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;padding-bottom:4px;">Next Actions</td></tr>
      {items}
    </table>"""


def render_release_signal_email(recommendation: str, risk_score: int | None = None) -> str:
    """Render release recommendation badge."""
    recommendation = str(recommendation)
    colour = _SIGNAL_COLOURS.get(recommendation, "#D97706")
    bg = _SIGNAL_BG.get(recommendation, "#78350f")
    risk_html = f" · Risk {risk_score}/100" if risk_score is not None else ""
    return f"""
    <span style="display:inline-block;background:{bg};color:{colour};border:1px solid {colour};padding:4px 10px;border-radius:6px;font-size:12px;font-weight:700;">
      {_e(recommendation.replace('_', ' '))}{risk_html}
    </span>"""
