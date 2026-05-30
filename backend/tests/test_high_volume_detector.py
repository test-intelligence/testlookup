"""Unit tests for the Phase 4.1 high-volume detector.

The detector is a small, isolated Redis-backed counter — perfect for
focused tests that don't need a worker pool. Pins:

  1. ``record_test_events`` increments the current-minute counter
     and refreshes its TTL only on the first bump (matches the rate-
     limit convention so we don't reset TTL on every batch).
  2. The flag fires only when N consecutive PRIOR minutes are all
     over threshold — a 1-min spike is not enough.
  3. ``is_high_volume`` reads the flag; missing flag ⇒ False.
  4. Operator override beats the detector:
     - ``"on"``  ⇒ True even when no flag exists.
     - ``"off"`` ⇒ False even when the flag is set.
  5. ``HIGH_VOLUME_AUTO_DETECT_ENABLED=False`` short-circuits both
     write and read paths.
  6. Redis errors fail OPEN on the read (False = full fidelity).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _fake_redis():
    return SimpleNamespace(
        incrby=AsyncMock(return_value=1),
        expire=AsyncMock(return_value=True),
        get=AsyncMock(return_value=None),
        setex=AsyncMock(return_value=True),
        delete=AsyncMock(return_value=1),
    )


@pytest.fixture
def enabled(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "HIGH_VOLUME_AUTO_DETECT_ENABLED", True)
    monkeypatch.setattr(config.settings, "HIGH_VOLUME_TESTS_PER_MINUTE", 1000)
    monkeypatch.setattr(config.settings, "HIGH_VOLUME_CONSECUTIVE_MINUTES", 3)


@pytest.mark.asyncio
async def test_record_test_events_increments_counter(enabled):
    from app.services.high_volume_detector import record_test_events

    redis = _fake_redis()
    # ``record_test_events`` reads the post-INCRBY return to decide
    # whether to set EXPIRE — only on the first bump of the minute,
    # i.e. when ``new_count == count``. Mock returns the same value
    # so we exercise the first-bump TTL-setting branch.
    redis.incrby = AsyncMock(return_value=100)
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_test_events("proj-1", 100)

    redis.incrby.assert_awaited_once()
    args, _ = redis.incrby.call_args
    # Key includes project_id + minute bucket.
    assert "proj-1" in args[0]
    redis.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_test_events_skips_when_disabled(monkeypatch):
    """``HIGH_VOLUME_AUTO_DETECT_ENABLED=False`` shouldn't touch Redis."""
    from app.core import config
    from app.services.high_volume_detector import record_test_events
    monkeypatch.setattr(config.settings, "HIGH_VOLUME_AUTO_DETECT_ENABLED", False)

    redis = _fake_redis()
    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_test_events("proj-1", 100)

    redis.incrby.assert_not_awaited()
    redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_test_events_flips_flag_when_n_consecutive_over_threshold(enabled):
    """When the current minute pushes above threshold AND the 3
    previous minutes were also over threshold, ``setex`` writes the
    flag key. Pins the multi-bucket check."""
    from app.services.high_volume_detector import record_test_events

    redis = _fake_redis()
    # Current-minute INCRBY return is 1500 → over the 1000 threshold.
    redis.incrby = AsyncMock(return_value=1500)
    # Previous 3 buckets all return strings ≥ 1000.
    redis.get = AsyncMock(return_value=b"1500")

    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_test_events("proj-1", 1500)

    # Flag was written via SETEX with the flag key path.
    redis.setex.assert_awaited()
    args, _ = redis.setex.call_args
    assert "testlookup:high_volume:flagged:proj-1" in args[0]


@pytest.mark.asyncio
async def test_record_test_events_does_not_flip_flag_on_single_minute_spike(enabled):
    """One minute over threshold isn't enough — the detector requires
    N CONSECUTIVE over-threshold minutes. The previous-minute check
    failing must short-circuit the flag write."""
    from app.services.high_volume_detector import record_test_events

    redis = _fake_redis()
    redis.incrby = AsyncMock(return_value=2000)  # current minute over
    # First lookup (minute -1) returns 500 — under threshold.
    redis.get = AsyncMock(return_value=b"500")

    with patch("app.db.redis_client.get_redis", return_value=redis):
        await record_test_events("proj-1", 2000)

    # No SETEX call to the flag key.
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_high_volume_returns_true_when_flag_set(enabled):
    from app.services.high_volume_detector import is_high_volume

    redis = _fake_redis()
    # Override is None, flag is "1".
    redis.get = AsyncMock(side_effect=[None, b"1"])

    with patch("app.db.redis_client.get_redis", return_value=redis):
        result = await is_high_volume("proj-1")

    assert result is True


@pytest.mark.asyncio
async def test_is_high_volume_returns_false_when_flag_missing(enabled):
    from app.services.high_volume_detector import is_high_volume

    redis = _fake_redis()
    redis.get = AsyncMock(return_value=None)

    with patch("app.db.redis_client.get_redis", return_value=redis):
        result = await is_high_volume("proj-1")

    assert result is False


@pytest.mark.asyncio
async def test_is_high_volume_respects_operator_override_on(enabled):
    """``"on"`` override forces True even when no detector flag exists."""
    from app.services.high_volume_detector import is_high_volume

    redis = _fake_redis()
    # Override returns "on"; flag lookup never runs.
    redis.get = AsyncMock(return_value=b"on")

    with patch("app.db.redis_client.get_redis", return_value=redis):
        result = await is_high_volume("proj-1")

    assert result is True


@pytest.mark.asyncio
async def test_is_high_volume_respects_operator_override_off(enabled):
    """``"off"`` override forces False even when detector flag IS set."""
    from app.services.high_volume_detector import is_high_volume

    redis = _fake_redis()
    redis.get = AsyncMock(return_value=b"off")

    with patch("app.db.redis_client.get_redis", return_value=redis):
        result = await is_high_volume("proj-1")

    assert result is False


@pytest.mark.asyncio
async def test_is_high_volume_fails_open_on_redis_error(enabled):
    """A Redis hiccup must default to FULL FIDELITY (no sampling) —
    the cost of over-storing test_cases is far smaller than the cost
    of silently dropping them."""
    from app.services.high_volume_detector import is_high_volume

    redis = SimpleNamespace(get=AsyncMock(side_effect=ConnectionError("down")))
    with patch("app.db.redis_client.get_redis", return_value=redis):
        result = await is_high_volume("proj-1")

    assert result is False
