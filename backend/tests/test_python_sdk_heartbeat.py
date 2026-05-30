"""Tests for the Python SDK's ``LiveStream`` heartbeat behaviour.

The heartbeat enqueues a ``live_heartbeat`` event whenever the SDK has
been silent for ``HEARTBEAT_INTERVAL`` seconds, so the server's Redis
``last_event_at`` field stays fresh and the idle-session reaper doesn't
falsely close a long-running session. Two contracts to pin:

  1. Heartbeat IS emitted when the SDK has been silent past the
     interval (returns True + a payload lands on the queue).
  2. Heartbeat is SUPPRESSED when a real event was recent (returns
     False, queue unchanged).

We test ``_maybe_emit_heartbeat`` directly rather than driving the
``_heartbeat_loop`` driver so timing is fully deterministic — no real
``asyncio.sleep`` involved.

The SDK lives at ``client/testlookup_reporter.py``; we add its directory
to ``sys.path`` so the test runs without a separate install step.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


_CLIENT_DIR = Path(__file__).resolve().parents[2] / "client"
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))


def _build_stream():
    """Construct a ``LiveStream`` with minimum-viable kwargs. We never
    enter the async context manager — the tests drive the helper
    method directly, so no HTTP traffic occurs."""
    os.environ.setdefault("TESTLOOKUP_URL", "http://test.invalid")
    os.environ.setdefault("TESTLOOKUP_API_KEY", "tlk_test_key")
    import testlookup_reporter
    stream = testlookup_reporter.LiveStream(
        api_key="tlk_test_key",
        run_id="run-test",
        base_url="http://test.invalid",
    )
    return stream, testlookup_reporter


@pytest.mark.asyncio
async def test_heartbeat_emitted_when_session_is_idle():
    """No real events for longer than ``HEARTBEAT_INTERVAL`` → heartbeat
    enqueued with ``event_type='live_heartbeat'`` and a timestamp."""
    stream, _ = _build_stream()
    # Pretend the last real event was 10 minutes ago.
    stream._last_real_enqueue_ms = (time.time() - 600) * 1_000

    emitted = await stream._maybe_emit_heartbeat()

    assert emitted is True
    assert not stream._queue.empty()
    event = stream._queue.get_nowait()
    assert event["event_type"] == "live_heartbeat"
    assert "timestamp_ms" in event


@pytest.mark.asyncio
async def test_heartbeat_suppressed_while_session_is_busy():
    """A real event was just enqueued → the next heartbeat tick must NOT
    emit. Otherwise a healthy stream gets spammed with no-ops."""
    stream, _ = _build_stream()
    stream._last_real_enqueue_ms = time.time() * 1_000  # "right now"

    emitted = await stream._maybe_emit_heartbeat()

    assert emitted is False
    assert stream._queue.empty()


@pytest.mark.asyncio
async def test_record_advances_last_real_enqueue_marker():
    """``record()`` must mark the stream as recently active so the next
    heartbeat tick suppresses itself. Without this, the heartbeat
    would still fire even during a flurry of test events."""
    stream, _ = _build_stream()
    stream._last_real_enqueue_ms = (time.time() - 600) * 1_000  # idle
    before = stream._last_real_enqueue_ms

    await stream.record("test_x", "PASSED", 5)

    after = stream._last_real_enqueue_ms
    assert after > before
    # And: the freshly-advanced marker now suppresses heartbeats.
    emitted = await stream._maybe_emit_heartbeat()
    assert emitted is False


@pytest.mark.asyncio
async def test_heartbeat_does_not_advance_busy_marker():
    """Heartbeats must NOT update the "last real activity" marker. If
    they did, a session with no real events would still appear busy
    after the first heartbeat fired — and the next heartbeat would
    then suppress itself forever."""
    stream, _ = _build_stream()
    stream._last_real_enqueue_ms = (time.time() - 600) * 1_000
    before = stream._last_real_enqueue_ms

    await stream._maybe_emit_heartbeat()

    assert stream._last_real_enqueue_ms == before
