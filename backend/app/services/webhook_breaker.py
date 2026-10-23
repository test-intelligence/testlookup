"""Outbound webhook circuit breaker (W10 INT-07, decision D-9).

A subscription is paused after ``breaker_threshold`` deliveries IN A ROW end in
a terminal failure attributable to it. What counts, once per delivery:

* a delivery that reaches ``FAILED`` or ``DLQ`` WITH the subscription's
  bookkeeping -- the receiver's 4xx (``FAILED``), the retries spent on a
  5xx / 429 / network error / timeout (``DLQ``), the egress guard refusing the
  target (``FAILED``: a mistyped private URL trips quickly, and the page says a
  refused target counts) and a configured signing secret that cannot be read
  (``FAILED``).

What does NOT count:

* a retried attempt (``PENDING`` with retry budget left): a timeout is not a
  failure until its retries are spent;
* "subscription missing or disabled" and "webhooks disabled or offline mode"
  (not the receiver's fault; no subscription bookkeeping is written);
* the relay's "dispatch attempts exhausted" (the broker's fault).

A ``SUCCESS`` resets the run to 0. ``failure_count`` keeps counting ATTEMPTS,
as before.

The trip is ONE guarded ``UPDATE ... RETURNING`` inside the delivery's own
transaction (``webhook_service._transition_processing_delivery``), right after
the increment whose ``UPDATE`` holds the row lock: concurrent workers are
serialised by that lock, and the second one sees ``enabled = false`` and
matches nothing -- exactly one trip. The trip stages, in the same transaction
(inside a SAVEPOINT, so a notification never fails the delivery's
bookkeeping), the ``webhook.circuit_opened`` activity row (system actor) and
one email notification per recipient (the subscription's last editor and the
project's QA leads). The counter ``webhook_circuit_opened_total`` rises after
the commit only.

This is the ONLY module that records activity for webhooks (§0.23 of the W10
contract): ``webhook_service`` calls it and carries no recording text of its
own, and no router imports it, so ``backend.activity-coverage`` does not read
the webhook CRUD handlers as recording. Commit-free: the delivery transaction
commits.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ProjectMember, User, UserRole, WebhookSubscription
from app.services.activity.service import ActorRef, record

logger = structlog.get_logger("services.webhook_breaker")

#: A new subscription pauses after this many consecutive failed deliveries.
DEFAULT_BREAKER_THRESHOLD = 10
#: The delivery statuses a counted failure ends in.
TERMINAL_FAILURE_STATUSES = ("FAILED", "DLQ")
#: ``WebhookSubscription.disabled_reason``: paused by the breaker, or by a person.
WEBHOOK_DISABLED_REASONS = ("circuit_open", "manual")
CIRCUIT_OPEN: Final = WEBHOOK_DISABLED_REASONS[0]
MANUAL: Final = WEBHOOK_DISABLED_REASONS[1]

#: The body's last sentence (the page is in-app, so no link is sent).
REENABLE_HINT = "Fix the receiver, then Re-enable it in Settings › Outbound webhooks."


@dataclass(frozen=True)
class BreakerTrip:
    """What the trip's guarded UPDATE returned, as plain values."""

    subscription_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    consecutive_failures: int
    breaker_threshold: int
    last_error: Optional[str]
    updated_by_user_id: Optional[uuid.UUID]
    disabled_at: datetime


def terminal_failure_values(error: str, now: datetime) -> dict[str, Any]:
    """The subscription bookkeeping of a delivery that ENDED failed: counted."""
    return {
        "last_failure_at": now,
        "last_error": error[:2000],
        "failure_count": WebhookSubscription.failure_count + 1,
        "consecutive_failures": WebhookSubscription.consecutive_failures + 1,
    }


def retry_failure_values(error: str, now: datetime) -> dict[str, Any]:
    """The bookkeeping of an attempt that will be retried: not counted."""
    return {
        "last_failure_at": now,
        "last_error": error[:2000],
        "failure_count": WebhookSubscription.failure_count + 1,
    }


#: Merged into a SUCCESS's subscription values: the run of failures is over.
SUCCESS_RESET: Final = {"consecutive_failures": 0}


def reenable_values() -> dict[str, Any]:
    """A person's Re-enable: the count starts again and the pause is forgotten."""
    return {"consecutive_failures": 0, "disabled_reason": None, "disabled_at": None}


async def trip_if_due(db: AsyncSession, subscription_id: uuid.UUID, *, now: datetime) -> Optional[BreakerTrip]:
    """Pause the subscription when its consecutive failures reached its threshold.

    ONE guarded statement in the delivery's transaction, after the increment::

        UPDATE webhook_subscriptions
           SET enabled = false, disabled_reason = 'circuit_open',
               disabled_at = :now, updated_at = :now
         WHERE id = :id AND enabled IS TRUE
           AND consecutive_failures >= breaker_threshold
        RETURNING webhook_subscriptions.*

    The row lock the increment took serialises concurrent workers: the second
    one sees ``enabled = false`` and matches nothing, so a subscription trips
    exactly once. ``None`` when nothing tripped."""
    result = await db.execute(
        update(WebhookSubscription)
        .where(
            WebhookSubscription.id == subscription_id,
            WebhookSubscription.enabled.is_(True),
            WebhookSubscription.consecutive_failures >= WebhookSubscription.breaker_threshold,
        )
        .values(enabled=False, disabled_reason=CIRCUIT_OPEN, disabled_at=now, updated_at=now)
        .returning(WebhookSubscription)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return BreakerTrip(
        subscription_id=row.id,
        project_id=row.project_id,
        name=row.name,
        consecutive_failures=int(row.consecutive_failures or 0),
        breaker_threshold=int(row.breaker_threshold or DEFAULT_BREAKER_THRESHOLD),
        last_error=row.last_error,
        updated_by_user_id=row.updated_by_user_id,
        disabled_at=now,
    )


async def _recipients(db: AsyncSession, trip: BreakerTrip) -> list[tuple[uuid.UUID, str]]:
    """The last editor (an active user) and the project's active QA leads,
    de-duplicated, each with an email address."""
    found: dict[uuid.UUID, str] = {}
    if trip.updated_by_user_id is not None:
        editor = (
            await db.execute(
                select(User.id, User.email).where(
                    User.id == trip.updated_by_user_id, User.is_active.is_(True),
                )
            )
        ).first()
        if editor is not None and editor[1]:
            found[editor[0]] = editor[1]
    leads = (
        await db.execute(
            select(User.id, User.email)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                ProjectMember.project_id == trip.project_id,
                ProjectMember.role == UserRole.QA_LEAD.value,
                User.is_active.is_(True),
            )
            .order_by(User.email)
        )
    ).all()
    for user_id, email in leads:
        if email and user_id not in found:
            found[user_id] = email
    return list(found.items())


def _summary(trip: BreakerTrip) -> str:
    return f"Webhook {trip.name} paused after {trip.consecutive_failures} consecutive failures"


async def stage_circuit_opened(db: AsyncSession, trip: BreakerTrip) -> None:
    """Stage the trip's activity row and notifications in the delivery's transaction.

    Inside a SAVEPOINT: any error is logged once and swallowed, and the
    savepoint takes the half-staged rows with it -- a notification never fails
    the delivery's bookkeeping (the pause itself is already staged, outside)."""
    from app.services.notification import manager

    try:
        async with db.begin_nested():
            await record(
                db,
                project_id=trip.project_id,
                event_type="webhook.circuit_opened",
                actor=ActorRef.system("webhook_breaker"),
                entity_id=trip.subscription_id,
                entity_label=trip.name,
                context={
                    "count": trip.consecutive_failures,
                    "threshold": trip.breaker_threshold,
                    "error": (trip.last_error or "")[:200],
                },
            )
            # The session does not autoflush: write the row inside the
            # savepoint so a failure below takes it back too.
            await db.flush()
            recipients = await _recipients(db, trip)
            body = "\n\n".join(part for part in ((trip.last_error or "").strip()[:500], REENABLE_HINT) if part)
            await manager.stage_explicit_notification_deliveries(
                db,
                project_id=trip.project_id,
                run_id=None,
                delivery_scope=f"webhook-circuit:{trip.subscription_id}:{trip.disabled_at.isoformat()}:v1",
                deliveries=[
                    {
                        "channel_type": "email",
                        "event_type": "webhook.circuit_opened",
                        "route_id": f"user:{user_id}",
                        "target": email,
                        "user_id": user_id,
                        "title": _summary(trip),
                        "body": body,
                    }
                    for user_id, email in recipients
                ],
            )
    except Exception as exc:  # noqa: BLE001 -- never fail the delivery's bookkeeping
        logger.warning(
            "webhook_circuit_notification_dropped",
            subscription_id=str(trip.subscription_id),
            project_id=str(trip.project_id),
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )


def count_trip() -> None:
    """One trip on the dashboard, AFTER the commit (never an uncommitted one)."""
    from app.core.metrics import webhook_circuit_opened_total

    webhook_circuit_opened_total.inc()
