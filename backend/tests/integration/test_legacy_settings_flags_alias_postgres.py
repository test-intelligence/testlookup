"""W25b HYG-03 — the legacy ``/api/v1/settings/flags`` aliases against a real PostgreSQL.

The three legacy routes now write through the canonical ``services/feature_flags``
(the facade module is deleted). Driven with a real session, proven here:

1. a legacy ``PUT`` of a new key creates the row the facade would have written —
   ``enabled_global`` from the body, ``rollout_percent == 100``, NO project or
   role allow-list — plus ``updated_by_user_id`` = the admin; the audit log holds
   exactly the route's ``updated`` and the service's ``create`` (D-W25b-2: both
   audits kept);
2. the canonical ``GET /feature-flags/{key}`` answers that row with
   ``updated_by_name``; the legacy GET lists it in the legacy shape;
3. a legacy ``PUT enabled=false`` is the canonical ``enabled_global = false``
   (+ ``updated`` / ``update``); a legacy ``DELETE`` makes the canonical GET 404
   (+ ``deleted`` / ``delete``); a second ``DELETE`` writes no audit row.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head,
skips otherwise. Redis is not part of it (``invalidate_flag_cache`` is a
no-op). The routes commit, so the teardown deletes every row this module wrote
— its audit rows (by key), its flag and its user — because CI runs all
``*_postgres.py`` modules in ONE shared database; the app engine is disposed
before the module's engine is built and after it is disposed.
"""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

# The legacy router module imports the SMTP transport at module level; the
# routes under test never use it.
sys.modules.setdefault("aiosmtplib", MagicMock())

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Non-hex letters only: a short hex-looking literal can collide with a uuid in a dump.
_NON_HEX = str.maketrans("0123456789abcdef", "ghijklmnopqrstuv")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    from app.db import postgres as app_postgres
    from app.models.postgres import FeatureFlag, SettingsAuditLog, User, UserRole
    from app.services import feature_flags as svc

    async def _no_cache(key):  # Redis is not part of this test
        return None

    monkeypatch.setattr(svc, "invalidate_flag_cache", _no_cache)
    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex[:10].translate(_NON_HEX)
    key = f"w25b_alias_{marker}"
    admin = User(
        id=uuid.uuid4(), email=f"w25b-admin-{marker}@example.test", username=f"w25b-admin-{marker}",
        full_name=f"Alias Admin {marker}", hashed_password="x", role=UserRole.ADMIN, is_active=True,
    )
    async with factory() as db:
        db.add(admin)
        await db.commit()
    try:
        async with factory() as db:
            yield {"db": db, "factory": factory, "admin": admin, "key": key}
    finally:
        async with factory() as db:
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.setting_key == f"feature_flag:{key}"))
            await db.execute(delete(FeatureFlag).where(FeatureFlag.key == key))
            await db.execute(delete(User).where(User.id == admin.id))
            await db.commit()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _audit_actions(factory, key: str) -> list[str]:
    from app.models.postgres import SettingsAuditLog

    async with factory() as db:
        rows = (await db.execute(
            select(SettingsAuditLog.action).where(SettingsAuditLog.setting_key == f"feature_flag:{key}")
        )).scalars().all()
    return sorted(rows)


async def _stored(factory, key: str):
    from app.models.postgres import FeatureFlag

    async with factory() as db:
        return (await db.execute(
            select(
                FeatureFlag.enabled_global, FeatureFlag.rollout_percent, FeatureFlag.enabled_projects,
                FeatureFlag.enabled_roles, FeatureFlag.updated_by_user_id, FeatureFlag.description,
            ).where(FeatureFlag.key == key)
        )).one_or_none()


async def test_a_legacy_write_is_a_canonical_write_with_both_audit_rows(world):
    from fastapi import HTTPException

    from app.routers import app_settings as legacy
    from app.routers import feature_flags as canonical

    db, factory, admin, key = world["db"], world["factory"], world["admin"], world["key"]

    # 1. PUT of a new key → the facade's row (global toggle, no allow-list, 100 %) + the actor.
    answer = await legacy.update_feature_flag(
        key, legacy.FeatureFlagUpdate(enabled=True, description="from a script"), db=db, current_user=admin,
    )
    assert answer == {"flag_key": key, "enabled": True, "scope": "global"}
    row = await _stored(factory, key)
    assert row is not None
    assert row.enabled_global is True
    assert row.rollout_percent == 100
    assert row.enabled_projects is None
    assert row.enabled_roles is None
    assert row.updated_by_user_id == admin.id
    assert row.description == "from a script"
    assert await _audit_actions(factory, key) == ["create", "updated"]

    # 2. The successor API sees it, with who changed it; the legacy GET lists it in the legacy shape.
    async with factory() as fresh:
        via_canonical = await canonical.get_feature_flag(key, db=fresh)
    assert via_canonical.enabled_global is True
    assert via_canonical.updated_by_name == admin.full_name
    async with factory() as fresh:
        listed = {r["flag_key"]: r for r in await legacy.list_feature_flags(db=fresh)}
    assert set(listed[key]) == {"flag_key", "scope", "enabled", "config", "description", "updated_at"}
    assert listed[key]["enabled"] is True
    assert listed[key]["scope"] == "global" and listed[key]["config"] is None
    assert listed[key]["description"] == "from a script"
    assert isinstance(listed[key]["updated_at"], str)

    # 3. PUT enabled=false → the canonical toggle is off; allow-lists and rollout untouched.
    await legacy.update_feature_flag(key, legacy.FeatureFlagUpdate(enabled=False), db=db, current_user=admin)
    row = await _stored(factory, key)
    assert row.enabled_global is False
    assert row.rollout_percent == 100 and row.enabled_projects is None and row.enabled_roles is None
    assert row.description == "from a script"  # no description sent → kept
    async with factory() as fresh:
        assert (await canonical.get_feature_flag(key, db=fresh)).enabled_global is False
    assert await _audit_actions(factory, key) == ["create", "update", "updated", "updated"]

    # 4. DELETE → the canonical GET is 404; both audit rows.
    await legacy.remove_feature_flag(key, db=db, current_user=admin)
    assert await _stored(factory, key) is None
    async with factory() as fresh:
        with pytest.raises(HTTPException) as exc:
            await canonical.get_feature_flag(key, db=fresh)
    assert exc.value.status_code == 404
    after_delete = ["create", "delete", "deleted", "update", "updated", "updated"]
    assert await _audit_actions(factory, key) == after_delete

    # 5. DELETE of the now-missing key: still fine (the route answers 204), no new audit row.
    await legacy.remove_feature_flag(key, db=db, current_user=admin)
    assert await _audit_actions(factory, key) == after_delete
