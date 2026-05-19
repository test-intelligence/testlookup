"""Unit tests for the per-project live-stream ingest rate limit.

The limiter is the first of two admission gates (Phase 1 of
``docs/SCALABLE_INGESTION_DESIGN.md``). These tests pin:

  1. Under-budget calls pass silently (no raise).
  2. Over-budget calls raise 429 with ``Retry-After`` set.
  3. The reject counter under ``testlookup:rate:reject:<bucket>`` is
     incremented when a request is refused — that's what
     ``/health/ingestion`` reads to surface the reject rate.
  4. Setting the limit to 0 disables the gate (used in tests and dev).
  5. Redis failures fail OPEN — we'd rather over-accept than block
     when our gate's own backend is degraded.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


def _fake_redis(incrby_return: int):
    """Build an AsyncMock Redis stub for the rate-limit code path."""
    redis = SimpleNamespace(
        incrby=AsyncMock(return_value=incrby_return),
        expire=AsyncMock(return_value=True),
        incr=AsyncMock(return_value=1),
    )
    return redis


@pytest.mark.asyncio
async def test_rate_limit_passes_under_budget():
    from app.services.ingestion_rate_limit import enforce_ingest_rate_limit

    redis = _fake_redis(incrby_return=5)  # 5 of 200 → fine
    with patch("app.db.redis_client.get_redis", return_value=redis):
        # No raise expected. Pin that by simply awaiting.
        await enforce_ingest_rate_limit("proj-1")

    # Always charges the bucket; only sets expire on the FIRST charge
    # of a minute (count == cost). Pin both behaviours.
    redis.incrby.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_sets_expire_only_on_first_charge():
    from app.services.ingestion_rate_limit import enforce_ingest_rate_limit

    # First call returns ``cost`` (==1) — sets EXPIRE.
    redis = _fake_redis(incrby_return=1)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await enforce_ingest_rate_limit("proj-1")
    redis.expire.assert_awaited_once()

    # Second call returns >1 — does NOT touch EXPIRE again.
    redis = _fake_redis(incrby_return=2)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await enforce_ingest_rate_limit("proj-1")
    redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_raises_429_when_over_budget():
    from app.services.ingestion_rate_limit import enforce_ingest_rate_limit
    from app.core.config import settings

    # 201 > default 200 → over budget.
    redis = _fake_redis(incrby_return=settings.INGEST_RATE_LIMIT_PER_MINUTE + 1)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await enforce_ingest_rate_limit("proj-1")

    assert exc.value.status_code == 429
    # The ``Retry-After`` header is the contract with the SDK — pin its
    # presence + that it's a positive integer string.
    assert "Retry-After" in exc.value.headers
    assert int(exc.value.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_rate_limit_increments_reject_counter_on_over_budget():
    """The /health/ingestion endpoint reads
    ``testlookup:rate:reject:<minute>`` to surface the reject rate.
    Pin that the limiter writes there when it rejects."""
    from app.services.ingestion_rate_limit import enforce_ingest_rate_limit
    from app.core.config import settings

    redis = _fake_redis(incrby_return=settings.INGEST_RATE_LIMIT_PER_MINUTE + 5)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        with pytest.raises(HTTPException):
            await enforce_ingest_rate_limit("proj-1")

    # ``incr`` is only called on the reject path; success path uses
    # ``incrby``. Confirm the over-budget branch fired.
    redis.incr.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_disabled_when_setting_is_zero(monkeypatch):
    """``INGEST_RATE_LIMIT_PER_MINUTE=0`` short-circuits the limiter
    so dev / tests can fire arbitrary load without instrumenting Redis."""
    from app.services.ingestion_rate_limit import enforce_ingest_rate_limit
    from app.core import config

    monkeypatch.setattr(config.settings, "INGEST_RATE_LIMIT_PER_MINUTE", 0)
    redis = _fake_redis(incrby_return=9999)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await enforce_ingest_rate_limit("proj-1")

    # Redis must NOT be touched at all when disabled.
    redis.incrby.assert_not_awaited()
    redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_fails_open_when_redis_is_unreachable():
    """The limiter is a gate; if its own backend is down the right
    move is to LET TRAFFIC IN, not block it. The downstream
    backpressure check + readiness probe will eventually surface the
    Redis outage."""
    from app.services.ingestion_rate_limit import enforce_ingest_rate_limit

    redis = SimpleNamespace(incrby=AsyncMock(side_effect=ConnectionError("redis down")))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        # Must NOT raise.
        await enforce_ingest_rate_limit("proj-1")
