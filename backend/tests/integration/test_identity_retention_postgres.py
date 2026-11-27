"""Optional policy/hold serialization proof in an isolated PostgreSQL schema.

Requires an explicit disposable TESTLOOKUP_POSTGRES_TEST_DSN. Only LIKE copies
of three already migrated tables are used; public rows are never modified.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import _bind_credential_kind
from app.models.postgres import IdentityEvent, SettingsAuditLog
from app.services import identity_retention_service as service
from app.services.retention_service import purge_identity_events_batch

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_committed_instance_hold_serializes_with_expiry_and_scim_rows_survive():
    dsn = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    schema = "identity_retention_proof_" + uuid.uuid4().hex
    # The identifier is generated here, never supplied in a request or DSN.
    assert schema.startswith("identity_retention_proof_") and len(schema.rsplit("_", 1)[1]) == 32
    control = create_async_engine(dsn)
    engine = None
    try:
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for table in ("app_settings", "identity_events", "settings_audit_log"):
                # LIKE copies column/default/index/check contracts, not foreign
                # keys into account tables. This schema has no real accounts.
                await connection.execute(text(f'CREATE TABLE "{schema}"."{table}" (LIKE public."{table}" INCLUDING ALL)'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema + ",public"}})
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", username="synthetic-admin", full_name="Synthetic Admin")
        _bind_credential_kind(admin, "jwt")
        routine, authority = uuid.uuid4(), uuid.uuid4()
        async with sessions() as db:
            db.add_all([
                IdentityEvent(id=routine, event_type="SSO_LOGIN", success=True, created_at=now - timedelta(days=500)),
                IdentityEvent(id=authority, event_type="SCIM_USER_CREATED", success=True, created_at=now - timedelta(days=5000)),
            ])
            await db.commit()
            preview = await service.preview(db, now=now, retention_days=365)
            await service.approve_policy(db, actor=admin, retention_days=365, observed_at=now,
                                         preview_hash=preview["preview_hash"], confirmation=preview["enable_confirmation"],
                                         reason="Synthetic PostgreSQL proof", now=now)
            await db.commit()
        async with sessions() as holder, sessions() as purger:
            await purger.execute(text("SELECT 1"))  # establish its connection before the race
            hold = await service.place_hold(holder, actor=admin, reason="Synthetic independent hold", expires_at=None, now=now)
            task = asyncio.create_task(purge_identity_events_batch(purger, now=now, scheduled=True))
            try:
                await asyncio.sleep(0.1)
                assert not task.done(), "purge bypassed the policy/hold transaction lock"
                await holder.commit()
                assert (await task)["status"] == "held"
                await purger.commit()
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        async with sessions() as db:
            assert set((await db.execute(select(IdentityEvent.id))).scalars()) == {routine, authority}
            await service.release_hold(db, actor=admin, hold_id=hold.id, reason="Synthetic case resolved",
                                       confirmation=service.RELEASE_CONFIRMATION, now=now)
            await db.commit()
            assert (await purge_identity_events_batch(db, now=now, scheduled=True))["deleted"] == 1
            await db.commit()
            assert set((await db.execute(select(IdentityEvent.id))).scalars()) == {authority}
        # Read committed receipts through a fresh session, not pending objects.
        async with sessions() as db:
            receipts = list((await db.execute(select(SettingsAuditLog))).scalars())
            assert {row.action for row in receipts} == {"enabled", "hold_placed", "hold_released", "purged"}
            assert len(receipts) == 4
            assert all(row.actor_name == "Synthetic Admin" for row in receipts if row.action != "purged")
            purge = next(row for row in receipts if row.action == "purged")
            assert purge.setting_key == service.PURGE_AUDIT_KEY
            assert purge.changed_fields["deleted"] == 1
            assert purge.changed_fields["scheduled"] is True
    finally:
        if engine is not None:
            await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await control.dispose()
