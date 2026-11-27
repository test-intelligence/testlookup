"""Narrow identity expiry: real disposable SQL, protected authority and guards."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import Column, MetaData, Table, func, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import (
    _bind_api_key_project, _bind_credential_kind, get_current_active_user,
)
from app.db.postgres import get_db
from app.models.identity_retention import ROUTINE_EVENT_TYPES, IdentityRetentionDocument
from app.models.postgres import (
    AppSetting, FederatedIdentity, IdentityEvent, InstanceRetentionPolicy, SettingsAuditLog, User,
)
from app.routers.identity_events import router
from app.services import identity_retention_service as service
from app.services.retention_service import FIELD_BOUNDS, purge_identity_events_batch


def actor(*, role="ADMIN", credential="jwt", bound=False):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, username="synthetic-admin", full_name="Synthetic Admin", is_active=True)
    _bind_credential_kind(user, credential)
    if bound:
        _bind_api_key_project(user, uuid.uuid4())
    return user


@pytest.fixture
async def world(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.sqlite'}")
    # Only the id column is needed by the real SCIM scope query. This avoids
    # pretending SQLite tests the unrelated PostgreSQL account schema.
    users = Table("users", MetaData(), Column("id", User.__table__.c.id.type, primary_key=True))
    async with engine.begin() as connection:
        await connection.run_sync(users.create)
        for table in (AppSetting.__table__, IdentityEvent.__table__, SettingsAuditLog.__table__,
                      FederatedIdentity.__table__, InstanceRetentionPolicy.__table__):
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    principal, local, admin = uuid.uuid4(), uuid.uuid4(), actor()
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        await db.execute(insert(users).values([{"id": value} for value in (principal, local, admin.id)]))
        await db.commit()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    async def seed(event_type="SSO_LOGIN", *, days=500, success=True, user_id=None, count=1):
        async with sessions() as db:
            events = [IdentityEvent(
                id=uuid.uuid4(), event_type=event_type, user_id=user_id or principal,
                created_at=now - timedelta(days=days), success=success,
                detail={"marker": "synthetic-event-content"}, ip_address="203.0.113.17",
            ) for _ in range(count)]
            db.add_all(events)
            await db.commit()
            return [event.id for event in events]

    async def approve(days=365):
        async with sessions() as db:
            preview = await service.preview(db, now=now, retention_days=days)
            await service.approve_policy(
                db, actor=admin, retention_days=days, observed_at=preview["observed_at"],
                preview_hash=preview["preview_hash"], confirmation=preview["enable_confirmation"],
                reason="Synthetic approved retention policy", now=now,
            )
            await db.commit()

    async def remaining():
        async with sessions() as db:
            return set((await db.execute(select(IdentityEvent.id))).scalars())

    async def purge(*, batch_size=500, scheduled=False, commit=True):
        async with sessions() as db:
            preview = await service.preview(db, now=now)
            result = await purge_identity_events_batch(
                db, now=now, actor=admin, batch_size=batch_size, scheduled=scheduled,
                observed_at=preview["observed_at"], preview_hash=preview["preview_hash"],
                confirmation=preview["purge_confirmation"], reason="Synthetic explicit maintenance",
            )
            if commit:
                await db.commit()
            else:
                await db.rollback()
            return result

    yield SimpleNamespace(sessions=sessions, now=now, seed=seed, approve=approve, remaining=remaining,
                          purge=purge, principal=principal, local=local, admin=admin)
    await engine.dispose()


@pytest.mark.asyncio
async def test_default_does_not_inherit_existing_project_instance_enablement(world):
    old = await world.seed(days=5000)
    async with world.sessions() as db:
        db.add(InstanceRetentionPolicy(id=1, enabled=True, audit_days=365))
        await db.commit()
    assert (await world.purge(scheduled=True)) == {"status": "disabled", "deleted": 0}
    assert await world.remaining() == set(old)
    async with world.sessions() as db:
        assert await db.get(AppSetting, service.POLICY_KEY) is None
        assert (await db.execute(select(func.count()).select_from(SettingsAuditLog))).scalar_one() == 0


@pytest.mark.asyncio
async def test_only_successful_routine_events_expire_and_scim_authority_still_authorizes(world):
    from app.services.mfa_service import is_sso_managed
    from app.services.scim_service import scim_visible_user_ids

    disposable = set()
    for kind in ROUTINE_EVENT_TYPES:
        disposable.update(await world.seed(kind))
    kept = set(await world.seed("SCIM_USER_CREATED", days=5000))
    kept.update(await world.seed("SSO_LOGIN_FAILED", days=5000, success=False))
    kept.update(await world.seed("ACCOUNT_LOCKED", days=5000))
    kept.update(await world.seed("MFA_BREAKGLASS_RESET", days=5000))
    kept.update(await world.seed("UNKNOWN_FUTURE_EVENT", days=5000))
    kept.update(await world.seed("SSO_LOGIN", success=False))
    kept.update(await world.seed("SSO_LOGIN", days=20))
    await world.approve()
    result = await world.purge()
    assert result["deleted"] == len(disposable) == 3
    assert await world.remaining() == kept
    async with world.sessions() as db:
        assert await scim_visible_user_ids(db, {world.principal, world.local}, None) == {world.principal}
        assert await is_sso_managed(db, SimpleNamespace(id=world.principal)) is True
        assert await is_sso_managed(db, SimpleNamespace(id=world.local)) is False
        audit = (await db.execute(select(SettingsAuditLog).where(
            SettingsAuditLog.setting_key == service.PURGE_AUDIT_KEY,
        ))).scalar_one()
        assert audit.actor_id == world.admin.id
        assert audit.changed_fields["deleted"] == 3
        assert "synthetic-event-content" not in str(audit.changed_fields)
        assert "203.0.113.17" not in str(audit.changed_fields)


@pytest.mark.asyncio
async def test_finite_batches_boundary_and_atomic_rollback(world):
    await world.seed(count=5)
    boundary = await world.seed(days=365)
    await world.approve()
    assert (await world.purge(batch_size=2, commit=False))["deleted"] == 2
    assert len(await world.remaining()) == 6
    async with world.sessions() as db:
        assert (await db.execute(select(func.count()).select_from(SettingsAuditLog).where(
            SettingsAuditLog.setting_key == service.PURGE_AUDIT_KEY,
        ))).scalar_one() == 0
    assert (await world.purge(batch_size=2))["batch_full"] is True
    assert (await world.purge(batch_size=2))["deleted"] == 2
    assert (await world.purge(batch_size=2))["deleted"] == 1
    assert await world.remaining() == set(boundary)


@pytest.mark.asyncio
async def test_overlapping_holds_are_independent_and_preserve_audit_history(world):
    original = set(await world.seed())
    await world.approve()
    async with world.sessions() as db:
        first = await service.place_hold(db, actor=world.admin, reason="Synthetic investigation one", expires_at=None, now=world.now)
        second = await service.place_hold(db, actor=world.admin, reason="Synthetic investigation two", expires_at=None, now=world.now)
        await db.commit()
    assert (await world.purge(scheduled=True))["status"] == "held"
    async with world.sessions() as db:
        await service.release_hold(db, actor=world.admin, hold_id=first.id, reason="Synthetic case resolved",
                                   confirmation=service.RELEASE_CONFIRMATION, now=world.now)
        await db.commit()
    with pytest.raises(service.IdentityRetentionError, match="hold prevents"):
        await world.purge()
    assert await world.remaining() == original
    async with world.sessions() as db:
        await service.release_hold(db, actor=world.admin, hold_id=second.id, reason="Synthetic second case resolved",
                                   confirmation=service.RELEASE_CONFIRMATION, now=world.now)
        await db.commit()
        rows = (await db.execute(select(SettingsAuditLog).where(SettingsAuditLog.action.in_(["hold_placed", "hold_released"])))).scalars().all()
        assert len(rows) == 4
        assert {row.changed_fields["hold"]["id"] for row in rows} == {str(first.id), str(second.id)}
    assert (await world.purge())["deleted"] == 1


@pytest.mark.asyncio
async def test_expired_hold_does_not_require_a_job_and_disable_never_releases_live_hold(world):
    await world.seed()
    await world.approve()
    async with world.sessions() as db:
        await service.place_hold(db, actor=world.admin, reason="Synthetic expired hold",
                                 expires_at=world.now - timedelta(days=1), now=world.now - timedelta(days=2))
        await db.commit()
    assert (await world.purge())["deleted"] == 1
    async with world.sessions() as db:
        await service.place_hold(db, actor=world.admin, reason="Synthetic live hold", expires_at=None, now=world.now)
        await service.disable_policy(db, actor=world.admin, reason="Synthetic safe disable")
        await db.commit()
        read = await service.read_policy(db, now=world.now)
        assert read["enabled"] is False and read["active_hold_count"] == 1


@pytest.mark.asyncio
async def test_stale_preview_and_policy_corruption_fail_closed(world):
    original = set(await world.seed())
    async with world.sessions() as db:
        preview = await service.preview(db, now=world.now, retention_days=365)
        await service.place_hold(db, actor=world.admin, reason="Synthetic new hold", expires_at=None, now=world.now)
        await db.commit()
        with pytest.raises(service.IdentityRetentionError, match="preview expired"):
            await service.approve_policy(db, actor=world.admin, retention_days=365,
                                         observed_at=world.now, preview_hash=preview["preview_hash"],
                                         confirmation=preview["enable_confirmation"], reason="Synthetic approval", now=world.now)
        await db.rollback()
        row = await db.get(AppSetting, service.POLICY_KEY)
        row.value = {"enabled": True, "retention_days": 1}
        await db.commit()
    with pytest.raises(service.IdentityRetentionError, match="unreadable"):
        await world.purge(scheduled=True)
    assert await world.remaining() == original


@pytest.mark.asyncio
async def test_failed_audit_flush_rolls_back_deleted_rows(world, monkeypatch):
    original = set(await world.seed())
    await world.approve()
    async with world.sessions() as db:
        monkeypatch.setattr(db, "flush", AsyncMock(side_effect=RuntimeError("synthetic audit failure")))
        with pytest.raises(RuntimeError, match="synthetic audit"):
            await purge_identity_events_batch(db, now=world.now, scheduled=True)
        await db.rollback()
    assert await world.remaining() == original


@pytest.mark.asyncio
async def test_scheduled_wrapper_honors_instance_utc_window(world):
    await world.seed()
    await world.approve()
    async with world.sessions() as db:
        db.add(InstanceRetentionPolicy(id=1, enabled=False, purge_window_start_utc=0))
        await db.commit()
    assert (await service.run_scheduled_batch(now=world.now.replace(hour=1)))["status"] == "outside_window"
    assert (await service.run_scheduled_batch(now=world.now.replace(hour=0)))["deleted"] == 1


def test_allowlist_bounds_and_lock_contract():
    assert set(ROUTINE_EVENT_TYPES) == {"SSO_LOGIN", "SSO_LOGOUT", "MFA_VERIFY_SUCCESS"}
    assert FIELD_BOUNDS["audit_days"] == (365, 3650)
    assert IdentityRetentionDocument().enabled is False
    statement = select(AppSetting).where(AppSetting.key == service.POLICY_KEY).with_for_update()
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    candidate = select(IdentityEvent.id).where(service.routine_event_clause()).limit(500).with_for_update(skip_locked=True)
    sql = str(candidate.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql and "success IS true" in sql


@pytest.fixture
def client_factory(world):
    def create(user):
        app = FastAPI()
        app.include_router(router)

        async def db_dependency():
            async with world.sessions() as db:
                try:
                    yield db
                except BaseException:
                    await db.rollback()
                    raise

        app.dependency_overrides[get_db] = db_dependency
        app.dependency_overrides[get_current_active_user] = lambda: user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return create


_READS = [("GET", "/retention/policy", None), ("GET", "/retention/preview", None)]
_WRITES = [
    ("POST", "/retention/policy", {"retention_days": 365, "observed_at": datetime.now(timezone.utc).isoformat(),
                                    "preview_hash": "a" * 64, "confirmation": "synthetic", "reason": "Synthetic reason"}),
    ("POST", "/retention/disable", {"reason": "Synthetic reason"}),
    ("POST", "/retention/holds", {"reason": "Synthetic reason"}),
    ("POST", f"/retention/holds/{uuid.UUID(int=1)}/release", {"reason": "Synthetic reason", "confirmation": "synthetic"}),
    ("POST", "/retention/purge", {"observed_at": datetime.now(timezone.utc).isoformat(), "preview_hash": "a" * 64,
                                   "confirmation": "synthetic", "reason": "Synthetic reason"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
@pytest.mark.parametrize(("method", "path", "body"), [*_READS, *_WRITES])
async def test_every_retention_route_is_instance_admin_only(client_factory, role, method, path, body):
    async with client_factory(actor(role=role)) as client:
        response = await client.request(method, "/api/v1/identity" + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), _WRITES)
async def test_even_unbound_admin_api_keys_cannot_mutate_retention(client_factory, method, path, body):
    async with client_factory(actor(credential="api_key")) as client:
        response = await client.request(method, "/api/v1/identity" + path, json=body)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "interactive_admin_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), [*_READS, *_WRITES])
async def test_project_bound_admin_key_cannot_read_or_mutate_instance_policy(client_factory, method, path, body):
    async with client_factory(actor(credential="api_key", bound=True)) as client:
        response = await client.request(method, "/api/v1/identity" + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_real_guarded_enable_preview_purge_and_permanent_audit(world, client_factory):
    routine = set(await world.seed(count=2))
    authority = set(await world.seed("SCIM_USER_CREATED", days=5000))
    async with client_factory(world.admin) as client:
        preview = (await client.get("/api/v1/identity/retention/preview?retention_days=365")).json()
        assert preview["counts"] == {"routine_authentication": 2, "scim_authority": 1, "protected_identity": 0}
        assert "synthetic-event-content" not in str(preview)
        response = await client.post("/api/v1/identity/retention/policy", json={
            "retention_days": 365, "observed_at": preview["observed_at"], "preview_hash": preview["preview_hash"],
            "confirmation": preview["enable_confirmation"], "reason": "Synthetic approved administration",
        })
        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True
        preview = (await client.get("/api/v1/identity/retention/preview")).json()
        response = await client.post("/api/v1/identity/retention/purge", json={
            "observed_at": preview["observed_at"], "preview_hash": preview["preview_hash"],
            "confirmation": preview["purge_confirmation"], "reason": "Synthetic explicit cleanup", "batch_size": 1,
        })
        assert response.status_code == 200, response.text
        assert response.json()["deleted"] == 1
    assert authority <= await world.remaining()
    assert len(routine & await world.remaining()) == 1
