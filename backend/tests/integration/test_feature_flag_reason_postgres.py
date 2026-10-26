"""ADM-07: a feature flag says when, who and why it last changed.

The flags page showed key / description / state and nothing about the last
change; ``settings_audit_log`` kept field names only, never a reason. A
create / update now takes an optional ``reason`` (<= 500 characters) that lands
on the audit row (migration 0232), every flag response carries
``updated_by_name`` and ``last_change_reason`` (two queries for the whole list),
and ``GET /feature-flags/{key}/history`` lists the changes newest first.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head,
skips otherwise. Calls the route functions with a real session; the router
commits, so every row this test writes is deleted at the end.
"""

from __future__ import annotations

import os
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world(monkeypatch):
    from app.models.postgres import FeatureFlag, SettingsAuditLog, User, UserRole
    from app.services import feature_flags as svc

    async def _no_cache(key):  # Redis is not part of this test
        return None

    monkeypatch.setattr(svc, "invalidate_flag_cache", _no_cache)
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    key = f"adm07_{marker}"
    admin = User(
        id=uuid.uuid4(), email=f"admin-{marker}@example.test", username=f"admin-{marker}",
        full_name=f"Ada Admin {marker}", hashed_password="x", role=UserRole.ADMIN, is_active=True,
    )
    async with factory() as db:
        db.add(admin)
        await db.commit()
    try:
        async with factory() as db:
            yield {"db": db, "admin": admin, "key": key}
    finally:
        async with factory() as db:
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.setting_key == f"feature_flag:{key}"))
            await db.execute(delete(FeatureFlag).where(FeatureFlag.key == key))
            await db.execute(delete(User).where(User.id == admin.id))
            await db.commit()
        await engine.dispose()


async def test_the_reason_lands_on_the_audit_row_and_on_the_flag(world):
    from app.models.schemas import FeatureFlagCreate, FeatureFlagUpdate
    from app.routers import feature_flags as router

    db, admin, key = world["db"], world["admin"], world["key"]
    created = await router.create_feature_flag(
        FeatureFlagCreate(key=key, description="probe", reason="  seeded for the demo  "), db=db, current_user=admin,
    )
    assert created.updated_by_name == admin.full_name
    assert created.last_change_reason == "seeded for the demo"

    updated = await router.update_feature_flag(
        key, FeatureFlagUpdate(enabled_global=True, reason="demo INT-07"), db=db, current_user=admin,
    )
    assert updated.enabled_global is True
    assert updated.last_change_reason == "demo INT-07"

    # A change without a reason says so (None), not the previous change's reason.
    plain = await router.update_feature_flag(key, FeatureFlagUpdate(rollout_percent=50), db=db, current_user=admin)
    assert plain.last_change_reason is None

    listed = {f.key: f for f in await router.list_feature_flags(db=db)}
    assert listed[key].updated_by_name == admin.full_name and listed[key].last_change_reason is None
    # every other flag in the list was annotated too (no N+1 gap: attributes exist on all rows)
    assert all(hasattr(f, "last_change_reason") for f in listed.values())


async def test_the_history_is_newest_first_with_field_names_and_reasons(world):
    from app.models.schemas import FeatureFlagCreate, FeatureFlagUpdate
    from app.routers import feature_flags as router

    db, admin, key = world["db"], world["admin"], world["key"]
    await router.create_feature_flag(FeatureFlagCreate(key=key, reason="first"), db=db, current_user=admin)
    await router.update_feature_flag(key, FeatureFlagUpdate(enabled_global=True, reason="second"), db=db,
                                     current_user=admin)
    history = await router.get_feature_flag_history(key, limit=20, db=db)
    assert [(h["action"], h["reason"]) for h in history] == [("update", "second"), ("create", "first")]
    assert history[0]["changed_fields"] == ["enabled_global"]
    assert history[0]["actor_name"] == admin.username
    one = await router.get_feature_flag_history(key, limit=1, db=db)
    assert [h["reason"] for h in one] == ["second"]


async def test_an_unknown_flag_history_is_404(world):
    from fastapi import HTTPException

    from app.routers import feature_flags as router

    with pytest.raises(HTTPException) as exc:
        await router.get_feature_flag_history(f"{world['key']}_nope", limit=20, db=world["db"])
    assert exc.value.status_code == 404


async def test_a_reason_longer_than_500_characters_is_refused():
    from app.models.schemas import FeatureFlagCreate, FeatureFlagUpdate

    with pytest.raises(ValidationError):
        FeatureFlagUpdate(reason="x" * 501)
    with pytest.raises(ValidationError):
        FeatureFlagCreate(key="ok_key", reason="x" * 501)
    assert FeatureFlagUpdate(reason="x" * 500).reason == "x" * 500
