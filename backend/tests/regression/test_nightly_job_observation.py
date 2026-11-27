"""Scheduled maintenance evidence survives missing and out-of-order workers."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import metrics
from app.db import postgres
from app.models.postgres import NightlyJobObservation
from app.services import feature_flags, nightly_job_observation as observations
from app.worker.nightly_schedule import (
    PERF_BASELINE_REFRESH, QUARANTINE_MAINTENANCE, SCHEDULE_HEADER, SLOT_HEADER, latest_due, read_nightly_slot,
)

SLOT = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", factory)
    async with engine.begin() as db:
        await db.run_sync(NightlyJobObservation.__table__.create)
        await db.execute(text(
            "CREATE TABLE feature_flags (key TEXT PRIMARY KEY, enabled_global BOOL, "
            "rollout_percent INTEGER, updated_at DATETIME)"
        ))
    async with factory.begin() as db:
        for schedule in (QUARANTINE_MAINTENANCE, PERF_BASELINE_REFRESH):
            active_since = SLOT - timedelta(days=3)
            db.add(NightlyJobObservation(schedule_key=schedule.key, monitoring_since=active_since))
            await db.execute(
                text("INSERT INTO feature_flags VALUES (:key, 1, 100, :updated)"),
                {"key": schedule.flag, "updated": active_since},
            )
    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=True))
    try:
        yield factory
    finally:
        await engine.dispose()


def _quarantine_sample(metric):
    return metric.labels(schedule=QUARANTINE_MAINTENANCE.key)._value.get()


def test_beat_publishes_the_right_slot_and_manual_jobs_do_not_get_it():
    from celery.signals import before_task_publish
    from app.worker.celery_app import celery_app

    schedule = QUARANTINE_MAINTENANCE
    entry = celery_app.conf.beat_schedule["nightly-flaky-quarantine-maintenance"]
    assert entry["task"] == schedule.task_name
    assert entry["schedule"].hour == {4} and entry["schedule"].minute == {0}
    headers = dict(entry["options"]["headers"])
    before_task_publish.send(sender=schedule.task_name, headers=headers)
    now = datetime.now(timezone.utc)
    slot = read_nightly_slot(schedule, headers, now)
    assert slot == latest_due(schedule, now)
    # A queued message that waits until tomorrow still belongs to this slot.
    assert read_nightly_slot(schedule, headers, now + timedelta(days=1)) == slot
    manual = {}
    before_task_publish.send(sender=schedule.task_name, headers=manual)
    assert SLOT_HEADER not in manual
    wrong_sender = {SCHEDULE_HEADER: schedule.key}
    before_task_publish.send(sender=PERF_BASELINE_REFRESH.task_name, headers=wrong_sender)
    assert SLOT_HEADER not in wrong_sender


def test_celery_serializes_a_fresh_slot_per_dispatch(monkeypatch):
    from kombu import Connection, Exchange, Producer, Queue
    from app.worker import nightly_schedule
    from app.worker.celery_app import celery_app

    class Clock(datetime):
        value = SLOT

        @classmethod
        def now(cls, tz=None):
            return cls.value

    monkeypatch.setattr(nightly_schedule, "datetime", Clock)
    schedule = QUARANTINE_MAINTENANCE
    entry = celery_app.conf.beat_schedule["nightly-flaky-quarantine-maintenance"]
    headers = entry["options"]["headers"]
    fixture = "nightly-slot-fixture"
    queue = Queue(fixture, Exchange(fixture), routing_key=fixture)
    with Connection("memory://") as broker:
        consumer = queue(broker)
        with Producer(broker) as producer:
            for day in (0, 1):
                Clock.value = SLOT + timedelta(days=day)
                message = celery_app.amqp.create_task_message(f"nightly-fixture-{day}", schedule.task_name)
                celery_app.amqp.send_task_message(producer, schedule.task_name, message, queue=queue, headers=headers)
                delivered = consumer.get(no_ack=True)
                assert delivered.headers[SLOT_HEADER] == int(Clock.value.timestamp())
                assert SLOT_HEADER not in headers
        consumer.delete()


async def test_never_run_deadline_started_failure_and_recovery_across_sessions(session):
    schedule = QUARANTINE_MAINTENANCE
    # The prior slot succeeded. Before today's grace runs out no failure fires.
    yesterday = SLOT - timedelta(days=1)
    await observations._record_success(schedule, yesterday, yesterday + timedelta(minutes=10))
    assert await observations.refresh_nightly_job_metrics(now=SLOT + timedelta(minutes=59))
    assert _quarantine_sample(metrics.nightly_job_overdue) == 0
    assert _quarantine_sample(metrics.nightly_job_next_due_timestamp) == (SLOT + timedelta(days=1)).timestamp()

    assert await observations.refresh_nightly_job_metrics(now=SLOT + timedelta(hours=1))
    assert _quarantine_sample(metrics.nightly_job_overdue) == 1
    assert _quarantine_sample(metrics.nightly_job_started_overdue_slot) == 0
    # Separate commit from a worker whose business operation failed. Merely
    # starting a task must not satisfy the durable completion requirement.
    await observations._record_attempt(schedule, SLOT, SLOT + timedelta(minutes=15))
    await observations.refresh_nightly_job_metrics(now=SLOT + timedelta(hours=1, minutes=1))
    assert _quarantine_sample(metrics.nightly_job_overdue) == 1
    assert _quarantine_sample(metrics.nightly_job_started_overdue_slot) == 1
    assert _quarantine_sample(metrics.nightly_job_last_attempt_timestamp) == (SLOT + timedelta(minutes=15)).timestamp()

    await observations._record_success(schedule, SLOT, SLOT + timedelta(hours=1, minutes=2))
    # A delayed prior-day delivery cannot overwrite the newer success.
    await observations._record_success(schedule, yesterday, SLOT + timedelta(hours=1, minutes=3))
    await observations.refresh_nightly_job_metrics(now=SLOT + timedelta(hours=1, minutes=5))
    assert _quarantine_sample(metrics.nightly_job_overdue) == 0
    assert _quarantine_sample(metrics.nightly_job_last_success_timestamp) == (SLOT + timedelta(hours=1, minutes=2)).timestamp()


async def test_upgrade_boundary_and_disabled_flag_do_not_invent_missed_runs(session):
    later = SLOT + timedelta(hours=2)
    await observations.refresh_nightly_job_metrics(now=later)
    assert _quarantine_sample(metrics.nightly_job_overdue) == 1  # installed and never attempted
    async with session.begin() as db:
        await db.execute(
            update(NightlyJobObservation).where(NightlyJobObservation.schedule_key == QUARANTINE_MAINTENANCE.key)
            .values(monitoring_since=SLOT + timedelta(minutes=1))
        )
    await observations.refresh_nightly_job_metrics(now=later)
    assert _quarantine_sample(metrics.nightly_job_overdue) == 0
    async with session.begin() as db:
        await db.execute(text("UPDATE feature_flags SET rollout_percent = 0"))
    await observations.refresh_nightly_job_metrics(now=later + timedelta(days=2))
    assert _quarantine_sample(metrics.nightly_job_enabled) == 0
    assert _quarantine_sample(metrics.nightly_job_next_due_timestamp) == 0
    assert _quarantine_sample(metrics.nightly_job_overdue) == 0


async def test_metadata_edit_preserves_the_alert_and_reenablement_starts_a_new_window(session):
    after_slot = SLOT + timedelta(hours=2)
    async with session.begin() as db:
        await db.execute(text("UPDATE feature_flags SET updated_at = :at"), {"at": after_slot})
        await observations.stage_nightly_activation(
            db, QUARANTINE_MAINTENANCE.flag, was_enabled=True, is_enabled=True, at=after_slot,
        )
    await observations.refresh_nightly_job_metrics(now=after_slot)
    assert _quarantine_sample(metrics.nightly_job_overdue) == 1

    async with session.begin() as db:
        await observations.stage_nightly_activation(
            db, QUARANTINE_MAINTENANCE.flag, was_enabled=False, is_enabled=True, at=after_slot,
        )
    await observations.refresh_nightly_job_metrics(now=after_slot)
    assert _quarantine_sample(metrics.nightly_job_overdue) == 0
    await observations.refresh_nightly_job_metrics(now=after_slot + timedelta(days=1))
    assert _quarantine_sample(metrics.nightly_job_overdue) == 1


async def test_disabled_skipped_and_failed_work_cannot_record_success(monkeypatch):
    headers = {SCHEDULE_HEADER: QUARANTINE_MAINTENANCE.key, SLOT_HEADER: int(SLOT.timestamp())}
    record = AsyncMock(return_value=False)
    success = AsyncMock()
    monkeypatch.setattr(observations, "_record_attempt", record)
    monkeypatch.setattr(observations, "_record_success", success)

    enabled_task = AsyncMock(return_value={"expired": 0})
    await observations.observe_nightly_job(QUARANTINE_MAINTENANCE, headers, enabled_task)
    enabled_task.assert_awaited_once()
    record.return_value = True
    skipped_task = AsyncMock(return_value={"skipped": 1})
    await observations.observe_nightly_job(QUARANTINE_MAINTENANCE, headers, skipped_task)
    failed_task = AsyncMock(side_effect=RuntimeError("synthetic maintenance failure"))
    with pytest.raises(RuntimeError, match="synthetic maintenance failure"):
        await observations.observe_nightly_job(QUARANTINE_MAINTENANCE, headers, failed_task)
    success.assert_not_awaited()
    assert record.await_count == 3


async def test_missing_observation_store_does_not_break_the_job(monkeypatch):
    headers = {SCHEDULE_HEADER: PERF_BASELINE_REFRESH.key, SLOT_HEADER: int((SLOT + timedelta(minutes=30)).timestamp())}
    monkeypatch.setattr(observations, "_record_attempt", AsyncMock(side_effect=RuntimeError("synthetic disconnected store")))
    worker = AsyncMock(return_value={"observed": 6})
    assert await observations.observe_nightly_job(PERF_BASELINE_REFRESH, headers, worker) == {"observed": 6}
    worker.assert_awaited_once()
