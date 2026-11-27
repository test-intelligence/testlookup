"""SQL outbox outcomes, independent of broker depth and Celery SUCCESS."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, case, func, select

from app.core.metrics import (
    downstream_outbox_entries,
    downstream_outbox_oldest_age_seconds,
    downstream_outbox_overdue_seconds,
)

STATUSES = ("waiting", "pending", "sending", "published", "processing", "completed", "failed")
LEASE_STATUSES = ("sending", "published", "processing")


def _age(value: datetime | None, now: datetime) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds())


async def refresh_downstream_operational_metrics(*, now: datetime | None = None) -> bool:
    """Publish one complete snapshot only after its grouped query succeeds.

    next_attempt_at and lease_expires_at already encode the relay's actual
    backoff/lease contract. No payload, run, project or task ID is a label.
    Call through collect_bounded to enforce the scrape deadline/freshness.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import RunDownstreamOutbox
    from app.services.run_downstream_outbox import _OPERATIONS

    operations = tuple(sorted(_OPERATIONS)) + ("other",)
    observed_at = now or datetime.now(timezone.utc)
    due = case(
        (
            RunDownstreamOutbox.status.in_(("waiting", "pending")),
            func.coalesce(RunDownstreamOutbox.next_attempt_at, RunDownstreamOutbox.created_at),
        ),
        (RunDownstreamOutbox.status.in_(LEASE_STATUSES), RunDownstreamOutbox.lease_expires_at),
        else_=None,
    )
    missing_lease = and_(
        RunDownstreamOutbox.status.in_(LEASE_STATUSES),
        RunDownstreamOutbox.lease_expires_at.is_(None),
    )
    statement = select(
        RunDownstreamOutbox.operation,
        RunDownstreamOutbox.status,
        func.count().label("count"),
        func.min(RunDownstreamOutbox.created_at).label("oldest"),
        func.min(due).label("due"),
        func.sum(case((missing_lease, 1), else_=0)).label("missing_lease_count"),
    ).group_by(RunDownstreamOutbox.operation, RunDownstreamOutbox.status)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(statement)).all()

    snapshot: dict[tuple[str, str], tuple[int, float, float]] = {}
    for operation, status, count, oldest, deadline, missing_lease_count in rows:
        if status not in STATUSES:
            raise ValueError("unknown downstream lifecycle state")
        if missing_lease_count:
            # The relay cannot recover a lease state with a NULL expiry.
            # Do not turn malformed/legacy state into a fresh healthy zero.
            raise ValueError("downstream lease state has no lease deadline")
        operation = operation if operation in _OPERATIONS else "other"
        key = (operation, status)
        previous = snapshot.get(key, (0, 0.0, 0.0))
        age = 0.0 if status == "completed" else _age(oldest, observed_at)
        overdue = 0.0 if status in {"completed", "failed"} else _age(deadline, observed_at)
        snapshot[key] = (previous[0] + count, max(previous[1], age), max(previous[2], overdue))
    for operation in operations:
        for status in STATUSES:
            count, age, overdue = snapshot.get((operation, status), (0, 0.0, 0.0))
            downstream_outbox_entries.labels(operation=operation, status=status).set(count)
            downstream_outbox_oldest_age_seconds.labels(operation=operation, status=status).set(age)
            downstream_outbox_overdue_seconds.labels(operation=operation, status=status).set(overdue)
    return True
