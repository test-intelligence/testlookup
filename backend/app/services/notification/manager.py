"""
Notification manager — resolves preferences and dispatches to email/Slack/Teams.

Called by the Celery task `dispatch_run_notifications` after each run completes
and by `dispatch_ai_notifications` after AI triage finishes.
"""
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    DigestSubscription,
    NotificationChannel,
    NotificationEventType,
    NotificationLog,
    NotificationPreference,
    User,
)
from app.services.notification import email_service, slack_service, teams_service

logger = logging.getLogger(__name__)

_MAX_DURABLE_DELIVERY_ATTEMPTS = 8
_TEAM_ROUTE_METADATA_KEY = "_durable_team_route"
_EXPLICIT_ROUTE_METADATA_KEY = "_durable_explicit_route"


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

def _enabled_global_webhook(channel: str, resolved: Optional[dict]) -> Optional[str]:
    """Return a global webhook only when its matching kill switch is on."""
    if resolved is not None:
        return resolved.get(f"{channel}_webhook_url") if resolved.get(f"{channel}_enabled") else None
    if channel == "slack":
        return settings.SLACK_WEBHOOK_URL if settings.SLACK_ENABLED else None
    return settings.TEAMS_WEBHOOK_URL if settings.TEAMS_ENABLED else None


def preference_webhook(
    channel: str, override: Optional[str], resolved: Optional[dict]
) -> tuple[Optional[str], bool]:
    """The webhook a subscriber's delivery goes to, and whether it is the deployment's own.

    ``override`` is the subscriber's own webhook, which ``POST/PUT
    /api/v1/notifications/preferences`` accepts from any authenticated user.
    It wins when set, and it is never deployment-wide. The operator's
    ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS`` names hosts, and Slack and Teams
    put every workspace on the same hosts, so a user could otherwise receive
    notification content in a workspace of their own (code review of H10).
    Without an override the delivery falls back to the global webhook an
    admin configured, which is deployment-wide.
    """
    if override:
        return override, False
    return _enabled_global_webhook(channel, resolved), True


def _notification_channel_value(channel: NotificationChannel | str) -> str:
    """Normalize ORM enum values across PostgreSQL driver configurations."""
    if isinstance(channel, NotificationChannel):
        return channel.value
    return NotificationChannel(channel).value


async def _dispatch_to_channel(
    pref: NotificationPreference,
    user_email: Optional[str],
    title: str,
    body: str,
    event_type: NotificationEventType,
    metadata: dict,
    smtp_cfg: Optional[dict] = None,
    global_webhooks: Optional[dict] = None,
    delivery_id: Optional[str] = None,
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
            # An unconfigured channel is a failure, not a send. send_notification
            # returns early and silently when SMTP is disabled, so without this
            # the "no exception means it worked" path below reported "sent" —
            # and that status is what NotificationLog records, so suppressed
            # emails were logged as delivered. The Slack branch below has always
            # reported its missing webhook honestly; this makes email match.
            cfg = smtp_cfg if smtp_cfg is not None else await email_service.get_smtp_config()
            if not (cfg or {}).get("enabled"):
                return "failed", "SMTP is not configured — no email was sent"
            await email_service.send_notification(
                to=to,
                title=title,
                body=body,
                event_type=event_type.value,
                metadata=metadata,
                smtp_cfg=smtp_cfg,
                delivery_id=delivery_id,
            )

        elif pref.channel == NotificationChannel.SLACK:
            webhook_url, deployment_wide = preference_webhook(
                "slack", pref.slack_webhook_url, global_webhooks
            )
            if not webhook_url:
                return "failed", "No Slack webhook URL configured"
            await slack_service.send_notification(
                webhook_url=webhook_url,
                title=title,
                body=body,
                event_type=event_type.value,
                metadata=metadata,
                delivery_id=delivery_id,
                deployment_wide=deployment_wide,
            )

        elif pref.channel == NotificationChannel.TEAMS:
            webhook_url, deployment_wide = preference_webhook(
                "teams", pref.teams_webhook_url, global_webhooks
            )
            if not webhook_url:
                return "failed", "No Teams webhook URL configured"
            await teams_service.send_notification(
                webhook_url=webhook_url,
                title=title,
                body=body,
                event_type=event_type.value,
                metadata=metadata,
                delivery_id=delivery_id,
                deployment_wide=deployment_wide,
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


def _build_notification_plans(
    rows: list[tuple[NotificationPreference, Optional[str]]],
    events: list[NotificationEventType],
    build_title_fn,
    metadata: dict,
) -> list[tuple[NotificationPreference, Optional[str], NotificationEventType, str, str]]:
    """Resolve preferences into the one highest-priority message per channel."""
    priority = [
        NotificationEventType.HIGH_FAILURE_RATE,
        NotificationEventType.RUN_FAILED,
        NotificationEventType.QUALITY_GATE_FAILED,
        NotificationEventType.FLAKY_TEST_DETECTED,
        NotificationEventType.AI_ANALYSIS_COMPLETE,
        NotificationEventType.RUN_PASSED,
    ]
    plans = []
    for pref, user_email in rows:
        subscribed = set(pref.events or [])
        matching = [event for event in events if event.value in subscribed]
        if not matching:
            continue

        if NotificationEventType.HIGH_FAILURE_RATE in matching:
            threshold = pref.failure_rate_threshold or 80.0
            if metadata.get("pass_rate", 100.0) >= threshold:
                matching.remove(NotificationEventType.HIGH_FAILURE_RATE)
        if not matching:
            continue

        event = next((candidate for candidate in priority if candidate in matching), matching[0])
        title, body = build_title_fn(event)
        plans.append((pref, user_email, event, title, body))
    return plans


async def _insert_scoped_notification_plans(
    db,
    *,
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    delivery_scope: str,
    metadata: dict,
    plans: list[
        tuple[NotificationPreference, Optional[str], NotificationEventType, str, str]
    ],
) -> None:
    """Stage idempotent preference deliveries on the caller's transaction."""
    if not plans:
        return
    values = []
    for pref, _user_email, event, title, body in plans:
        channel = _notification_channel_value(pref.channel)
        material = (
            f"{delivery_scope}:{pref.user_id}:{channel}:{event.value}"
        )
        values.append(
            {
                "id": uuid.uuid4(),
                "user_id": pref.user_id,
                "project_id": project_id,
                "run_id": run_id,
                "preference_id": pref.id,
                "delivery_key": hashlib.sha256(material.encode("utf-8")).hexdigest(),
                "delivery_metadata": metadata,
                "channel": channel,
                "event_type": event.value,
                "title": title,
                "body": body,
                "status": "pending",
            }
        )
    await db.execute(
        pg_insert(NotificationLog)
        .values(values)
        .on_conflict_do_nothing(index_elements=[NotificationLog.delivery_key])
    )


async def _stage_scoped_preference_deliveries(
    db,
    *,
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    events: list[NotificationEventType],
    build_title_fn,
    metadata: dict,
    delivery_scope: str,
) -> None:
    """Load and stage default-channel deliveries without committing."""
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
    plans = _build_notification_plans(
        list(prefs_result.all()), events, build_title_fn, metadata
    )
    await _insert_scoped_notification_plans(
        db,
        project_id=project_id,
        run_id=run_id,
        delivery_scope=delivery_scope,
        metadata=metadata,
        plans=plans,
    )


async def stage_scoped_preference_deliveries(
    db,
    *,
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    events: list[NotificationEventType],
    build_title_fn,
    metadata: dict,
    delivery_scope: str,
) -> None:
    """Stage preference deliveries on a caller-owned transaction.

    Transition evaluation holds a project row lock while it advances the
    project-wide state machine. Reusing that session keeps the notification
    children and state stamp atomic and avoids a second transaction waiting on
    the caller's own PostgreSQL foreign-key lock.
    """
    await _stage_scoped_preference_deliveries(
        db,
        project_id=project_id,
        run_id=run_id,
        events=events,
        build_title_fn=build_title_fn,
        metadata=metadata,
        delivery_scope=delivery_scope,
    )


async def _load_and_notify(
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    events: list[NotificationEventType],
    build_title_fn,   # (event, pref) -> (title, body)
    metadata: dict,
    delivery_scope: Optional[str] = None,
) -> None:
    async with AsyncSessionLocal() as db:
        if delivery_scope:
            await _stage_scoped_preference_deliveries(
                db,
                project_id=project_id,
                run_id=run_id,
                events=events,
                build_title_fn=build_title_fn,
                metadata=metadata,
                delivery_scope=delivery_scope,
            )
            await db.commit()
            # The durable notification relay owns provider I/O and retries.
            return

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
        plans = _build_notification_plans(rows, events, build_title_fn, metadata)

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

        global_webhooks = None
        if any(pref.channel in (NotificationChannel.SLACK, NotificationChannel.TEAMS) for (pref, *_rest) in plans):
            from app.services.integration_config_service import resolve_global_notification_webhooks
            global_webhooks = await resolve_global_notification_webhooks(db)

        # Phase 2 — fan out all deliveries in parallel. A slow webhook no
        # longer blocks the next recipient. No coroutine here touches a DB
        # session, so they cannot race on a shared connection.
        results = await asyncio.gather(
            *(
                _dispatch_to_channel(
                    pref, user_email, title, body, event, metadata, smtp_cfg, global_webhooks,
                    delivery_id=None,
                )
                for (pref, user_email, event, title, body) in plans
            ),
            return_exceptions=False,
        )

        # Phase 3 — persist outcomes. Token fencing ensures a stale worker
        # cannot overwrite the result of a later lease owner.
        now_sent = datetime.now(timezone.utc)
        for (pref, _user_email, event, title, body), (status, error_detail) in zip(plans, results):
            db.add(NotificationLog(
                user_id=pref.user_id,
                project_id=project_id,
                run_id=run_id,
                delivery_key=None,
                channel=_notification_channel_value(pref.channel),
                event_type=event.value,
                title=title,
                body=body,
                status=status,
                error_detail=error_detail,
                sent_at=now_sent if status == "sent" else None,
            ))

        await db.commit()


async def stage_team_notification_deliveries(
    db,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    delivery_scope: str,
    deliveries: list[dict[str, Any]],
) -> None:
    """Persist ownership-routed provider deliveries for the shared relay.

    The target is snapshotted because a team-channel row may be changed or
    deleted before the relay runs. The snapshot is kept in the internal
    delivery metadata and is removed before provider dispatch. This function
    is stage-only; the transition evaluator commits the child intents and its
    state stamp atomically.
    """
    if not deliveries:
        return

    values: list[dict[str, Any]] = []
    for delivery in deliveries:
        channel = NotificationChannel(str(delivery["channel_type"]))
        event = NotificationEventType(str(delivery["event_type"]))
        team_name = str(delivery["team_name"])
        target = str(delivery["target"])
        if not team_name or not target:
            raise ValueError("team delivery requires a team_name and target")
        material = f"{delivery_scope}:team:{team_name}:{channel.value}"
        route_snapshot = {
            "team_name": team_name,
            "channel_type": channel.value,
            "target": target,
            "fallback": delivery.get("fallback"),
        }
        metadata = dict(delivery.get("metadata") or {})
        metadata[_TEAM_ROUTE_METADATA_KEY] = route_snapshot
        values.append(
            {
                "id": uuid.uuid4(),
                "user_id": None,
                "project_id": project_id,
                "run_id": run_id,
                "preference_id": None,
                "delivery_key": hashlib.sha256(material.encode("utf-8")).hexdigest(),
                "delivery_metadata": metadata,
                "channel": channel.value,
                "event_type": event.value,
                "title": str(delivery["title"]),
                "body": str(delivery["body"]),
                "status": "pending",
                "routed_team": team_name,
            }
        )

    await db.execute(
        pg_insert(NotificationLog)
        .values(values)
        .on_conflict_do_nothing(index_elements=[NotificationLog.delivery_key])
    )


async def stage_explicit_notification_deliveries(
    db,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    delivery_scope: str,
    deliveries: list[dict[str, Any]],
) -> None:
    """Stage direct, snapshotted provider targets for the durable relay."""
    if not deliveries:
        return

    values: list[dict[str, Any]] = []
    for delivery in deliveries:
        channel = NotificationChannel(str(delivery["channel_type"]))
        event = NotificationEventType(str(delivery["event_type"]))
        route_id = str(delivery["route_id"])
        target = str(delivery["target"])
        if not route_id or not target:
            raise ValueError("explicit delivery requires a route_id and target")
        material = f"{delivery_scope}:explicit:{route_id}:{channel.value}"
        route_snapshot = {
            "channel_type": channel.value,
            "target": target,
            "digest_subscription_id": delivery.get("digest_subscription_id"),
        }
        metadata = dict(delivery.get("metadata") or {})
        metadata[_EXPLICIT_ROUTE_METADATA_KEY] = route_snapshot
        values.append(
            {
                "id": uuid.uuid4(),
                "user_id": delivery.get("user_id"),
                "project_id": project_id,
                "run_id": run_id,
                "preference_id": None,
                "delivery_key": hashlib.sha256(material.encode("utf-8")).hexdigest(),
                "delivery_metadata": metadata,
                "channel": channel.value,
                "event_type": event.value,
                "title": str(delivery["title"]),
                "body": str(delivery["body"]),
                "status": "pending",
            }
        )

    await db.execute(
        pg_insert(NotificationLog)
        .values(values)
        .on_conflict_do_nothing(index_elements=[NotificationLog.delivery_key])
    )


async def claim_pending_notification_deliveries(
    db,
    *,
    limit: int = 200,
    lease_seconds: int = 300,
) -> list[NotificationLog]:
    """Lease durable notification children for provider delivery."""
    now = datetime.now(timezone.utc)
    bounded_limit = max(1, min(int(limit), 500))
    due_filter = and_(
        NotificationLog.delivery_key.is_not(None),
        or_(
            and_(
                NotificationLog.status == "pending",
                or_(
                    NotificationLog.next_delivery_at.is_(None),
                    NotificationLog.next_delivery_at <= now,
                ),
            ),
            and_(
                NotificationLog.status == "sending",
                NotificationLog.delivery_lease_expires_at <= now,
            ),
        ),
    )
    ranked_due = (
        select(
            NotificationLog.id.label("id"),
            func.row_number()
            .over(
                partition_by=NotificationLog.project_id,
                order_by=(NotificationLog.created_at, NotificationLog.id),
            )
            .label("tenant_rank"),
        )
        .where(due_filter)
        .cte("ranked_notification_due")
    )
    result = await db.execute(
        select(NotificationLog)
        .join(ranked_due, ranked_due.c.id == NotificationLog.id)
        .order_by(
            ranked_due.c.tenant_rank,
            NotificationLog.created_at,
            NotificationLog.id,
        )
        .with_for_update(skip_locked=True, of=NotificationLog)
        .limit(bounded_limit)
    )
    claimed: list[NotificationLog] = []
    for row in result.scalars().all():
        due = row.next_delivery_at is None or row.next_delivery_at <= now
        expired = (
            row.delivery_lease_expires_at is not None
            and row.delivery_lease_expires_at <= now
        )
        if not (
            (row.status == "pending" and due)
            or (row.status == "sending" and expired)
        ):
            continue
        if int(row.delivery_attempts or 0) >= _MAX_DURABLE_DELIVERY_ATTEMPTS:
            # Lease exhausted rows through the ordinary token-fenced outcome
            # path. Team routes must stage their default-channel fallback in
            # the same transaction that makes the team failure terminal; an
            # eager terminal write here would create a crash window between
            # those two durable facts.
            row.status = "sending"
            row.delivery_token = uuid.uuid4()
            row.delivery_started_at = now
            row.delivery_lease_expires_at = now + timedelta(
                seconds=max(30, min(int(lease_seconds), 900))
            )
            row.next_delivery_at = row.delivery_lease_expires_at
            row.error_detail = row.error_detail or "delivery_attempts_exhausted"
            setattr(row, "_relay_exhausted", True)
            claimed.append(row)
            continue
        row.status = "sending"
        row.delivery_attempts = int(row.delivery_attempts or 0) + 1
        row.delivery_token = uuid.uuid4()
        row.delivery_started_at = now
        row.delivery_lease_expires_at = now + timedelta(
            seconds=max(30, min(int(lease_seconds), 900))
        )
        row.next_delivery_at = row.delivery_lease_expires_at
        row.error_detail = None
        claimed.append(row)
    return claimed


async def relay_pending_notification_deliveries(
    *, limit: int = 200
) -> dict[str, int]:
    """Deliver due notification children with durable, token-fenced retries.

    External notification providers do not share an exactly-once API. The
    relay therefore guarantees at-least-once attempts and sends the stable
    delivery key as Message-ID/X-TestLookup-Delivery for correlation and
    receiver-side deduplication after an ambiguous worker crash.
    """
    async with AsyncSessionLocal() as db:
        rows = await claim_pending_notification_deliveries(db, limit=limit)
        await db.commit()
        if not rows:
            return {"claimed": 0, "sent": 0, "retrying": 0, "failed": 0}

        preference_ids = {
            row.preference_id for row in rows if row.preference_id is not None
        }
        preferences = {}
        if preference_ids:
            preference_result = await db.execute(
                select(NotificationPreference, User.email)
                .join(User, NotificationPreference.user_id == User.id)
                .where(NotificationPreference.id.in_(preference_ids))
            )
            preferences = {
                pref.id: (pref, user_email)
                for pref, user_email in preference_result.all()
            }
        team_routes = {
            row.id: dict(row.delivery_metadata or {}).get(_TEAM_ROUTE_METADATA_KEY)
            for row in rows
        }
        explicit_routes = {
            row.id: dict(row.delivery_metadata or {}).get(_EXPLICIT_ROUTE_METADATA_KEY)
            for row in rows
        }
        needs_email = any(
            (
                preferences.get(row.preference_id, (None, None))[0] is not None
                and preferences[row.preference_id][0].channel
                == NotificationChannel.EMAIL
            )
            or (
                isinstance(team_routes.get(row.id), dict)
                and team_routes[row.id].get("channel_type")
                == NotificationChannel.EMAIL.value
            )
            or (
                isinstance(explicit_routes.get(row.id), dict)
                and explicit_routes[row.id].get("channel_type")
                == NotificationChannel.EMAIL.value
            )
            for row in rows
        )
        needs_webhooks = any(
            preferences.get(row.preference_id, (None, None))[0] is not None
            and preferences[row.preference_id][0].channel
            in (NotificationChannel.SLACK, NotificationChannel.TEAMS)
            for row in rows
        )
        global_webhooks = None
        if needs_webhooks:
            from app.services.integration_config_service import (
                resolve_global_notification_webhooks,
            )

            global_webhooks = await resolve_global_notification_webhooks(db)

    smtp_cfg = await email_service._get_smtp_cfg() if needs_email else None

    async def _dispatch_snapshotted_route(
        row: NotificationLog,
        route: dict[str, Any],
    ) -> tuple[str, Optional[str]]:
        try:
            channel = NotificationChannel(str(route.get("channel_type")))
            target = str(route.get("target") or "")
            if not target:
                return "failed", "Notification delivery target is missing"
            event = NotificationEventType(row.event_type)
            metadata = dict(row.delivery_metadata or {})
            metadata.pop(_TEAM_ROUTE_METADATA_KEY, None)
            metadata.pop(_EXPLICIT_ROUTE_METADATA_KEY, None)
            if channel == NotificationChannel.EMAIL:
                if not (smtp_cfg or {}).get("enabled"):
                    return "failed", "SMTP is not configured — no email was sent"
                await email_service.send_notification(
                    to=target,
                    title=row.title,
                    body=row.body,
                    event_type=event.value,
                    metadata=metadata,
                    smtp_cfg=smtp_cfg,
                    delivery_id=row.delivery_key,
                )
            elif channel == NotificationChannel.SLACK:
                # A team route's webhook was set per project by a QA lead, and
                # an explicit route's target per subscription. Neither is the
                # deployment's own, so the allow-list does not cover them.
                await slack_service.send_notification(
                    webhook_url=target,
                    title=row.title,
                    body=row.body,
                    event_type=event.value,
                    metadata=metadata,
                    delivery_id=row.delivery_key,
                    deployment_wide=False,
                )
            elif channel == NotificationChannel.TEAMS:
                await teams_service.send_notification(
                    webhook_url=target,
                    title=row.title,
                    body=row.body,
                    event_type=event.value,
                    metadata=metadata,
                    delivery_id=row.delivery_key,
                    deployment_wide=False,
                )
            return "sent", None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Snapshotted notification delivery failed route=%s event=%s: %s",
                route.get("team_name"),
                row.event_type,
                exc,
            )
            return "failed", str(exc)

    async def _stage_team_fallback(
        db,
        row: NotificationLog,
        route: dict[str, Any],
    ) -> None:
        fallback = route.get("fallback")
        if not isinstance(fallback, dict):
            return
        event_members: list[NotificationEventType] = []
        for value in fallback.get("events") or []:
            try:
                event_members.append(NotificationEventType(str(value)))
            except ValueError:
                continue
        if not event_members or row.project_id is None:
            return
        title = str(fallback.get("title") or row.title)
        body = str(fallback.get("body") or row.body)
        fallback_scope = str(fallback.get("delivery_scope") or "")
        if not fallback_scope:
            return
        await _stage_scoped_preference_deliveries(
            db,
            project_id=row.project_id,
            run_id=row.run_id,
            events=event_members,
            build_title_fn=lambda _event: (title, body),
            metadata=dict(fallback.get("metadata") or {}),
            delivery_scope=fallback_scope,
        )

    async def _deliver_one(row: NotificationLog) -> tuple[str, Optional[str]]:
        if bool(getattr(row, "_relay_exhausted", False)):
            return "failed", row.error_detail or "delivery_attempts_exhausted"
        team_route = team_routes.get(row.id)
        if isinstance(team_route, dict):
            return await _dispatch_snapshotted_route(row, team_route)
        explicit_route = explicit_routes.get(row.id)
        if isinstance(explicit_route, dict):
            return await _dispatch_snapshotted_route(row, explicit_route)
        route = preferences.get(row.preference_id)
        if route is None:
            return "failed", "Notification preference no longer exists"
        pref, user_email = route
        try:
            event = NotificationEventType(row.event_type)
        except ValueError:
            return "failed", f"Unsupported notification event: {row.event_type}"
        return await _dispatch_to_channel(
            pref,
            user_email,
            row.title,
            row.body,
            event,
            dict(row.delivery_metadata or {}),
            smtp_cfg,
            global_webhooks,
            delivery_id=row.delivery_key,
        )

    results = await asyncio.gather(*(_deliver_one(row) for row in rows))
    now_done = datetime.now(timezone.utc)
    sent = retrying = failed = 0
    async with AsyncSessionLocal() as db:
        for row, (status, error_detail) in zip(rows, results):
            attempts = int(row.delivery_attempts or 0)
            team_route = team_routes.get(row.id)
            is_team_route = isinstance(team_route, dict)
            explicit_route = explicit_routes.get(row.id)
            if status == "sent":
                values = {
                    "status": "sent",
                    "sent_at": now_done,
                    "error_detail": None,
                    "next_delivery_at": None,
                }
            elif attempts >= _MAX_DURABLE_DELIVERY_ATTEMPTS:
                values = {
                    "status": "failed",
                    "error_detail": error_detail or "delivery_attempts_exhausted",
                    "next_delivery_at": None,
                }
                if is_team_route:
                    values["routing_fallback"] = "delivery_failed"
            else:
                values = {
                    "status": "pending",
                    "error_detail": error_detail,
                    "next_delivery_at": now_done + timedelta(
                        seconds=min(900, 2 ** min(attempts, 9))
                    ),
                }
            values.update(
                {
                    "delivery_token": None,
                    "delivery_lease_expires_at": None,
                }
            )
            outcome = await db.execute(
                update(NotificationLog)
                .where(
                    NotificationLog.id == row.id,
                    NotificationLog.status == "sending",
                    NotificationLog.delivery_token == row.delivery_token,
                )
                .values(**values)
            )
            if int(getattr(outcome, "rowcount", 0) or 0) != 1:
                continue
            if status == "sent":
                sent += 1
                if isinstance(explicit_route, dict):
                    raw_subscription_id = explicit_route.get("digest_subscription_id")
                    if raw_subscription_id:
                        await db.execute(
                            update(DigestSubscription)
                            .where(DigestSubscription.id == uuid.UUID(str(raw_subscription_id)))
                            .values(
                                delivery_count=DigestSubscription.delivery_count + 1,
                                last_delivered_at=now_done,
                            )
                        )
            elif attempts >= _MAX_DURABLE_DELIVERY_ATTEMPTS:
                failed += 1
                if is_team_route:
                    await _stage_team_fallback(db, row, team_route)
            else:
                retrying += 1
        await db.commit()
    return {
        "claimed": len(rows),
        "sent": sent,
        "retrying": retrying,
        "failed": failed,
    }


# ── Public entry points ───────────────────────────────────────

async def _per_run_events_enabled(project_id: uuid.UUID) -> bool:
    """Per-project gate for the legacy per-run fan-out (PMF US-7.1).

    Projects created after migration 0103 default to "per-run spam mode
    off" — only transition events notify. Every project existing at
    migration time was backfilled with an explicit per_run_events_enabled=
    True row, so their behaviour is unchanged. Fails OPEN (True) on any
    lookup error so a policy-table hiccup can never silence notifications
    for legacy installs.
    """
    try:
        async with AsyncSessionLocal() as db:
            from app.services.notification_transitions import get_effective_policy
            policy = await get_effective_policy(db, project_id)
            return bool(policy.per_run_events_enabled)
    except Exception as exc:  # noqa: BLE001 — fail open to legacy behaviour
        logger.warning(
            "Per-run notification policy lookup failed for project=%s: %s — defaulting to enabled",
            project_id,
            exc,
        )
        return True


async def dispatch_run_notifications(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    build_number: str,
    pass_rate: float,
    total_tests: int,
    failed_tests: int,
    project_name: str,
    dashboard_url: str = "#",
    delivery_scope: Optional[str] = None,
) -> None:
    """
    Evaluate which run-level events apply and send to all subscribed users.
    Called from the `dispatch_run_notifications` Celery task.
    """
    # Per-project policy gate (PMF US-7.1): new projects default to
    # transition-only notifications; the per-run fan-out is suppressed.
    if not await _per_run_events_enabled(project_id):
        logger.info(
            "Per-run notifications suppressed by transition policy project=%s build=%s",
            project_id,
            build_number,
        )
        return

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

    await _load_and_notify(
        project_id,
        run_id,
        events,
        _msg,
        meta,
        delivery_scope=delivery_scope,
    )
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
    delivery_scope: Optional[str] = None,
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

    await _load_and_notify(
        project_id,
        run_id,
        events,
        _msg,
        meta,
        delivery_scope=delivery_scope,
    )


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
