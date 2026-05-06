"""Unit tests for services.ai_config_resolver — the single source of truth for AI config.

CLAUDE.md mandates a strict precedence:

  1. Database overrides (``AppSetting.key == "ai_config"``)
  2. Secret refs (``secret_service.read_secret("ai_config", <key>)``)
  3. Environment defaults (from ``settings``)

…with a 60-second Redis cache. The tests below codify the contract every
caller of ``get_effective_ai_config()`` depends on:

  - Env defaults appear when nothing else is configured.
  - DB overrides win over env for every documented field.
  - A *partial* DB row falls through to env for missing keys.
  - Secrets override env-only API keys, but an empty/None secret never
    clobbers a real env value.
  - Redis cache hits short-circuit the DB entirely.
  - Cache misses write through with TTL == ``_CACHE_TTL``.
  - ``invalidate_ai_config_cache()`` deletes the key so the next call reloads.
  - Redis or Postgres failures must NOT raise — the resolver degrades to env.
"""
from __future__ import annotations

import json
import time as _time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("asyncpg")

from app.services import ai_config_resolver as resolver  # noqa: E402
from app.services.ai_config_resolver import (  # noqa: E402
    _CACHE_KEY,
    _CACHE_TTL,
    get_effective_ai_config,
    invalidate_ai_config_cache,
)


# ── helpers ──────────────────────────────────────────────────────────────────


class _AsyncSessionCtx:
    """Async context manager that yields a pre-built db mock from `async with`."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_db(row=None):
    """Mock AsyncSession whose .execute(...) returns the given AppSetting row."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    db.execute = AsyncMock(return_value=result)
    return db


def _patch_redis(monkeypatch, fake_redis):
    """Make every ``from app.db.redis_client import get_redis`` return fake_redis."""
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)


def _patch_db(monkeypatch, db):
    """Stub ``from app.db.postgres import AsyncSessionLocal`` with a mock factory."""
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        MagicMock(return_value=_AsyncSessionCtx(db)),
    )


def _patch_secrets(monkeypatch, **values):
    """Stub ``read_secret(db, scope, key_name)`` to return values[key_name] or None."""

    async def _read_secret(_db, _scope, key_name):
        return values.get(key_name)

    monkeypatch.setattr("app.services.secret_service.read_secret", _read_secret)


def _set_env_defaults(monkeypatch):
    """Pin settings.* to known values so env-default assertions are deterministic."""
    monkeypatch.setattr(resolver.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(resolver.settings, "LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(resolver.settings, "LLM_TEMPERATURE", 0.1)
    monkeypatch.setattr(resolver.settings, "LLM_MAX_TOKENS", 4096)
    monkeypatch.setattr(resolver.settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(resolver.settings, "AI_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(resolver.settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(resolver.settings, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_API_KEY", None)


# ── env defaults (no DB row, no secrets, Redis empty) ────────────────────────


@pytest.mark.asyncio
async def test_env_defaults_used_when_db_empty_and_no_secrets(monkeypatch, fake_redis):
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(monkeypatch)

    cfg = await get_effective_ai_config()

    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5:7b"
    assert cfg["temperature"] == 0.1
    assert cfg["max_tokens"] == 4096
    assert cfg["base_url"] is None
    assert cfg["offline_mode"] is True
    assert cfg["timeout_seconds"] == 300
    assert cfg["openai_api_key"] is None
    assert cfg["google_api_key"] is None
    assert cfg["anthropic_api_key"] is None


# ── precedence: DB overrides win over env ────────────────────────────────────


@pytest.mark.asyncio
async def test_db_overrides_win_over_env_for_all_documented_keys(monkeypatch, fake_redis):
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)

    overrides = {
        "llm_provider": "openai",
        "llm_model": "gpt-4o",
        "llm_temperature": 0.7,
        "llm_max_tokens": 1024,
        "base_url": "https://api.openai.com/v1",
        "ai_offline_mode": False,
        "ai_timeout_seconds": 90,
    }
    row = MagicMock()
    row.value = overrides
    _patch_db(monkeypatch, _make_db(row=row))
    _patch_secrets(monkeypatch)

    cfg = await get_effective_ai_config()

    assert cfg["provider"] == "openai"
    assert cfg["model"] == "gpt-4o"
    assert cfg["temperature"] == 0.7
    assert cfg["max_tokens"] == 1024
    assert cfg["base_url"] == "https://api.openai.com/v1"
    assert cfg["offline_mode"] is False
    assert cfg["timeout_seconds"] == 90


@pytest.mark.asyncio
async def test_partial_db_override_falls_through_to_env_for_missing_keys(
    monkeypatch, fake_redis
):
    """A DB row that only sets `llm_provider` must keep env defaults for everything else."""
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)

    row = MagicMock()
    row.value = {"llm_provider": "openai"}
    _patch_db(monkeypatch, _make_db(row=row))
    _patch_secrets(monkeypatch)

    cfg = await get_effective_ai_config()

    assert cfg["provider"] == "openai"
    assert cfg["model"] == "qwen2.5:7b"
    assert cfg["temperature"] == 0.1
    assert cfg["max_tokens"] == 4096
    assert cfg["timeout_seconds"] == 300


@pytest.mark.asyncio
async def test_db_row_with_falsy_value_dict_is_ignored(monkeypatch, fake_redis):
    """``if row and row.value`` — an empty dict is falsy and must not erase env defaults."""
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)

    row = MagicMock()
    row.value = {}
    _patch_db(monkeypatch, _make_db(row=row))
    _patch_secrets(monkeypatch)

    cfg = await get_effective_ai_config()

    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5:7b"


# ── precedence: secrets override env-only api-key fields ─────────────────────


@pytest.mark.asyncio
async def test_secrets_override_env_api_keys(monkeypatch, fake_redis):
    _set_env_defaults(monkeypatch)
    monkeypatch.setattr(resolver.settings, "OPENAI_API_KEY", "env-openai")
    monkeypatch.setattr(resolver.settings, "GOOGLE_API_KEY", "env-google")
    monkeypatch.setattr(resolver.settings, "ANTHROPIC_API_KEY", "env-anthropic")

    _patch_redis(monkeypatch, fake_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(
        monkeypatch,
        openai_api_key="db-openai",
        google_api_key="db-google",
        anthropic_api_key="db-anthropic",
    )

    cfg = await get_effective_ai_config()

    assert cfg["openai_api_key"] == "db-openai"
    assert cfg["google_api_key"] == "db-google"
    assert cfg["anthropic_api_key"] == "db-anthropic"


@pytest.mark.asyncio
async def test_missing_secret_keeps_env_value(monkeypatch, fake_redis):
    """``read_secret`` returns None → env value is preserved, not nullified."""
    _set_env_defaults(monkeypatch)
    monkeypatch.setattr(resolver.settings, "OPENAI_API_KEY", "env-openai")

    _patch_redis(monkeypatch, fake_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(monkeypatch)  # all returns are None

    cfg = await get_effective_ai_config()
    assert cfg["openai_api_key"] == "env-openai"


@pytest.mark.asyncio
async def test_empty_string_secret_does_not_override_env(monkeypatch, fake_redis):
    """``if secret:`` — an empty stored secret must NOT clobber a real env value."""
    _set_env_defaults(monkeypatch)
    monkeypatch.setattr(resolver.settings, "OPENAI_API_KEY", "env-openai")

    _patch_redis(monkeypatch, fake_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(monkeypatch, openai_api_key="")

    cfg = await get_effective_ai_config()
    assert cfg["openai_api_key"] == "env-openai"


# ── Redis cache (60s TTL) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_hit_short_circuits_db(monkeypatch, fake_redis):
    """If Redis already has a cached entry, the resolver must NOT open a DB session."""
    cached = {"provider": "cached-provider", "model": "cached-model"}
    await fake_redis.set(_CACHE_KEY, json.dumps(cached))
    _patch_redis(monkeypatch, fake_redis)

    # AsyncSessionLocal must never even be looked up on a cache hit.
    bomb = MagicMock(side_effect=AssertionError("DB must not be touched on cache hit"))
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", bomb)

    cfg = await get_effective_ai_config()

    assert cfg == cached
    bomb.assert_not_called()


@pytest.mark.asyncio
async def test_cache_miss_writes_through_with_ttl(monkeypatch, fake_redis):
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(monkeypatch)

    cfg = await get_effective_ai_config()

    raw = await fake_redis.get(_CACHE_KEY)
    assert raw is not None, "cache must be populated after a miss"
    stored = json.loads(raw)
    assert stored == cfg

    # TTL must be bounded by _CACHE_TTL (60s).
    expiry = fake_redis._expiry.get(_CACHE_KEY)
    assert expiry is not None, "setex must record a TTL"
    remaining = expiry - _time.monotonic()
    assert 0 < remaining <= _CACHE_TTL


@pytest.mark.asyncio
async def test_invalidate_clears_cache_so_next_call_reloads(monkeypatch, fake_redis):
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(monkeypatch)

    # Prime the cache with stale content.
    await fake_redis.set(_CACHE_KEY, json.dumps({"provider": "stale"}))
    assert await fake_redis.get(_CACHE_KEY) is not None

    await invalidate_ai_config_cache()

    assert await fake_redis.get(_CACHE_KEY) is None
    cfg = await get_effective_ai_config()
    # Reloaded fresh — env default, not the stale "stale" provider.
    assert cfg["provider"] == "ollama"


@pytest.mark.asyncio
async def test_cached_value_round_trips_to_subsequent_call(monkeypatch, fake_redis):
    """Two consecutive calls return the same dict; the second is served from cache."""
    _set_env_defaults(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)

    row = MagicMock()
    row.value = {"llm_provider": "gemini", "llm_model": "gemini-1.5-pro"}
    _patch_db(monkeypatch, _make_db(row=row))
    _patch_secrets(monkeypatch, google_api_key="g-key")

    first = await get_effective_ai_config()
    second = await get_effective_ai_config()  # cache hit

    assert first == second
    assert first["provider"] == "gemini"
    assert first["model"] == "gemini-1.5-pro"
    assert first["google_api_key"] == "g-key"


# ── Graceful degradation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_failure_does_not_raise_and_still_loads_from_db(monkeypatch):
    """``get_redis()`` raising must NOT propagate; env defaults still surface."""
    _set_env_defaults(monkeypatch)

    def _broken_redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.db.redis_client.get_redis", _broken_redis)
    _patch_db(monkeypatch, _make_db(row=None))
    _patch_secrets(monkeypatch)

    cfg = await get_effective_ai_config()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_db_failure_falls_through_to_env_defaults(monkeypatch, fake_redis):
    """If opening AsyncSessionLocal raises, env defaults must still be returned."""
    _set_env_defaults(monkeypatch)
    monkeypatch.setattr(resolver.settings, "OPENAI_API_KEY", "env-openai")

    _patch_redis(monkeypatch, fake_redis)

    def _broken_session(*_a, **_kw):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _broken_session)

    cfg = await get_effective_ai_config()
    assert cfg["provider"] == "ollama"
    assert cfg["temperature"] == 0.1
    assert cfg["openai_api_key"] == "env-openai"


@pytest.mark.asyncio
async def test_invalidate_swallows_redis_failure(monkeypatch):
    """``invalidate_ai_config_cache()`` must never propagate Redis errors."""

    def _broken_redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.db.redis_client.get_redis", _broken_redis)

    # Must not raise.
    await invalidate_ai_config_cache()
