"""Durable worker observations and read-only API scrape snapshots."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.worker.nightly_schedule import (
    NIGHTLY_SCHEDULES, NIGHTLY_SUCCESS_GRACE, NightlySchedule, latest_due, read_nightly_slot,
)

logger = structlog.get_logger(__name__)


async def stage_nightly_activation(
    db: AsyncSession, flag_key: str, *, was_enabled: bool, is_enabled: bool, at: datetime,
) -> None:
    """Stage a true off-to-on transition in the feature flag transaction."""
    if was_enabled or not is_enabled:
        return
    for schedule in NIGHTLY_SCHEDULES:
        if schedule.flag == flag_key:
            from app.models.postgres import NightlyJobObservation as Observation

            await db.execute(
                update(Observation).where(Observation.schedule_key == schedule.key).values(enabled_since=at)
            )
            return


async def _record_attempt(schedule: NightlySchedule, slot: datetime, started: datetime) -> bool:
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import NightlyJobObservation as Observation
    from app.services.feature_flags import is_enabled

    async with AsyncSessionLocal() as db:
        enabled = await is_enabled(schedule.flag, db=db, any_scope=True)
        await db.execute(
            update(Observation).where(
                Observation.schedule_key == schedule.key,
                or_(
                    Observation.last_attempt_slot_at.is_(None),
                    Observation.last_attempt_slot_at < slot,
                    and_(Observation.last_attempt_slot_at == slot, Observation.last_attempt_started_at < started),
                ),
            ).values(last_attempt_slot_at=slot, last_attempt_started_at=started)
        )
        await db.commit()
        return enabled


async def _record_success(schedule: NightlySchedule, slot: datetime, finished: datetime) -> None:
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import NightlyJobObservation as Observation

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Observation).where(
                Observation.schedule_key == schedule.key,
                or_(
                    Observation.last_success_slot_at.is_(None),
                    Observation.last_success_slot_at < slot,
                    and_(Observation.last_success_slot_at == slot, Observation.last_success_finished_at < finished),
                ),
            ).values(last_success_slot_at=slot, last_success_finished_at=finished)
        )
        await db.commit()


async def observe_nightly_job(
    schedule: NightlySchedule,
    headers: object,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Preserve the job's outcome even if its observation cannot be persisted."""
    started = datetime.now(timezone.utc)
    slot = read_nightly_slot(schedule, headers, started)
    enabled = False
    if slot is not None:
        try:
            enabled = await _record_attempt(schedule, slot, started)
        except Exception as exc:  # noqa: BLE001 -- a failed observation must not stop maintenance
            logger.warning("nightly_job_attempt_record_failed", schedule=schedule.key, error_type=type(exc).__name__)
    result = await operation()
    if slot is not None and enabled and not result.get("skipped"):
        try:
            await _record_success(schedule, slot, datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 -- preserve a completed job's result
            logger.warning("nightly_job_success_record_failed", schedule=schedule.key, error_type=type(exc).__name__)
    return result


def _utc(value: datetime) -> datetime:
    # Some test/embedded database drivers return naive UTC timestamps.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _metric_timestamp(value: datetime | None) -> float:
    return _utc(value).timestamp() if value is not None else 0.0


async def refresh_nightly_job_metrics(*, now: datetime | None = None) -> bool:
    """Compute schedule expectations from SQL-backed history and flag state."""
    from app.core.metrics import (
        nightly_job_enabled, nightly_job_last_attempt_timestamp, nightly_job_last_success_timestamp,
        nightly_job_next_due_timestamp, nightly_job_overdue, nightly_job_started_overdue_slot,
    )
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import FeatureFlag, NightlyJobObservation as Observation

    observed_at = _utc(now) if now is not None else datetime.now(timezone.utc)
    flag_by_schedule = case({schedule.key: schedule.flag for schedule in NIGHTLY_SCHEDULES}, value=Observation.schedule_key)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Observation, FeatureFlag.enabled_global, FeatureFlag.rollout_percent)
            .outerjoin(FeatureFlag, FeatureFlag.key == flag_by_schedule)
            .where(Observation.schedule_key.in_([schedule.key for schedule in NIGHTLY_SCHEDULES]))
        )).all()

    observations = {row.schedule_key: (row, bool(on and rollout and rollout > 0)) for row, on, rollout in rows}
    if any(schedule.key not in observations for schedule in NIGHTLY_SCHEDULES):
        raise RuntimeError("nightly job observation rows are missing")

    for schedule in NIGHTLY_SCHEDULES:
        state, enabled = observations[schedule.key]
        active_since = _utc(state.monitoring_since)
        if state.enabled_since is not None:
            active_since = max(active_since, _utc(state.enabled_since))
        # Keep yesterday's unresolved deadline visible through today's grace
        # window, and only compare outcomes for the actual broker-stamped slot.
        deadline_slot = latest_due(schedule, observed_at - NIGHTLY_SUCCESS_GRACE)
        success_slot = state.last_success_slot_at
        overdue = enabled and deadline_slot >= active_since and (
            success_slot is None or _utc(success_slot) < deadline_slot
        )
        attempt_slot = state.last_attempt_slot_at
        started = overdue and attempt_slot is not None and _utc(attempt_slot) >= deadline_slot
        labels = {"schedule": schedule.key}
        nightly_job_enabled.labels(**labels).set(int(enabled))
        nightly_job_next_due_timestamp.labels(**labels).set(
            (latest_due(schedule, observed_at) + timedelta(days=1)).timestamp() if enabled else 0
        )
        nightly_job_last_attempt_timestamp.labels(**labels).set(_metric_timestamp(state.last_attempt_started_at))
        nightly_job_last_success_timestamp.labels(**labels).set(_metric_timestamp(state.last_success_finished_at))
        nightly_job_overdue.labels(**labels).set(int(overdue))
        nightly_job_started_overdue_slot.labels(**labels).set(int(started))
    return True
