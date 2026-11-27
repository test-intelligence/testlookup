"""Due-aware weekly delivery snapshots from the persisted subscription state."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select

from app.core.metrics import (
    weekly_digest_delivery_outcomes,
    weekly_digest_oldest_overdue_seconds,
    weekly_digest_subscriptions,
)
from app.services.digest_delivery_observation import OUTCOMES

SCHEDULES = ("WEEKLY", "WEEKLY_RETRO")
STATES = ("enabled", "due", "unresolved", "never_delivered", "missing_schedule")
# next_delivery_at can fall immediately after the daily 07:00 UTC dispatch.
# Allow the next daily pass (24h) plus one hour of processing before overdue.
DELIVERY_GRACE_SECONDS = 25 * 60 * 60


def _age(value: datetime | None, now: datetime) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # legacy database UTC
    return max(0.0, (now - value).total_seconds())


async def refresh_digest_operational_metrics(*, now: datetime | None = None) -> bool:
    """Publish only a complete bounded snapshot; collect_bounded owns freshness.

    Generation and provider completion are different persisted facts. Pending
    claim receipts keep due work visible even after next_delivery_at advances,
    across retries and worker replacement. No due work means measured zeros.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import DigestSubscription as Subscription

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("digest observation time must include timezone")
    observed_at = observed_at.astimezone(timezone.utc)
    due = func.coalesce(Subscription.pending_delivery_since_at, Subscription.next_delivery_at)
    outcome = func.coalesce(Subscription.last_delivery_outcome, "not_observed")
    statement = select(
        Subscription.schedule, outcome.label("outcome"), func.count(),
        func.sum(case((due <= observed_at, 1), else_=0)),
        func.sum(case((Subscription.pending_delivery_since_at.is_not(None), 1), else_=0)),
        func.sum(case((Subscription.last_delivered_at.is_(None), 1), else_=0)),
        func.sum(case((due.is_(None), 1), else_=0)),
        func.min(due),
    ).where(
        Subscription.schedule.in_(SCHEDULES), Subscription.is_active.is_(True),
        Subscription.is_paused.is_(False),
    ).group_by(Subscription.schedule, outcome)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(statement)).all()

    snapshot = {schedule: {state: 0 for state in STATES} for schedule in SCHEDULES}
    outcomes = {(schedule, state): 0 for schedule in SCHEDULES for state in OUTCOMES}
    overdue = dict.fromkeys(SCHEDULES, 0.0)
    for schedule, state, count, due_count, unresolved, never, missing, oldest in rows:
        if schedule not in SCHEDULES or state not in OUTCOMES:
            raise ValueError("unknown digest observation state")
        outcomes[(schedule, state)] += count
        for name, value in zip(STATES, (count, due_count, unresolved, never, missing)):
            snapshot[schedule][name] += int(value)
        overdue[schedule] = max(overdue[schedule], _age(oldest, observed_at) - DELIVERY_GRACE_SECONDS)
    for schedule in SCHEDULES:
        for state in STATES:
            weekly_digest_subscriptions.labels(schedule=schedule, state=state).set(snapshot[schedule][state])
        weekly_digest_oldest_overdue_seconds.labels(schedule=schedule).set(overdue[schedule])
        for state in OUTCOMES:
            weekly_digest_delivery_outcomes.labels(schedule=schedule, outcome=state).set(outcomes[(schedule, state)])
    return True
