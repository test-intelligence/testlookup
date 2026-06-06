"""
Notification manager — resolves preferences and dispatches to email/Slack/Teams.

Called by the Celery task `dispatch_run_notifications` after each run completes
and by `dispatch_ai_notifications` after AI triage finishes.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    NotificationChannel,
    NotificationEventType,
    NotificationLog,
    NotificationPreference,
    User,
)
from app.services.notification import email_service, slack_service, teams_service

logger = logging.getLogger(__name__)


# ── Message builders ──────────────────────────────────────────

def _run_message(
    event: NotificationEventType,
    project_name: str,
    build_number: str,
    pass_rate: float,
    total_tests: int,
    failed_tests: int,
    dashboard_url: str,
) -> tuple[str, str]:
    """Return (title, body) for a run-level event."""
    if event == NotificationEventType.RUN_PASSED:
        title = f"✅ Build #{build_number} passed — {project_name}"
        body = (
            f"All {total_tests} tests passed with a 100% pass rate. "
            f"Great work — no failures detected in this run."
        )
    elif event == NotificationEventType.HIGH_FAILURE_RATE:
        title = f"⚠️ High failure rate — {project_name} build #{build_number}"
        body = (
            f"{failed_tests} of {total_tests} tests failed "
            f"(pass rate: {pass_rate:.1f}%). Immediate attention recommended."
        )
    else:  # run_failed
        title = f"🚨 Build #{build_number} failed — {project_name}"
        body = (
            f"{failed_tests} of {total_tests} tests failed "
            f"(pass rate: {pass_rate:.1f}%). Review the test results for details."
        )
    return title, body


def _ai_message(
    project_name: str,
    test_name: str,
    root_cause: str,
    confidence: int,
    dashboard_url: str,
) -> tuple[str, str]:
    """Return (title, body) for an AI analysis completion event."""
    title = f"🤖 AI analysis ready — {test_name}"
    body = (
        f"Root cause analysis completed for *{test_name}* in {project_name} "
        f"(confidence: {confidence}%).\n\n_{root_cause}_"
    )
    return title, body


# ── Core dispatch logic ───────────────────────────────────────

async def _dispatch_to_channel(
    pref: NotificationPreference,
    user_email: Optional[str],
    title: str,
    body: str,
    event_type: NotificationEventType,
    metadata: dict,
    smtp_cfg: Optional[dict] = None,
) -> tuple[str, Optional[str]]:
    """
    Send one notification through the channel specified by `pref`.
    Returns (status, error_detail).

    ``smtp_cfg`` is the pre-resolved SMTP configuration. When several
    deliveries run concurrently (``_load_and_notify`` fans out via
    ``asyncio.gather``) the email path must NOT open its own DB session to
    resolve the config — every concurrent coroutine doing so would race on the
    shared engine pool and asyncpg raises "another operation is in progress".
    The caller resolves the config once and threads it through here.
    """
    try:
        if pref.channel == NotificationChannel.EMAIL:
            to = pref.email_override or user_email
            if not to:
                return "failed", "No email address available"
            await email_service.send_notification(
                to=to,
                title=title,
                body=body,
                event_type=event_type.value,
                metadata=metadata,
                smtp_cfg=smtp_cfg,
            )

        elif pref.channel == NotificationChannel.SLACK:
            webhook_url = pref.slack_webhook_url or settings.SLACK_WEBHOOK_URL
            if not webhook_url:
                return "failed", "No Slack webhook URL configured"
            await slack_service.send_notification(
                webhook_url=webhook_url,
                title=title,
                body=body,
                event_type=event_type.value,
                metadata=metadata,
            )

        elif pref.channel == NotificationChannel.TEAMS:
            webhook_url = pref.teams_webhook_url or settings.TEAMS_WEBHOOK_URL
            if not webhook_url:
                return "failed", "No Teams webhook URL configured"
            await teams_service.send_notification(
                webhook_url=webhook_url,
                title=title,
                body=body,
                event_type=event_type.value,
                metadata=metadata,
            )

        return "sent", None

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Notification delivery failed channel=%s event=%s: %s",
            pref.channel,
            event_type.value,
            exc,
        )
        return "failed", str(exc)


async def _load_and_notify(
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    events: list[NotificationEventType],
    build_title_fn,   # (event, pref) -> (title, body)
    metadata: dict,
) -> None:
    async with AsyncSessionLocal() as db:
        # Load all preferences for this project (and global preferences)
        prefs_result = await db.execute(
            select(NotificationPreference, User.email)
            .join(User, NotificationPreference.user_id == User.id)
            .where(
                NotificationPreference.enabled.is_(True),
                or_(
                    NotificationPreference.project_id == project_id,
                    NotificationPreference.project_id.is_(None),
                ),
            )
        )
        rows = prefs_result.all()

        # Phase 1 — resolve each subscribed preference into (pref, user_email,
        # event, title, body). This is pure Python work, no I/O.
        priority = [
            NotificationEventType.HIGH_FAILURE_RATE,
            NotificationEventType.RUN_FAILED,
            NotificationEventType.QUALITY_GATE_FAILED,
            NotificationEventType.FLAKY_TEST_DETECTED,
            NotificationEventType.AI_ANALYSIS_COMPLETE,
            NotificationEventType.RUN_PASSED,
        ]
        plans: list[tuple] = []
        for pref, user_email in rows:
            subscribed = set(pref.events or [])
            matching = [e for e in events if e.value in subscribed]
            if not matching:
                continue

            if NotificationEventType.HIGH_FAILURE_RATE in matching:
                threshold = pref.failure_rate_threshold or 80.0
                pass_rate = metadata.get("pass_rate", 100.0)
                if pass_rate >= threshold:
                    matching.remove(NotificationEventType.HIGH_FAILURE_RATE)
            if not matching:
                continue

            event = next((e for e in priority if e in matching), matching[0])
            title, body = build_title_fn(event)
            plans.append((pref, user_email, event, title, body))

        if not plans:
            return

        # Resolve the DB-backed SMTP config exactly ONCE, before the
        # fan-out, and only if at least one plan targets the email channel.
        # Doing this inside the gather (one AsyncSessionLocal per concurrent
        # email coroutine) is what triggered BUG-002: the freshly opened
        # sessions raced on the shared engine pool and asyncpg raised
        # "another operation is in progress". Resolving up front means no DB
        # work happens concurrently across the gathered coroutines.
        smtp_cfg = None
        if any(pref.channel == NotificationChannel.EMAIL for (pref, *_rest) in plans):
            smtp_cfg = await email_service._get_smtp_cfg()

        # Phase 2 — fan out all deliveries in parallel. A slow webhook no
        # longer blocks the next recipient. No coroutine here touches a DB
        # session, so they cannot race on a shared connection.
        results = await asyncio.gather(
            *(
                _dispatch_to_channel(pref, user_email, title, body, event, metadata, smtp_cfg)
                for (pref, user_email, event, title, body) in plans
            ),
            return_exceptions=False,
        )

        # Phase 3 — accumulate every NotificationLog row and commit once.
        now_sent = datetime.now(timezone.utc)
        for (pref, _user_email, event, title, body), (status, error_detail) in zip(plans, results):
            db.add(NotificationLog(
                user_id=pref.user_id,
                project_id=project_id,
                run_id=run_id,
                channel=pref.channel.value,
                event_type=event.value,
                title=title,
                body=body,
                status=status,
                error_detail=error_detail,
                sent_at=now_sent if status == "sent" else None,
            ))

        await db.commit()


# ── Public entry points ───────────────────────────────────────

async def dispatch_run_notifications(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    build_number: str,
    pass_rate: float,
    total_tests: int,
    failed_tests: int,
    project_name: str,
    dashboard_url: str = "#",
) -> None:
    """
    Evaluate which run-level events apply and send to all subscribed users.
    Called from the `dispatch_run_notifications` Celery task.
    """
    events: list[NotificationEventType] = []
    if failed_tests > 0:
        events.append(NotificationEventType.RUN_FAILED)
    else:
        events.append(NotificationEventType.RUN_PASSED)
    if pass_rate < 100:
        events.append(NotificationEventType.HIGH_FAILURE_RATE)

    meta = {
        "project_name": project_name,
        "build_number": build_number,
        "pass_rate": pass_rate,
        "total_tests": total_tests,
        "failed_tests": failed_tests,
        "dashboard_url": dashboard_url,
    }

    def _msg(event: NotificationEventType) -> tuple[str, str]:
        return _run_message(
            event, project_name, build_number, pass_rate,
            total_tests, failed_tests, dashboard_url,
        )

    await _load_and_notify(project_id, run_id, events, _msg, meta)
    logger.info(
        "Run notifications dispatched project=%s build=%s pass_rate=%.1f",
        project_id,
        build_number,
        pass_rate,
    )


async def dispatch_ai_notifications(
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    test_name: str,
    root_cause: str,
    confidence: int,
    project_name: str,
    dashboard_url: str = "#",
) -> None:
    """
    Send AI analysis completion notifications to subscribed users.
    Called from the `run_ai_analysis` Celery task on success.
    """
    events = [NotificationEventType.AI_ANALYSIS_COMPLETE]

    meta = {
        "project_name": project_name,
        "test_name": test_name,
        "confidence": confidence,
        "dashboard_url": dashboard_url,
    }

    def _msg(_event: NotificationEventType) -> tuple[str, str]:
        return _ai_message(project_name, test_name, root_cause, confidence, dashboard_url)

    await _load_and_notify(project_id, run_id, events, _msg, meta)


async def dispatch_ai_summary_notifications(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    build_number: str,
    project_name: str,
    executive_summary: str,
    executive_panel: dict | None = None,
    pass_rate: float = 0.0,
    total_tests: int = 0,
    failed_tests: int = 0,
    dashboard_url: str = "#",
) -> None:
    """
    Send AI executive-summary email after the AI pipeline completes.

    Triggered from the run_agent_pipeline Celery task after the summary
    stage finishes and the intelligence snapshot is persisted.
    """
    events = [NotificationEventType.AI_ANALYSIS_COMPLETE]

    status_signal = "CONDITIONAL_GO"
    risk_score = None
    if executive_panel:
        status_signal = executive_panel.get("status_signal", "CONDITIONAL_GO")
        risk_score = executive_panel.get("risk_score")

    title = f"🤖 AI Summary — Build {build_number}"
    if failed_tests == 0:
        body = f"All {total_tests} tests passed in {project_name} (build {build_number}). No issues detected."
    else:
        body = (
            f"{failed_tests} failure{'s' if failed_tests != 1 else ''} detected in {project_name} "
            f"(build {build_number}, {pass_rate:.1f}% pass rate). "
            f"Release signal: {status_signal.replace('_', ' ')}"
            + (f" (risk {risk_score}/100)" if risk_score is not None else "")
            + f".\n\n{executive_summary}"
        )

    meta = {
        "project_name": project_name,
        "build_number": build_number,
        "pass_rate": pass_rate,
        "total_tests": total_tests,
        "failed_tests": failed_tests,
        "dashboard_url": dashboard_url,
        "executive_panel": executive_panel,
    }

    def _msg(_event: NotificationEventType) -> tuple[str, str]:
        return title, body

    await _load_and_notify(project_id, run_id, events, _msg, meta)


async def send_test_notification(
    user_id: uuid.UUID,
    channel: NotificationChannel,
    email_override: Optional[str],
    slack_webhook_url: Optional[str],
    teams_webhook_url: Optional[str],
) -> tuple[str, Optional[str]]:
    """
    Send a single test notification to verify configuration.
    Returns (status, error_detail).
    """
    title = "🔔 TestLookup — Test notification"
    body = "If you received this, your notification channel is configured correctly."
    meta: dict = {}

    async with AsyncSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        user_email = user.email if user else None

    # Build a mock preference for dispatch
    from app.models.postgres import NotificationPreference as NP
    mock_pref = NP(
        user_id=user_id,
        channel=channel,
        enabled=True,
        email_override=email_override,
        slack_webhook_url=slack_webhook_url,
        teams_webhook_url=teams_webhook_url,
    )

    return await _dispatch_to_channel(
        mock_pref,
        user_email,
        title,
        body,
        NotificationEventType.RUN_PASSED,
        meta,
    )
