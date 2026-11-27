"""Shared project-scoped event health and bounded fleet metric snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.models.postgres import PipelineEventOutbox
from app.services.pipeline_event_admission import PIPELINE_EVENT_CAPACITY
from app.services.pipeline_event_delivery import STATUSES


def _age(value: datetime | None, now: datetime) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds())


def _scope(statement, project_ids: set[uuid.UUID] | None):
    return statement if project_ids is None else statement.where(PipelineEventOutbox.project_id.in_(project_ids))


async def read_event_delivery_snapshot(
    db: AsyncSession, *, project_ids: set[uuid.UUID] | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("event snapshot time must include timezone")
    due = case(
        (PipelineEventOutbox.status == "pending", func.coalesce(PipelineEventOutbox.next_attempt_at, PipelineEventOutbox.created_at)),
        (PipelineEventOutbox.status == "sending", PipelineEventOutbox.lease_expires_at),
        else_=None,
    )
    statement = select(
        PipelineEventOutbox.status, func.count(), func.min(PipelineEventOutbox.created_at), func.min(due),
        func.sum(case((PipelineEventOutbox.last_error_code.is_not(None), 1), else_=0)),
        func.sum(case(((PipelineEventOutbox.status == "sending") & PipelineEventOutbox.lease_expires_at.is_(None), 1), else_=0)),
        func.sum(case(((PipelineEventOutbox.status == "delivered") & PipelineEventOutbox.delivered_at.is_(None), 1), else_=0)),
    ).group_by(PipelineEventOutbox.status)
    rows = (await db.execute(_scope(statement, project_ids))).all()
    snapshot = {state: {"count": 0, "oldest_age_seconds": 0.0, "overdue_seconds": 0.0} for state in STATUSES}
    unresolved_errors = 0
    for state, count, oldest, deadline, errors, missing_lease, missing_receipt in rows:
        if state not in STATUSES or missing_lease or missing_receipt:
            raise ValueError("invalid event delivery lifecycle")
        snapshot[state] = {
            "count": int(count),
            "oldest_age_seconds": 0.0 if state == "delivered" else _age(oldest, observed_at),
            "overdue_seconds": _age(deadline, observed_at),
        }
        if state != "delivered":
            unresolved_errors += int(errors)
    counts = {state: int(value["count"]) for state, value in snapshot.items()}
    backlog = sum(counts[state] for state in ("pending", "sending", "failed"))
    # Legacy count names remain compatibility aliases for current shared
    # unresolved failures, not process-lifetime monotonic counters.
    return {
        "status": "degraded" if counts["failed"] or unresolved_errors or max(
            float(value["oldest_age_seconds"]) for value in snapshot.values()
        ) > 300 else "healthy",
        "write_failure_count": unresolved_errors,
        "dead_letter_count": counts["failed"],
        "dead_letter_limit": PIPELINE_EVENT_CAPACITY,
        "recent_dead_letters": [],
        "backlog_count": backlog,
        "oldest_pending_age_seconds": max(float(value["oldest_age_seconds"]) for value in snapshot.values()),
        "outbox_total_count": sum(counts.values()),
        "capacity": PIPELINE_EVENT_CAPACITY,
        "status_counts": counts,
        "states": snapshot,
    }


async def read_event_log_health(
    db: AsyncSession, *, project_ids: set[uuid.UUID] | None,
) -> dict[str, Any]:
    snapshot = await read_event_delivery_snapshot(db, project_ids=project_ids)
    statement = select(
        PipelineEventOutbox.id, PipelineEventOutbox.pipeline_run_id,
        PipelineEventOutbox.attempts, PipelineEventOutbox.last_error_code,
    ).where(PipelineEventOutbox.status == "failed").order_by(PipelineEventOutbox.created_at).limit(20)
    rows = (await db.execute(_scope(statement, project_ids))).all()
    snapshot["recent_dead_letters"] = [{
        "event_id": str(identifier), "pipeline_run_id": pipeline,
        "attempts": int(attempts), "error_code": error,
    } for identifier, pipeline, attempts, error in rows]
    snapshot.pop("states")
    return snapshot


async def refresh_pipeline_event_operational_metrics() -> bool:
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        snapshot = await read_event_delivery_snapshot(db)
    for state, values in snapshot["states"].items():
        metrics.pipeline_event_outbox_entries.labels(status=state).set(values["count"])
        metrics.pipeline_event_outbox_oldest_age.labels(status=state).set(values["oldest_age_seconds"])
        metrics.pipeline_event_outbox_overdue.labels(status=state).set(values["overdue_seconds"])
    metrics.pipeline_event_outbox_capacity.set(snapshot["capacity"])
    return True
