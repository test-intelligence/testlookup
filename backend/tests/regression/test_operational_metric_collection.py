"""Freshness and SQL business outcomes remain observable during dependency failure."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import metrics
from app.services import metric_collection as collection
from app.services.downstream_operational_metrics import refresh_downstream_operational_metrics

NOW = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def outbox_db(monkeypatch):
    from app.db import postgres

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.execute(text(
            "CREATE TABLE run_downstream_outbox (operation VARCHAR(60), status VARCHAR(20), "
            "created_at DATETIME, next_attempt_at DATETIME, lease_expires_at DATETIME)"
        ))
    monkeypatch.setattr(postgres, "AsyncSessionLocal", async_sessionmaker(engine))
    yield engine
    await engine.dispose()


async def _insert(engine, operation, status, created, due=None, *, lease=None):
    async with engine.begin() as connection:
        await connection.execute(text(
            "INSERT INTO run_downstream_outbox VALUES (:operation, :status, :created, :due, :lease)"
        ), {"operation": operation, "status": status, "created": created.isoformat(),
            "due": due.isoformat() if due else None,
            "lease": lease.isoformat() if lease else None})


@pytest.mark.asyncio
async def test_terminal_sql_failures_are_visible_with_no_pending_broker_work(outbox_db):
    for _ in range(2):
        await _insert(outbox_db, "agent_pipeline", "failed", NOW - timedelta(days=3))
    await _insert(outbox_db, "agent_pipeline", "completed", NOW - timedelta(days=4))
    await refresh_downstream_operational_metrics(now=NOW)
    values = metrics.downstream_outbox_entries
    assert values.labels(operation="agent_pipeline", status="failed")._value.get() == 2
    assert values.labels(operation="agent_pipeline", status="pending")._value.get() == 0
    assert values.labels(operation="agent_pipeline", status="completed")._value.get() == 1
    assert metrics.downstream_outbox_oldest_age_seconds.labels(
        operation="agent_pipeline", status="failed",
    )._value.get() == 3 * 86400
    # Fresh empty storage resets a prior failed snapshot, rather than leaving
    # historical in-process values after an authorized recovery.
    async with outbox_db.begin() as connection:
        await connection.execute(text("DELETE FROM run_downstream_outbox"))
    await refresh_downstream_operational_metrics(now=NOW)
    assert values.labels(operation="agent_pipeline", status="failed")._value.get() == 0


@pytest.mark.asyncio
async def test_actual_retry_deadlines_and_bounded_unknown_operation_labels(outbox_db):
    await _insert(outbox_db, "agent_pipeline", "published", NOW - timedelta(hours=1),
                  NOW - timedelta(seconds=301), lease=NOW - timedelta(seconds=301))
    await _insert(outbox_db, "agent_pipeline", "pending", NOW - timedelta(hours=2), NOW + timedelta(minutes=1))
    await _insert(outbox_db, "untrusted-operation-a", "failed", NOW)
    await _insert(outbox_db, "untrusted-operation-b", "failed", NOW)
    await refresh_downstream_operational_metrics(now=NOW)
    assert metrics.downstream_outbox_overdue_seconds.labels(operation="agent_pipeline", status="published")._value.get() == 301
    assert metrics.downstream_outbox_overdue_seconds.labels(operation="agent_pipeline", status="pending")._value.get() == 0
    samples = [s for f in metrics.downstream_outbox_entries.collect() for s in f.samples]
    assert not any(s.labels["operation"].startswith("untrusted-") for s in samples)
    assert metrics.downstream_outbox_entries.labels(operation="other", status="failed")._value.get() == 2


@pytest.mark.asyncio
async def test_deadlines_follow_state_specific_relay_contract_despite_conflicting_legacy_fields(outbox_db):
    for status in ("waiting", "pending"):
        # A stale lease must not make a future retry/recovery slot overdue.
        await _insert(outbox_db, "agent_pipeline", status, NOW - timedelta(hours=2),
                      NOW + timedelta(minutes=1), lease=NOW - timedelta(minutes=5))
    for status in ("sending", "published", "processing"):
        # The relay consults lease expiry for these states, not next_attempt.
        await _insert(outbox_db, "agent_pipeline", status, NOW - timedelta(hours=2),
                      NOW - timedelta(minutes=5), lease=NOW + timedelta(minutes=1))
    await refresh_downstream_operational_metrics(now=NOW)
    overdue = metrics.downstream_outbox_overdue_seconds
    for status in ("waiting", "pending", "sending", "published", "processing"):
        assert overdue.labels(operation="agent_pipeline", status=status)._value.get() == 0

    async with outbox_db.begin() as connection:
        await connection.execute(text("DELETE FROM run_downstream_outbox"))
    for status in ("waiting", "pending"):
        await _insert(outbox_db, "agent_pipeline", status, NOW - timedelta(hours=2),
                      NOW - timedelta(seconds=61), lease=NOW + timedelta(minutes=1))
    for status in ("sending", "published", "processing"):
        await _insert(outbox_db, "agent_pipeline", status, NOW - timedelta(hours=2),
                      NOW + timedelta(minutes=1), lease=NOW - timedelta(seconds=61))
    await refresh_downstream_operational_metrics(now=NOW)
    for status in ("waiting", "pending", "sending", "published", "processing"):
        assert overdue.labels(operation="agent_pipeline", status=status)._value.get() == 61


@pytest.mark.asyncio
async def test_missing_lease_among_other_valid_rows_fails_collection_without_publishing_snapshot(outbox_db):
    await _insert(outbox_db, "agent_pipeline", "processing", NOW, lease=NOW + timedelta(minutes=1))
    await refresh_downstream_operational_metrics(now=NOW)
    gauge = metrics.downstream_outbox_entries.labels(operation="agent_pipeline", status="processing")
    assert gauge._value.get() == 1
    await _insert(outbox_db, "agent_pipeline", "processing", NOW - timedelta(hours=1),
                  NOW - timedelta(minutes=5))
    async def refresh():
        return await refresh_downstream_operational_metrics(now=NOW)

    assert not await collection.collect_bounded("downstream", refresh)
    assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 0
    assert gauge._value.get() == 1  # Previous snapshot is explicitly stale, not replaced with a healthy zero.


@pytest.mark.asyncio
async def test_slow_cancel_cleanup_does_not_block_scrape_or_spawn_repeated_refreshes(monkeypatch):
    monkeypatch.setattr(collection, "COLLECTION_TIMEOUT_SECONDS", 0.02)
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    calls = 0

    async def slow_cleanup():
        nonlocal calls
        calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    try:
        started = asyncio.get_running_loop().time()
        assert not await asyncio.wait_for(collection.collect_bounded("downstream", slow_cleanup), timeout=0.3)
        assert asyncio.get_running_loop().time() - started < 0.2
        await asyncio.wait_for(cleanup_started.wait(), timeout=0.2)
        pending = collection._IN_FLIGHT["downstream"]
        assert pending.abandoned
        assert not pending.task.done()
        assert not await asyncio.wait_for(collection.collect_bounded("downstream", slow_cleanup), timeout=0.3)
        assert calls == 1
        assert collection._IN_FLIGHT["downstream"] is pending
    finally:
        release_cleanup.set()
        pending = collection._IN_FLIGHT.get("downstream")
        if pending is not None:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(pending.task), timeout=0.3)
        await asyncio.sleep(0)
    assert "downstream" not in collection._IN_FLIGHT


@pytest.mark.asyncio
async def test_cancellation_suppression_cannot_publish_late_success_as_fresh(monkeypatch):
    monkeypatch.setattr(collection, "COLLECTION_TIMEOUT_SECONDS", 0.02)
    assert await collection.collect_bounded("downstream", AsyncMock(return_value=True))
    last = metrics.metric_collection_last_success.labels(collector="downstream")._value.get()
    release_late_success = asyncio.Event()

    async def ignores_cancellation():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_late_success.wait()
            return True

    pending = None
    try:
        assert not await asyncio.wait_for(collection.collect_bounded("downstream", ignores_cancellation), timeout=0.3)
        pending = collection._IN_FLIGHT["downstream"]
        assert pending.abandoned
        assert not pending.task.done()
        assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 0
        assert metrics.metric_collection_last_success.labels(collector="downstream")._value.get() == last
    finally:
        release_late_success.set()
        if pending is not None:
            assert await asyncio.wait_for(asyncio.shield(pending.task), timeout=0.3) is True
        await asyncio.sleep(0)
    assert "downstream" not in collection._IN_FLIGHT
    assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 0
    assert metrics.metric_collection_last_success.labels(collector="downstream")._value.get() == last


@pytest.mark.asyncio
async def test_overlapping_healthy_scrapes_share_one_successful_collection(monkeypatch):
    monkeypatch.setattr(collection, "COLLECTION_TIMEOUT_SECONDS", 0.5)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    before = metrics.metric_collection_errors_total.labels(collector="downstream")._value.get()

    async def refresh():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return True

    waiters = []
    try:
        waiters.append(asyncio.create_task(collection.collect_bounded("downstream", refresh)))
        await asyncio.wait_for(started.wait(), timeout=0.3)
        waiters.append(asyncio.create_task(collection.collect_bounded("downstream", refresh)))
        await asyncio.sleep(0)
        assert calls == 1
        assert not waiters[1].done(), "a healthy in-flight collection must not become a failed second scrape"
        release.set()
        assert await asyncio.wait_for(asyncio.gather(*waiters), timeout=0.3) == [True, True]
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*waiters, return_exceptions=True), timeout=0.3)
    assert metrics.metric_collection_errors_total.labels(collector="downstream")._value.get() == before
    assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 1
    assert "downstream" not in collection._IN_FLIGHT


@pytest.mark.asyncio
async def test_cancelled_first_waiter_does_not_cancel_shared_collection_or_fail_other_scrape(monkeypatch):
    monkeypatch.setattr(collection, "COLLECTION_TIMEOUT_SECONDS", 0.5)
    assert await collection.collect_bounded("downstream", AsyncMock(return_value=True))
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    before = metrics.metric_collection_errors_total.labels(collector="downstream")._value.get()

    async def refresh():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return True

    waiters = []
    try:
        waiters.append(asyncio.create_task(collection.collect_bounded("downstream", refresh)))
        await asyncio.wait_for(started.wait(), timeout=0.3)
        waiters.append(asyncio.create_task(collection.collect_bounded("downstream", refresh)))
        await asyncio.sleep(0)
        attempt = collection._IN_FLIGHT["downstream"]
        waiters[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiters[0]
        assert not attempt.abandoned
        assert attempt.task.cancelling() == 0
        assert not attempt.task.done()
        assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 1
        release.set()
        assert await asyncio.wait_for(waiters[1], timeout=0.3) is True
        assert calls == 1
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*waiters, return_exceptions=True), timeout=0.3)
    assert metrics.metric_collection_errors_total.labels(collector="downstream")._value.get() == before
    assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 1
    assert "downstream" not in collection._IN_FLIGHT


@pytest.mark.asyncio
async def test_slow_collector_is_bounded_and_does_not_advance_freshness(monkeypatch):
    monkeypatch.setattr(collection, "COLLECTION_TIMEOUT_SECONDS", 0.02)
    healthy = AsyncMock(return_value=True)
    assert await collection.collect_bounded("downstream", healthy)
    last = metrics.metric_collection_last_success.labels(collector="downstream")._value.get()
    before = metrics.metric_collection_errors_total.labels(collector="downstream")._value.get()
    stalled = asyncio.Event()
    start = asyncio.get_running_loop().time()
    assert not await collection.collect_bounded("downstream", stalled.wait)
    assert asyncio.get_running_loop().time() - start < 0.5
    assert metrics.metric_collection_success.labels(collector="downstream")._value.get() == 0
    assert metrics.metric_collection_last_success.labels(collector="downstream")._value.get() == last
    assert metrics.metric_collection_errors_total.labels(collector="downstream")._value.get() == before + 1


@pytest.mark.asyncio
async def test_immediate_failure_and_explicit_partial_result_are_not_success():
    for failed in (AsyncMock(side_effect=ConnectionError), AsyncMock(return_value=False), AsyncMock(return_value=None)):
        assert not await collection.collect_bounded("agents", failed)
        assert metrics.metric_collection_success.labels(collector="agents")._value.get() == 0
    assert await collection.collect_bounded("agents", AsyncMock(return_value=True))
    assert metrics.metric_collection_success.labels(collector="agents")._value.get() == 1


@pytest.mark.asyncio
async def test_child_queue_is_observed_and_partial_failure_is_reported(monkeypatch):
    from app import bootstrap
    from app.db import redis_client

    seen = []
    async def length(queue):
        seen.append(queue)
        if queue == "critical":
            raise ConnectionError("fake-down")
        return 7
    monkeypatch.setattr(redis_client, "get_redis", lambda: SimpleNamespace(llen=length))
    assert await bootstrap._refresh_celery_queue_depths() is False
    assert "agent_children" in seen
    assert metrics.celery_queue_length.labels(queue_name="agent_children")._value.get() == 7
