"""Concurrent worker observations against an explicitly disposable PostgreSQL."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import postgres
from app.models.postgres import NightlyJobObservation
from app.services import feature_flags, nightly_job_observation as observations
from app.worker.nightly_schedule import QUARANTINE_MAINTENANCE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_older_delivery_cannot_overwrite_the_newer_slot(monkeypatch):
    dsn = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN")
    if not dsn:
        pytest.skip("requires a disposable PostgreSQL DSN")
    schema = f"nightly_fixture_{uuid.uuid4().hex}"
    admin = create_async_engine(dsn)
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    schedule = QUARANTINE_MAINTENANCE
    latest = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)
    older = latest - timedelta(days=1)
    factory = async_sessionmaker(engine)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", factory)
    monkeypatch.setattr(feature_flags, "is_enabled", AsyncMock(return_value=True))

    try:
        async with admin.begin() as db:
            await db.execute(text(f"CREATE SCHEMA {schema}"))
        async with engine.begin() as db:
            await db.run_sync(NightlyJobObservation.__table__.create)
        async with factory.begin() as db:
            db.add(NightlyJobObservation(schedule_key=schedule.key, monitoring_since=older))

        async def worker(slot, observed_at):
            await observations._record_attempt(schedule, slot, observed_at)
            await observations._record_success(schedule, slot, observed_at + timedelta(minutes=1))

        await asyncio.gather(
            worker(latest, latest + timedelta(minutes=5)),
            worker(older, latest + timedelta(minutes=6)),
        )
        async with factory() as db:
            record = await db.get(NightlyJobObservation, schedule.key)
            assert record.last_attempt_slot_at == latest
            assert record.last_success_slot_at == latest
            assert record.last_success_finished_at == latest + timedelta(minutes=6)
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await admin.dispose()
