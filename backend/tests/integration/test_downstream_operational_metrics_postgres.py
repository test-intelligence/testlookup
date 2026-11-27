"""Durable SQL outcomes stay visible across misleading Celery SUCCESS and replay.

The test owns one temporary schema, with no shared application rows. It uses
Celery's real eager trace and the production tracked-task/outbox/metric paths.
The optional empty-Valkey control needs a separately owned disposable URL.
Broker publication and external business effects are deliberately absent.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from celery import Celery
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import metrics
from app.db import postgres
from app.models.postgres import RunDownstreamOutbox
from app.services import downstream_operational_metrics, run_downstream_outbox as outbox
from app.worker import tasks

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def durable_fixture(monkeypatch):
    dsn = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("requires a disposable PostgreSQL DSN")
    schema = f"downstream_metrics_fixture_{uuid.uuid4().hex}"
    admin = create_async_engine(dsn)
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0, pool_timeout=2,
        connect_args={"command_timeout": 5, "server_settings": {"search_path": schema, "statement_timeout": "3000"}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(outbox, "AsyncSessionLocal", sessions)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin.begin() as db:
            await db.execute(text(f"CREATE SCHEMA {schema}"))
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE projects (id uuid PRIMARY KEY)"))
            await db.execute(text("CREATE TABLE test_runs (id uuid PRIMARY KEY)"))
            await db.execute(text("INSERT INTO projects VALUES (:id)"), {"id": project_id})
            await db.execute(text("INSERT INTO test_runs VALUES (:id)"), {"id": run_id})
            await db.run_sync(RunDownstreamOutbox.__table__.create)
        yield SimpleNamespace(sessions=sessions, project_id=project_id, run_id=run_id)
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await admin.dispose()


def scrape():
    registry = CollectorRegistry()
    for gauge in (metrics.downstream_outbox_entries, metrics.downstream_outbox_oldest_age_seconds,
                  metrics.downstream_outbox_overdue_seconds):
        registry.register(gauge)
    samples = {}
    for family in text_string_to_metric_families(generate_latest(registry).decode("utf-8")):
        for sample in family.samples:
            assert set(sample.labels) == {"operation", "status"}
            samples[(sample.name, sample.labels["operation"], sample.labels["status"])] = sample.value
    return samples


def count(samples, status, operation="agent_pipeline"):
    return samples[("testlookup_downstream_outbox_entries", operation, status)]


async def seed(fixture, now):
    rows = []
    for offset in (600, 500):
        rows.append(RunDownstreamOutbox(id=uuid.uuid4(), project_id=fixture.project_id, run_id=fixture.run_id,
            operation="agent_pipeline", input_version=uuid.uuid4().hex, status="published",
            execution_attempts=outbox.MAX_EXECUTION_ATTEMPTS - 1, attempts=1,
            dispatch_token=uuid.uuid4(), created_at=now - timedelta(seconds=offset),
            lease_expires_at=now + timedelta(minutes=2), payload={}))
    rows.extend([
        RunDownstreamOutbox(id=uuid.uuid4(), project_id=fixture.project_id, run_id=fixture.run_id,
            operation="persist_live_session", input_version=uuid.uuid4().hex, status="published", attempts=1,
            dispatch_token=uuid.uuid4(), created_at=now - timedelta(seconds=900),
            lease_expires_at=now - timedelta(seconds=61), payload={}),
        RunDownstreamOutbox(id=uuid.uuid4(), project_id=fixture.project_id, run_id=fixture.run_id,
            operation="suite_comparison", input_version=uuid.uuid4().hex, status="pending",
            created_at=now - timedelta(seconds=300), next_attempt_at=now + timedelta(minutes=2), payload={}),
    ])
    async with fixture.sessions.begin() as db:
        db.add_all(rows)
    return rows


async def exercise_durable_outcomes(fixture, monkeypatch, *, broker=None):
    now = datetime.now(timezone.utc)
    rows = await seed(fixture, now)
    event_loop = asyncio.get_running_loop()
    monkeypatch.setattr(tasks, "_run_async", lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, event_loop).result(timeout=5))
    effects = []

    class ProbeTask(tasks.DownstreamTrackedTask):
        name = "tests.durable_outcome_probe"

        def run(self, *, succeed):
            if not succeed:
                raise ValueError("synthetic business failure")
            effects.append("one controlled business effect")
            return {"processed": True}

    app = Celery("durable-metrics-fixture", broker="memory://", backend="cache+memory://")
    probe = app.register_task(ProbeTask())

    async def invoke(row, *, succeed):
        return await asyncio.wait_for(asyncio.to_thread(probe.apply,
            kwargs={"succeed": succeed}, task_id=str(uuid.uuid4()), throw=True,
            headers={"downstream_outbox_id": str(row.id), "downstream_dispatch_token": str(row.dispatch_token)}), timeout=10)

    try:
        for row in rows[:2]:
            result = await invoke(row, succeed=False)
            assert result.state == "SUCCESS" and result.successful()
            assert result.result == {"deferred": True, "downstream_outbox_id": str(row.id)}
        async with fixture.sessions() as db:
            stored = (await db.scalars(select(RunDownstreamOutbox).order_by(RunDownstreamOutbox.created_at))).all()
            assert sum(row.status == "failed" for row in stored) == 2
            assert all(row.completed_at is None for row in stored)
        assert await downstream_operational_metrics.refresh_downstream_operational_metrics(now=now)
        failed = scrape()
        assert (count(failed, "failed"), count(failed, "completed"), count(failed, "pending")) == (2, 0, 0)
        assert count(failed, "published", "persist_live_session") == 1
        assert failed[("testlookup_downstream_outbox_overdue_seconds", "persist_live_session", "published")] == 61
        assert failed[("testlookup_downstream_outbox_overdue_seconds", "suite_comparison", "pending")] == 0
        if broker is not None:
            assert await broker.dbsize() == 0, "SQL failures remain visible while the broker is empty"

        # Operator service-level recovery leaves the second failed intent intact.
        # Route authorization is covered by test_outbox_requeue_route.py.
        async with fixture.sessions.begin() as db:
            selected = await outbox.requeue_failed_downstream_operations(db, operation="agent_pipeline",
                run_id=fixture.run_id, limit=1, dry_run=False)
            assert [item["id"] for item in selected] == [str(rows[0].id)]
        async with fixture.sessions.begin() as db:
            claimed = await outbox.claim_staged_dispatches(db, run_id=fixture.run_id, operations=("agent_pipeline",))
            assert [row.id for row in claimed] == [rows[0].id]
            recovered = claimed[0]
        async with fixture.sessions.begin() as db:
            assert await outbox.mark_downstream_dispatch_published(db, outbox_id=recovered.id, dispatch_token=recovered.dispatch_token)
        result = await invoke(recovered, succeed=True)
        assert result.successful() and result.result == {"processed": True}
        for _ in range(2):
            duplicate = await invoke(recovered, succeed=True)
            assert duplicate.successful() and duplicate.result == {"duplicate": True, "downstream_outbox_id": str(recovered.id)}
        assert effects == ["one controlled business effect"]
        assert await downstream_operational_metrics.refresh_downstream_operational_metrics(now=now)
        recovered_samples = scrape()
        assert (count(recovered_samples, "failed"), count(recovered_samples, "completed"), count(recovered_samples, "pending")) == (1, 1, 0)
        async with fixture.sessions() as db:
            recovered_row = await db.get(RunDownstreamOutbox, recovered.id)
            assert recovered_row.execution_attempts == 1 and recovered_row.status == "completed"
    finally:
        app.close()


async def test_celery_success_does_not_hide_sql_failure_never_started_or_duplicate_work(durable_fixture, monkeypatch):
    await exercise_durable_outcomes(durable_fixture, monkeypatch)


async def test_durable_failures_and_recovery_remain_visible_with_empty_native_valkey(durable_fixture, monkeypatch):
    uri = os.getenv("TESTLOOKUP_VALKEY_OPERATIONAL_TEST_URL", "").strip()
    if not uri:
        pytest.skip("requires a separately owned disposable Valkey URL")
    import redis.asyncio as redis

    broker = redis.Redis.from_url(uri, socket_timeout=2, socket_connect_timeout=2)
    try:
        assert await broker.ping()
        assert await broker.dbsize() == 0, "the disposable broker must start empty"
        await exercise_durable_outcomes(durable_fixture, monkeypatch, broker=broker)
        assert await broker.dbsize() == 0, "no broker backlog exists throughout the durable SQL failure/recovery"
    finally:
        await broker.aclose()
