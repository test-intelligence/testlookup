"""AIC-04 (G-C line): the Ask AI chat resolves the analysis mode through the one resolver.

``conversation._configured_mode`` read the ``ai_config`` row itself, with its
own session, and fell back to ``ANALYSIS_MODE``. It now delegates to
``analysis_router.resolve_configured_analysis_mode`` (Redis accelerator -> the
stored row -> env -> ``auto``), so the chat can never disagree with the
pipeline about the mode. The real resolver runs here over a fake Redis and a
fake effective config; nothing in between is mocked.
"""
from __future__ import annotations

import pytest

from app.agents import conversation as conv
from app.core.config import settings


class _FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.sets: list[tuple] = []

    async def get(self, _key):
        return self.value

    async def set(self, key, value, **kwargs):
        self.sets.append((key, value, kwargs))


@pytest.fixture
def env_auto(monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "auto")


def _redis(monkeypatch, value=None) -> _FakeRedis:
    from app.db import redis_client

    fake = _FakeRedis(value)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


def _effective(monkeypatch, mode, source):
    from app.services import ai_config_resolver

    async def _cfg(*_a, **_k):
        return {"analysis_mode": mode, "analysis_mode_source": source}

    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", _cfg)


@pytest.mark.asyncio
async def test_the_redis_accelerator_wins_over_env(monkeypatch, env_auto):
    _redis(monkeypatch, "rules")
    _effective(monkeypatch, "llm", "ai_config")  # never reached on a hit
    assert await conv._configured_mode() == "rules"


@pytest.mark.asyncio
async def test_a_cache_miss_reads_the_stored_row_and_reseeds_without_expiry(monkeypatch, env_auto):
    redis = _redis(monkeypatch, None)
    _effective(monkeypatch, "RULES", "ai_config")
    assert await conv._configured_mode() == "rules"
    assert redis.sets == [("config:analysis_mode", "rules", {})]


@pytest.mark.asyncio
async def test_nothing_stored_is_the_env_default(monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_MODE", "llm")
    redis = _redis(monkeypatch, None)
    _effective(monkeypatch, "llm", "env_default")
    assert await conv._configured_mode() == "llm"
    assert redis.sets == [], "an env default is never seeded"


@pytest.mark.asyncio
async def test_a_resolver_failure_falls_back_to_env(monkeypatch):
    from app.services import analysis_router

    async def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(settings, "ANALYSIS_MODE", "Rules")
    monkeypatch.setattr(analysis_router, "resolve_configured_analysis_mode", _boom)
    assert await conv._configured_mode() == "rules"
