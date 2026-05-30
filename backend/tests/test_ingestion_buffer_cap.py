"""Test for the Phase 2.1 LTRIM-bounded per-run buffer.

``_persist_event_batch`` writes each ``test_result`` event to a Redis
List (``testlookup:live:testcases:<run_id>``) so the persist worker can
drain it later. Before Phase 2.1 that list grew unbounded — a single
long-lived run could blow up Redis memory even while staying within
the per-project rate-limit budget.

The fix: ``LTRIM list -N -1`` on every batch, where N comes from
``settings.LIVE_BUFFER_MAX_EVENTS_PER_RUN`` (default 50K). This pins
that:

  1. The pipeline emits an ``LTRIM`` call when the cap is > 0.
  2. The cap value is read from the setting at call time (so an env
     change takes effect without a restart).
  3. Setting the cap to 0 disables the trim — needed by tests that
     don't want eviction.
  4. The trim keeps the NEWEST events (``LTRIM -N -1``), not the
     oldest. The dashboard cares about recent activity.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakePipeline:
    """Records every method call so the test can assert on the
    sequence + arguments rather than on Redis I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return _record

    async def execute(self):
        self.calls.append(("execute", (), {}))
        return []


@pytest.mark.asyncio
async def test_persist_event_batch_emits_ltrim_for_buffer_cap(monkeypatch):
    """Single PASSED event with cap=50K → pipeline includes LTRIM."""
    from app.core import config
    monkeypatch.setattr(config.settings, "LIVE_BUFFER_MAX_EVENTS_PER_RUN", 50_000)

    pipe = _FakePipeline()
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)

    # _persist_event_batch also calls ``publish_event_batch`` on the
    # producer module; stub it out to keep the test focused on the
    # buffer / counter pipeline.
    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.streams.producer.publish_event_batch", AsyncMock(return_value=1)):
        from app.services.stream_service import _persist_event_batch
        events = [{"event_type": "test_result", "test_name": "t1", "status": "PASSED"}]
        await _persist_event_batch(session_id="s1", run_id="run-x", events=events)

    # Pin the LTRIM call — the cap should keep the newest 50K entries.
    ltrim_calls = [c for c in pipe.calls if c[0] == "ltrim"]
    assert len(ltrim_calls) == 1, f"expected exactly one ltrim, got: {pipe.calls}"
    name, args, kwargs = ltrim_calls[0]
    # Signature: ltrim(list_key, -N, -1). The list_key includes the
    # run_id so we know the right run was trimmed.
    list_key, start, end = args
    assert "run-x" in list_key
    assert start == -50_000
    assert end == -1


@pytest.mark.asyncio
async def test_persist_event_batch_skips_ltrim_when_cap_is_zero(monkeypatch):
    """Cap=0 disables the trim — legacy behaviour for tests and any
    deploy that doesn't want eviction yet."""
    from app.core import config
    monkeypatch.setattr(config.settings, "LIVE_BUFFER_MAX_EVENTS_PER_RUN", 0)

    pipe = _FakePipeline()
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)

    with patch("app.db.redis_client.get_redis", return_value=redis), \
         patch("app.streams.producer.publish_event_batch", AsyncMock(return_value=1)):
        from app.services.stream_service import _persist_event_batch
        events = [{"event_type": "test_result", "test_name": "t1", "status": "PASSED"}]
        await _persist_event_batch(session_id="s1", run_id="run-x", events=events)

    assert not any(c[0] == "ltrim" for c in pipe.calls), (
        f"ltrim should be absent when cap=0, got: {pipe.calls}"
    )
