"""Regression guard: a zero-TTL key is expired at the boundary, not after it.

The defect
----------
``FakeRedis._is_expired`` compared with a strict ``>``::

    if key in self._expiry and time.monotonic() > self._expiry[key]:

``set(..., ex=0)`` stores an expiry of exactly ``time.monotonic()``, so at that
instant the key was *not* expired — it stayed alive until the clock ticked past
the value. Two tests papered over it with ``await asyncio.sleep(0.01)`` and a
comment reading "Need a tiny sleep to ensure monotonic clock advances".

On Windows that sleep is not enough. ``time.monotonic()`` there has ~15.6ms
granularity, so a 10ms sleep frequently returns before the clock ticks at all,
leaving ``monotonic() > expiry`` false and the key readable.
``test_cache_expired_key_returns_none`` failed **2 times in 12 runs** of
``test_phase5_hardening.py`` on otherwise-unchanged code, always on the slower
runs — the shape that reads as "someone's change broke this" when it is really
a coin flip. It cost a bisect against ``main`` before the timing showed itself.

``>=`` is both correct and deterministic: at the boundary the key IS expired,
which is what ``ex=0`` means and what the tests always claimed to assert. The
sleeps are gone with it.

What is guarded
---------------
The boundary itself, **with no sleep anywhere**. On the old strict ``>`` these
fail every time rather than two runs in twelve, so the guard cannot itself
become the flake it was written to remove.
"""
from __future__ import annotations

import time

import pytest

from tests.conftest import FakeRedis


@pytest.mark.asyncio
async def test_a_zero_ttl_key_is_gone_on_the_very_next_read():
    """No sleep: this is the exact instant the strict ``>`` kept the key alive."""
    redis = FakeRedis()
    await redis.set("k", "v", ex=0)

    assert await redis.get("k") is None, (
        "ex=0 means expired now; a strict > leaves the key readable until the "
        "monotonic clock happens to tick"
    )
    assert await redis.exists("k") == 0


@pytest.mark.asyncio
async def test_the_boundary_holds_when_the_clock_does_not_move():
    """Pin the clock so the result cannot depend on timer granularity at all.

    With ``time.monotonic`` frozen, a strict ``>`` can never expire anything —
    which is precisely the Windows behaviour, just made explicit instead of
    probabilistic.
    """
    redis = FakeRedis()
    frozen = time.monotonic()
    real_monotonic = time.monotonic
    try:
        time.monotonic = lambda: frozen  # type: ignore[assignment]
        await redis.set("k", "v", ex=0)
        assert await redis.get("k") is None, (
            "with a stopped clock the boundary is the only thing that can "
            "expire an ex=0 key"
        )
    finally:
        time.monotonic = real_monotonic  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_a_live_ttl_is_not_expired_early():
    """The other side of the boundary — ``>=`` must not expire a real TTL."""
    redis = FakeRedis()
    await redis.set("k", "v", ex=60)

    assert await redis.get("k") == b"v"
    assert await redis.exists("k") == 1


@pytest.mark.asyncio
async def test_expire_with_zero_seconds_also_takes_effect_immediately():
    """``expire(key, 0)`` shares the comparison, so it shares the fix."""
    redis = FakeRedis()
    await redis.set("k", "v")
    assert await redis.get("k") == b"v"

    assert await redis.expire("k", 0) is True
    assert await redis.get("k") is None
