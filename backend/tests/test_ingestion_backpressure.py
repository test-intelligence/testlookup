"""Unit tests for the Redis-memory backpressure gate.

The gate is the second admission layer (Phase 1 of
``docs/SCALABLE_INGESTION_DESIGN.md``). It complements the per-project
rate limit by enforcing a global cap when EVERY project is within
budget but aggregate load is still pushing Redis toward OOM.

Pins:

  1. Under threshold → no raise.
  2. Over the percentage threshold (with ``maxmemory`` set) → 503.
  3. Over the absolute-bytes threshold (when ``maxmemory`` is 0) → 503.
  4. ``Retry-After`` header set + cap on reject counter.
  5. Both threshold settings = 0 disables the gate.
  6. Redis ``INFO`` failure fails OPEN.
  7. The snapshot is cached for ~5s to keep the hot path cheap; back-
     to-back calls within the cache window share one INFO read.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


def _fake_redis_with_info(used_memory: int, maxmemory: int):
    """An AsyncMock Redis whose ``INFO memory`` returns the supplied
    used/max bytes. The rest of the surface is no-op so the reject
    counter increment doesn't blow up."""
    redis = SimpleNamespace(
        info=AsyncMock(return_value={
            "used_memory": used_memory,
            "maxmemory": maxmemory,
        }),
        incr=AsyncMock(return_value=1),
        expire=AsyncMock(return_value=True),
    )
    return redis


@pytest.fixture(autouse=True)
def _clear_memory_cache():
    """Reset the module-level snapshot cache between tests so one
    test's mocked snapshot doesn't leak into the next."""
    import app.services.ingestion_backpressure as m
    m._CACHE = None
    yield
    m._CACHE = None


@pytest.mark.asyncio
async def test_backpressure_under_threshold_passes(monkeypatch):
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 75.0)
    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_ABSOLUTE_BYTES", 0)
    redis = _fake_redis_with_info(used_memory=50_000_000, maxmemory=100_000_000)  # 50% → fine
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await enforce_redis_memory_backpressure()  # no raise


@pytest.mark.asyncio
async def test_backpressure_pct_threshold_triggers_503(monkeypatch):
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 75.0)
    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_ABSOLUTE_BYTES", 0)
    redis = _fake_redis_with_info(used_memory=80_000_000, maxmemory=100_000_000)  # 80% > 75%
    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await enforce_redis_memory_backpressure()
    assert exc.value.status_code == 503
    assert exc.value.headers.get("Retry-After") == "5"
    # The reject path increments the global reject counter.
    redis.incr.assert_awaited_once()


@pytest.mark.asyncio
async def test_backpressure_absolute_threshold_triggers_503_when_maxmemory_zero(monkeypatch):
    """When ``maxmemory`` is 0 (default Redis on a dev machine), the
    percentage threshold is meaningless. The absolute-bytes ceiling
    must still gate the path."""
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 75.0)
    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_ABSOLUTE_BYTES", 1_000_000)
    redis = _fake_redis_with_info(used_memory=2_000_000, maxmemory=0)  # over abs cap
    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await enforce_redis_memory_backpressure()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_backpressure_disabled_when_both_thresholds_zero(monkeypatch):
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 0)
    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_ABSOLUTE_BYTES", 0)
    redis = _fake_redis_with_info(used_memory=999_999_999, maxmemory=1)  # comically over
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await enforce_redis_memory_backpressure()  # no raise

    # No INFO read either — disabled short-circuits before any Redis op.
    redis.info.assert_not_awaited()


@pytest.mark.asyncio
async def test_backpressure_fails_open_on_redis_error(monkeypatch):
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 75.0)
    redis = SimpleNamespace(info=AsyncMock(side_effect=ConnectionError("redis down")))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        # Fail-OPEN: must NOT raise.
        await enforce_redis_memory_backpressure()


@pytest.mark.asyncio
async def test_backpressure_caches_snapshot_to_avoid_redis_storm(monkeypatch):
    """Hot path: 100s of HTTP requests/sec mustn't each fire an INFO.
    Pin that consecutive calls inside the 5s cache window share ONE
    underlying ``INFO`` read."""
    from app.services.ingestion_backpressure import enforce_redis_memory_backpressure
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_REDIS_MEMORY_THRESHOLD_PCT", 75.0)
    redis = _fake_redis_with_info(used_memory=10, maxmemory=100)  # under threshold
    with patch("app.db.redis_client.get_redis", return_value=redis):
        for _ in range(20):
            await enforce_redis_memory_backpressure()

    # 20 enforce calls → 1 INFO call (cached). Sharply lower than 1:1.
    assert redis.info.await_count == 1
