"""Tests for the ``live_heartbeat`` event contract.

The heartbeat is emitted by the SDK during long inter-test gaps so the
server-side ``last_event_at`` field stays fresh — that's what the
``close_stale_live_sessions`` reaper reads to decide whether a session
is dead. The contract has three load-bearing properties:

  1. A heartbeat in a batch DOES update Redis ``last_event_at``.
  2. A heartbeat DOES NOT increment any pass/fail/skip/broken counter
     (it carries no test payload).
  3. A heartbeat DOES NOT push to the ``LIVE_TESTCASES`` list (which is
     the source of truth for per-test rows the worker persists).

Together these mean the reaper sees the session as fresh while the
per-run aggregates remain accurate.

The 4th property — that the live consumer ignores the message rather
than logging it as "unknown" — is exercised by reading the dispatcher
implementation directly.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakePipeline:
    """Mimics enough of ``redis.pipeline()`` for the ingest call path:
    captures every method call so the test can assert which Redis ops
    fired. ``execute()`` is awaitable.
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name):
        def _fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return _fn

    def __getattr__(self, name):
        return self._record(name)

    async def execute(self):
        self.calls.append(("execute", (), {}))
        return []

    def names_called(self) -> list[str]:
        return [c[0] for c in self.calls]


# ── _persist_event_batch contract ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_event_refreshes_last_event_at_without_counters():
    """A heartbeat-only batch must bump ``last_event_at`` (reaper signal)
    but not touch any counter or per-test list. That's the whole point
    of the event type."""
    from app.services import stream_service

    pipe = _FakePipeline()
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)

    with patch.object(stream_service, "get_redis", return_value=redis), \
         patch.object(stream_service, "publish_event_batch",
                      new=AsyncMock(return_value=1)), \
         patch.object(stream_service, "await_if_needed",
                      new=AsyncMock(side_effect=lambda x: x)):
        events = [SimpleNamespace(model_dump=lambda: {
            "event_type": "live_heartbeat",
            "timestamp_ms": 1_700_000_000_000,
        })]
        accepted = await stream_service._persist_event_batch(
            session_id="sess-1", run_id="run-1", events=events,
        )

    assert accepted == 1
    names = pipe.names_called()
    # last_event_at refresh is the only Redis write a heartbeat triggers.
    assert "hset" in names
    # No counter bumps and no test-list pushes — those would corrupt
    # /runs / /live aggregates.
    assert "hincrby" not in names
    assert "rpush" not in names


@pytest.mark.asyncio
async def test_test_result_still_bumps_counters_and_pushes_list():
    """Regression guard: making ``live_heartbeat`` a no-op must not have
    broken the real ``test_result`` path."""
    from app.services import stream_service

    pipe = _FakePipeline()
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)

    with patch.object(stream_service, "get_redis", return_value=redis), \
         patch.object(stream_service, "publish_event_batch",
                      new=AsyncMock(return_value=1)), \
         patch.object(stream_service, "await_if_needed",
                      new=AsyncMock(side_effect=lambda x: x)):
        events = [SimpleNamespace(model_dump=lambda: {
            "event_type": "test_result",
            "test_name": "test_x",
            "status": "PASSED",
            "duration_ms": 12,
        })]
        await stream_service._persist_event_batch(
            session_id="s", run_id="r", events=events,
        )

    names = pipe.names_called()
    assert "rpush" in names
    assert "hincrby" in names
    assert "hset" in names


# ── live consumer dispatcher ──────────────────────────────────────────────


def test_live_consumer_dispatcher_recognises_heartbeat_explicitly():
    """A future refactor that drops the explicit ``live_heartbeat`` branch
    would push heartbeats back through the "Unknown live event type"
    debug log. Pin the explicit branch — it's the signal that the
    consumer intentionally ignores heartbeats (no broadcast to WS, no
    state mutation)."""
    import inspect
    consumer_mod = importlib.import_module("app.streams.live_consumer")
    src = inspect.getsource(consumer_mod.LiveEventStreamConsumer._process)
    assert '"live_heartbeat"' in src or "'live_heartbeat'" in src, (
        "live_heartbeat must be explicit in the consumer's _process so "
        "it doesn't fall through to the 'Unknown live event type' log."
    )
