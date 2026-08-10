"""Async SMTP email delivery via aiosmtplib."""
import logging
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── DB-backed SMTP config resolver ────────────────────────────

async def get_smtp_config() -> dict[str, Any]:
    """Public accessor for the effective SMTP config.

    Callers outside this module need to know whether email can be delivered at
    all — dispatch reports an unconfigured channel as a failure rather than
    letting a silent skip read as a send.

    Resolves on demand and therefore opens a DB session; concurrent delivery
    paths must keep threading a pre-resolved ``smtp_cfg`` instead (see
    ``send_notification``).
    """
    return await _get_smtp_cfg()


async def _get_smtp_cfg() -> dict[str, Any]:
    """
    Return effective SMTP configuration.

    Priority: DB-stored value → environment variable defaults.
    Imports are local to avoid circular imports at module load time.
    """
    try:
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import AppSetting

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AppSetting).where(AppSetting.key == "smtp_config")
            )
            row = result.scalar_one_or_none()
            if row and row.value:
                return dict(row.value)
    except Exception as exc:
        logger.debug("Could not load SMTP config from DB, falling back to env: %s", exc)

    # Env-var fallback
    return {
        "enabled": settings.SMTP_ENABLED,
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "user": settings.SMTP_USER,
        "password": settings.SMTP_PASSWORD,
        "from_address": settings.SMTP_FROM,
        "tls": settings.SMTP_TLS,
    }

# ── HTML template helpers ─────────────────────────────────────

_STATUS_COLOURS = {
    "run_failed": "#ef4444",
    "run_passed": "#22c55e",
    "high_failure_rate": "#f97316",
    "ai_analysis_complete": "#3b82f6",
    "quality_gate_failed": "#dc2626",
    "flaky_test_detected": "#a855f7",
}

_HEADER_ICONS = {
    "run_failed": "🚨",
    "run_passed": "✅",
    "high_failure_rate": "⚠️",
    "ai_analysis_complete": "🤖",
    "quality_gate_failed": "🔴",
    "flaky_test_detected": "🌊",
}


def _build_html(
    title: str,
    body: str,
    event_type: str,
    metadata: dict,
) -> str:
    colour = _STATUS_COLOURS.get(event_type, "#3b82f6")
    icon = _HEADER_ICONS.get(event_type, "🔔")
    dashboard_url = metadata.get("dashboard_url", "#")
    build_number = metadata.get("build_number", "")
    project_name = metadata.get("project_name", "")
    pass_rate = metadata.get("pass_rate")
    total_tests = metadata.get("total_tests", "")
    failed_tests = metadata.get("failed_tests", "")

    stats_rows = ""
    if pass_rate is not None:
        stats_rows += f"""
        <tr>
          <td style="padding:4px 8px;color:#94a3b8;font-size:13px;">Pass rate</td>
          <td style="padding:4px 8px;color:#f1f5f9;font-size:13px;font-weight:600;">{pass_rate:.1f}%</td>
        </tr>"""
    if total_tests:
        stats_rows += f"""
        <tr>
          <td style="padding:4px 8px;color:#94a3b8;font-size:13px;">Total tests</td>
          <td style="padding:4px 8px;color:#f1f5f9;font-size:13px;">{total_tests}</td>
        </tr>"""
    if failed_tests:
        stats_rows += f"""
        <tr>
          <td style="padding:4px 8px;color:#94a3b8;font-size:13px;">Failed</td>
          <td style="padding:4px 8px;color:#ef4444;font-size:13px;font-weight:600;">{failed_tests}</td>
        </tr>"""
    if build_number:
        stats_rows += f"""
        <tr>
          <td style="padding:4px 8px;color:#94a3b8;font-size:13px;">Build</td>
          <td style="padding:4px 8px;color:#f1f5f9;font-size:13px;">#{build_number}</td>
        </tr>"""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#1e293b;border-radius:12px;overflow:hidden;border:1px solid #334155;">
        <!-- Header -->
        <tr>
          <td style="background:{colour};padding:20px 28px;">
            <p style="margin:0;font-size:22px;font-weight:700;color:#fff;">{icon} {title}</p>
            {f'<p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.8);">{project_name}</p>' if project_name else ''}
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:24px 28px;">
            {_build_executive_panel_section(metadata) if event_type == "ai_analysis_complete" and metadata.get("executive_panel") else f'<p style="margin:0 0 20px;font-size:15px;color:#cbd5e1;line-height:1.6;">{body}</p>'}
            {f'<table cellpadding="0" cellspacing="0" style="width:100%;background:#0f172a;border-radius:8px;margin-bottom:20px;">{stats_rows}</table>' if stats_rows and not metadata.get("executive_panel") else ''}
            {f'<a href="{dashboard_url}" style="display:inline-block;padding:10px 20px;background:{colour};color:#fff;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;">View in Dashboard →</a>' if dashboard_url != "#" else ''}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:16px 28px;border-top:1px solid #334155;">
            <p style="margin:0;font-size:12px;color:#475569;">
              TestLookup · {now}<br>
              You're receiving this because you configured notifications for this project.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _build_executive_panel_section(metadata: dict) -> str:
    """Render the executive panel as an HTML section for AI summary emails."""
    from app.services.notification.email_templates import render_executive_panel_email
    return render_executive_panel_email(metadata.get("executive_panel", {}))


def _build_plain(title: str, body: str, metadata: dict) -> str:
    lines = [title, "=" * len(title), "", body, ""]
    if metadata.get("project_name"):
        lines.append(f"Project: {metadata['project_name']}")
    if metadata.get("build_number"):
        lines.append(f"Build: #{metadata['build_number']}")
    if metadata.get("pass_rate") is not None:
        lines.append(f"Pass rate: {metadata['pass_rate']:.1f}%")
    if metadata.get("failed_tests"):
        lines.append(f"Failed tests: {metadata['failed_tests']}")
    if metadata.get("dashboard_url") and metadata["dashboard_url"] != "#":
        lines.append(f"\nView: {metadata['dashboard_url']}")
    lines.append("\n--\nTestLookup notification service")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────

async def send_notification(
    to: str,
    title: str,
    body: str,
    event_type: str,
    metadata: dict | None = None,
    smtp_cfg: dict[str, Any] | None = None,
) -> None:
    """
    Send an HTML + plain-text notification email via SMTP.
    Raises on delivery failure — caller is responsible for logging / retrying.

    ``smtp_cfg`` lets the caller pass a pre-resolved SMTP configuration so the
    DB-backed resolver (`_get_smtp_cfg`) is not re-run. This is required when
    many emails are dispatched concurrently (e.g. the notification manager's
    `asyncio.gather` fan-out): resolving the config once up front avoids each
    concurrent coroutine opening its own `AsyncSessionLocal`, which under
    asyncpg can surface as "another operation is in progress" when freshly
    opened sessions race on a shared pooled connection. When omitted, the
    config is resolved on demand (sequential single-call paths are unaffected).
    """
    cfg = smtp_cfg if smtp_cfg is not None else await _get_smtp_cfg()

    if not cfg.get("enabled"):
        logger.debug("SMTP disabled — skipping email to %s", to)
        return

    meta = metadata or {}
    msg = MIMEMultipart("alternative")
    msg["Subject"] = title
    msg["From"] = cfg.get("from_address", settings.SMTP_FROM)
    msg["To"] = to

    msg.attach(MIMEText(_build_plain(title, body, meta), "plain"))
    msg.attach(MIMEText(_build_html(title, body, event_type, meta), "html"))

    use_tls = bool(cfg.get("tls", True))
    await aiosmtplib.send(
        msg,
        hostname=cfg.get("host", settings.SMTP_HOST),
        port=int(cfg.get("port", settings.SMTP_PORT)),
        username=cfg.get("user") or None,
        password=cfg.get("password") or None,
        use_tls=use_tls,
        start_tls=not use_tls,  # STARTTLS for port 587
    )
    logger.info("Email sent to %s — event=%s", to, event_type)


async def send_html_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    smtp_cfg: dict[str, Any] | None = None,
) -> None:
    """
    Send a pre-rendered HTML email (e.g. a scheduled digest) via SMTP.

    Unlike :func:`send_notification`, the caller owns the full HTML body —
    no title/body templating is applied. Raises on delivery failure; the
    caller is responsible for logging / status tracking. This is the
    delivery function the scheduled-digest dispatcher uses (it previously
    imported a non-existent ``send_email`` from this module, so digest
    emails silently failed — fixed alongside PMF US-7.4).
    """
    cfg = smtp_cfg if smtp_cfg is not None else await _get_smtp_cfg()

    if not cfg.get("enabled"):
        logger.debug("SMTP disabled — skipping email to %s", to_email)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_address", settings.SMTP_FROM)
    msg["To"] = to_email

    msg.attach(MIMEText(text_body or subject, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    use_tls = bool(cfg.get("tls", True))
    await aiosmtplib.send(
        msg,
        hostname=cfg.get("host", settings.SMTP_HOST),
        port=int(cfg.get("port", settings.SMTP_PORT)),
        username=cfg.get("user") or None,
        password=cfg.get("password") or None,
        use_tls=use_tls,
        start_tls=not use_tls,  # STARTTLS for port 587
    )
    logger.info("HTML email sent to %s — subject=%s", to_email, subject)


async def send_html_email_with_attachments(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    attachments: list[tuple[str, str, str]] | None = None,
    smtp_cfg: dict[str, Any] | None = None,
) -> None:
    """
    Send a pre-rendered HTML email carrying file attachments (PMF US-7.5).

    ``attachments`` is a list of ``(filename, content, mime_type)`` tuples
    (content as ``str``; mime like ``"text/html"``). The MIME layout is a
    ``multipart/mixed`` envelope whose first part is the usual
    ``multipart/alternative`` (plain + HTML body), followed by one part per
    attachment with ``Content-Disposition: attachment``.

    With no attachments this delegates to :func:`send_html_email` so the
    two paths stay byte-identical for plain digests. Raises on delivery
    failure — the caller owns logging / status tracking.
    """
    if not attachments:
        await send_html_email(
            to_email, subject, html_body, text_body=text_body, smtp_cfg=smtp_cfg,
        )
        return

    cfg = smtp_cfg if smtp_cfg is not None else await _get_smtp_cfg()

    if not cfg.get("enabled"):
        logger.debug("SMTP disabled — skipping email to %s", to_email)
        return

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_address", settings.SMTP_FROM)
    msg["To"] = to_email

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text_body or subject, "plain"))
    alternative.attach(MIMEText(html_body, "html"))
    msg.attach(alternative)

    for filename, content, mime_type in attachments:
        maintype, _, subtype = (mime_type or "application/octet-stream").partition("/")
        if maintype == "text":
            part = MIMEText(content, subtype or "plain", "utf-8")
        else:
            part = MIMEApplication(
                content.encode("utf-8") if isinstance(content, str) else content,
                _subtype=subtype or "octet-stream",
            )
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    use_tls = bool(cfg.get("tls", True))
    await aiosmtplib.send(
        msg,
        hostname=cfg.get("host", settings.SMTP_HOST),
        port=int(cfg.get("port", settings.SMTP_PORT)),
        username=cfg.get("user") or None,
        password=cfg.get("password") or None,
        use_tls=use_tls,
        start_tls=not use_tls,  # STARTTLS for port 587
    )
    logger.info(
        "HTML email with %d attachment(s) sent to %s — subject=%s",
        len(attachments), to_email, subject,
    )
