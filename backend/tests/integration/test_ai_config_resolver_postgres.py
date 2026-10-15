"""W04 G-A on real PostgreSQL + Redis: the stored AI settings are what the runtime obeys.

What only real stores can prove (contract W04 §6.3 G-A (1)):

1. Stored / absent / invalid ``app_settings["ai_config"]`` rows -> value + source
   for ``analysis_mode``, ``deep_investigation_enabled``, ``finetune_enabled``
   and the timeout, identically from the runtime resolver and from
   ``GET /settings/ai``'s loader (JSON round trip through Postgres: a stored
   ``"false"`` string stays a string and is ignored).
2. ``PUT /settings/ai`` then ``GET /settings/ai`` (the real handlers on a real
   session) round-trips every value and source, and seeds
   ``config:analysis_mode`` with NO expiry (Redis ``TTL == -1``).
3. A v1 ``config:ai_effective`` entry (an old-build pod) is never read.
4. Redis flushed (the keys deleted) -> ``resolve_configured_analysis_mode``
   re-seeds the stored mode without a TTL, through the passed session only.

The ``ai_config`` row is global: the test saves it and restores it at the end,
and every Redis key it touches is deleted afterwards. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated) and ``REDIS_URL``; run through
``docs/deferred-scope-2026-10-13/tools/local-stack/pg_pytest_w04.sh a``.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
redis_asyncio = pytest.importorskip("redis.asyncio")

pytestmark = [pytest.mark.integration]

_KEYS = ("config:ai_effective:v2", "config:ai_effective", "config:analysis_mode")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest_asyncio.fixture
async def stores(monkeypatch):
    from app.models.postgres import AppSetting, User, UserRole

    engine = create_async_engine(_env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.from_url(_env("REDIS_URL"), decode_responses=True)
    await redis.ping()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    def _no_second_session(*_a, **_kw):
        raise AssertionError("a second session was opened")

    # Every read under test goes through the session it was given.
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _no_second_session)

    async with sessions() as db:
        original = (await db.execute(select(AppSetting).where(AppSetting.key == "ai_config"))).scalar_one_or_none()
        saved = None if original is None else (dict(original.value or {}), original.is_secret_backed)
        tag = uuid.uuid4().hex[:8]
        admin = User(
            id=uuid.uuid4(), email=f"w04a-{tag}@example.com", username=f"w04a_{tag}",
            hashed_password="x", role=UserRole.ADMIN.value, is_active=True,
        )
        db.add(admin)
        await db.commit()
    await redis.delete(*_KEYS)

    async def write_row(value):
        async with sessions() as db:
            row = (await db.execute(select(AppSetting).where(AppSetting.key == "ai_config"))).scalar_one_or_none()
            if value is None:
                if row is not None:
                    await db.delete(row)
            elif row is None:
                db.add(AppSetting(key="ai_config", value=value))
            else:
                row.value = value
            await db.commit()
        await redis.delete(*_KEYS)

    try:
        yield sessions, redis, admin, write_row
    finally:
        async with sessions() as db:
            row = (await db.execute(select(AppSetting).where(AppSetting.key == "ai_config"))).scalar_one_or_none()
            if saved is None:
                if row is not None:
                    await db.delete(row)
            else:
                if row is None:
                    db.add(AppSetting(key="ai_config", value=saved[0], is_secret_backed=saved[1]))
                else:
                    row.value, row.is_secret_backed, row.updated_by = saved[0], saved[1], None
            await db.commit()
            await db.execute(delete(User).where(User.id == admin.id))
            await db.commit()
        await redis.delete(*_KEYS)
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, {
            "analysis_mode": ("auto", "env_default"),
            "deep_investigation_enabled": (True, "env_default"),
            "finetune_enabled": (False, "env_default"),
            "timeout": (300, "env_default"),
        }),
        ({"analysis_mode": "rules", "deep_investigation_enabled": False, "finetune_enabled": True,
          "ai_timeout_seconds": 5}, {
            "analysis_mode": ("rules", "ai_config"),
            "deep_investigation_enabled": (False, "ai_config"),
            "finetune_enabled": (True, "ai_config"),
            "timeout": (5, "ai_config"),
        }),
        ({"analysis_mode": "turbo", "deep_investigation_enabled": "false", "finetune_enabled": 1,
          "ai_timeout_seconds": 4}, {
            "analysis_mode": ("auto", "env_default"),
            "deep_investigation_enabled": (True, "env_default"),
            "finetune_enabled": (False, "env_default"),
            "timeout": (300, "env_default"),
        }),
    ],
    ids=["absent", "stored", "invalid"],
)
async def test_stored_absent_and_invalid_rows(stores, monkeypatch, stored, expected):
    from app.core.config import settings
    from app.routers.app_settings import _load_ai_config
    from app.services.ai_config_resolver import get_effective_ai_config

    monkeypatch.setattr(settings, "ANALYSIS_MODE", "auto")
    monkeypatch.setattr(settings, "DEEP_INVESTIGATION_ENABLED", True)
    monkeypatch.setattr(settings, "FINETUNE_ENABLED", False)
    monkeypatch.setattr(settings, "AI_TIMEOUT_SECONDS", 300)
    sessions, _redis, _admin, write_row = stores
    await write_row(stored)

    async with sessions() as db:
        runtime = await get_effective_ai_config(db=db, fresh=True)
        page = await _load_ai_config(db)

    for key in ("analysis_mode", "deep_investigation_enabled", "finetune_enabled"):
        assert (runtime[key], runtime[f"{key}_source"]) == expected[key], key
        assert (page[key], page[f"{key}_source"]) == expected[key], key
    assert (runtime["timeout_seconds"], runtime["timeout_seconds_source"]) == expected["timeout"]
    assert (page["ai_timeout_seconds"], page["ai_timeout_seconds_source"]) == expected["timeout"]


def _stub_put(monkeypatch):
    from app.routers import app_settings as router_mod

    monkeypatch.setattr(router_mod, "log_settings_change", AsyncMock())
    monkeypatch.setattr(
        router_mod,
        "_ml_status",
        AsyncMock(return_value={
            "ml_model_available": False, "ml_model_accuracy": None, "ml_training_sample_count": 0,
            "ml_human_label_count": 0, "ml_human_label_floor": 50, "ml_maturity": "not_trained",
        }),
    )
    return router_mod


@pytest.mark.asyncio
async def test_put_then_get_round_trips_and_seeds_the_mode_without_expiry(stores, monkeypatch):
    from app.core.config import settings
    from app.models.schemas import AIConfigUpdate

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    router_mod = _stub_put(monkeypatch)
    sessions, redis, admin, write_row = stores
    await write_row(None)

    async with sessions() as db:
        saved = await router_mod.update_ai_config(
            payload=AIConfigUpdate(
                analysis_mode="rules", deep_investigation_enabled=False,
                finetune_enabled=True, ai_timeout_seconds=5,
            ),
            current_user=admin,
            db=db,
        )
    async with sessions() as db:
        read = await router_mod.get_ai_config(_=admin, db=db)

    for body in (saved, read):
        assert (body.analysis_mode, body.analysis_mode_source) == ("rules", "ai_config")
        assert (body.deep_investigation_enabled, body.deep_investigation_enabled_source) == (False, "ai_config")
        assert (body.finetune_enabled, body.finetune_enabled_source) == (True, "ai_config")
        assert (body.ai_timeout_seconds, body.ai_timeout_seconds_source) == (5, "ai_config")
    assert await redis.get("config:analysis_mode") == "rules"
    assert await redis.ttl("config:analysis_mode") == -1  # no expiry


@pytest.mark.asyncio
async def test_a_v1_cache_entry_is_ignored(stores):
    import json

    from app.services.ai_config_resolver import get_effective_ai_config

    sessions, redis, _admin, write_row = stores
    await write_row({"analysis_mode": "ml", "deep_investigation_enabled": False})
    await redis.set("config:ai_effective", json.dumps({"provider": "v1-pod", "deep_investigation_enabled": True}))

    async with sessions() as db:
        cfg = await get_effective_ai_config(db=db)

    assert cfg["deep_investigation_enabled"] is False
    assert cfg["analysis_mode"] == "ml"
    assert await redis.exists("config:ai_effective:v2") == 1
    assert 0 < await redis.ttl("config:ai_effective:v2") <= 60


@pytest.mark.asyncio
async def test_a_flushed_redis_is_reseeded_from_the_stored_row(stores):
    from app.services.analysis_router import ConfiguredMode, resolve_configured_analysis_mode
    from app.services.deep_investigation_gate import deep_investigation_gate

    sessions, redis, _admin, write_row = stores
    await write_row({"analysis_mode": "rules", "deep_investigation_enabled": True})

    async with sessions() as db:
        assert await resolve_configured_analysis_mode(db) == ConfiguredMode("rules", "ai_config")
        assert await redis.ttl("config:analysis_mode") == -1
        await redis.delete(*_KEYS)  # the flush
        assert await resolve_configured_analysis_mode(db) == ConfiguredMode("rules", "ai_config")
        decision = await deep_investigation_gate(db)

    assert await redis.get("config:analysis_mode") == "rules"
    assert await redis.ttl("config:analysis_mode") == -1
    assert decision.allowed is False and decision.reason_code == "rules_mode"
