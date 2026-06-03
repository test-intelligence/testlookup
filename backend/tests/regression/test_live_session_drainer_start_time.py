"""Regression pins for the live_session_drainer review (review/live-session-drainer).

Fix: the Phase 4.5 drainer created the ``TestRun`` row with ``start_time=now``
(the first 30s drain tick). Close-time ``upsert_test_run`` only sets
``start_time`` when it CREATES the row — on an already-existing row (one the
drainer materialised) it updates totals/end_time but leaves ``start_time``
alone. So a drained run (any run lasting > the drain interval) kept a start time
skewed up to a full tick late, breaking duration/velocity metrics — even though
the real start is available in the Redis live-state hash. The drainer now
resolves ``started_at`` from that hash via ``_resolved_started_at``.
"""
from __future__ import annotations

import json
import sys
import types
import uuid as u
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.regression


# ── pure helpers ────────────────────────────────────────────────────────────

def test_resolved_started_at_prefers_state_then_falls_back():
    from app.services.live_session_drainer import _resolved_started_at

    now = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    iso = "2026-06-03T01:02:03+00:00"
    assert _resolved_started_at({"started_at": iso}, now) == datetime.fromisoformat(iso)
    assert _resolved_started_at({}, now) == now
    assert _resolved_started_at({"started_at": "not-a-date"}, now) == now
    assert _resolved_started_at({"started_at": None}, now) == now


def test_event_to_row_status_suite_and_truncation():
    from app.models.postgres import TestStatus
    from app.services.live_session_drainer import _event_to_row

    run = u.uuid4()
    row = _event_to_row(
        {"test_name": "t", "class_name": "C", "status": "passed"},
        run_uuid=run, default_suite="Smoke",
    )
    assert row["status"] == TestStatus.PASSED.value  # normalised + uppercased
    assert row["suite_name"] == "Smoke"               # session fallback
    assert row["test_run_id"] == run

    bad = _event_to_row({"test_name": "t", "status": "weird"}, run_uuid=run, default_suite=None)
    assert bad["status"] == TestStatus.UNKNOWN.value

    long = _event_to_row({"test_name": "x" * 2000, "status": "PASSED"}, run_uuid=run, default_suite=None)
    assert len(long["test_name"]) == 1000


# ── end-to-end drain (mocked redis + db) ────────────────────────────────────

class _FakeRedis:
    def __init__(self, entries):
        self.entries = entries
        self.ltrim_calls = []

    async def set(self, k, v, nx=None, ex=None):  # lock acquired
        return True

    async def delete(self, k):
        return 1

    async def lrange(self, k, a, b):
        return self.entries[a:] if b == -1 else self.entries[a:b + 1]

    async def ltrim(self, k, a, b):
        self.ltrim_calls.append((a, b))


class _FakeSession:
    def __init__(self):
        self.added = []
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, *_a, **_k):
        # both the TestRun SELECT (→ None) and the TestCase INSERT land here
        from types import SimpleNamespace
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, obj):
        self.added.append(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


@pytest.mark.asyncio
async def test_drain_creates_run_with_real_session_start(monkeypatch):
    from app.services import live_session_drainer as drn

    # avoid importing stream_service's heavy deps (per the module docstring)
    stub = types.ModuleType("app.services.stream_service")
    stub.canonical_test_run_uuid = lambda run_id: u.uuid5(u.NAMESPACE_DNS, run_id)
    monkeypatch.setitem(sys.modules, "app.services.stream_service", stub)

    started_iso = "2026-06-03T01:02:03+00:00"
    state = {
        "passed": 1, "failed": 1, "skipped": 0, "broken": 0, "total": 2,
        "started_at": started_iso, "suite_name": "Smoke",
    }
    monkeypatch.setattr(drn.RedisLiveRunState, "get", AsyncMock(return_value=state))

    entries = [
        json.dumps({"test_name": "t1", "status": "PASSED"}),
        json.dumps({"test_name": "t2", "status": "FAILED"}),
    ]
    fake_redis = _FakeRedis(entries)
    monkeypatch.setattr(drn, "get_redis", lambda: fake_redis)

    fake_db = _FakeSession()
    monkeypatch.setattr(drn, "AsyncSessionLocal", lambda: fake_db)
    monkeypatch.setattr(drn.settings, "PERSIST_LIVE_BULK_INSERT_CHUNK", 100, raising=False)

    out = await drn.drain_run_buffer("run-123", str(u.uuid4()), max_events=10)

    assert out["drained"] == 2
    runs = [o for o in fake_db.added if type(o).__name__ == "TestRun"]
    assert len(runs) == 1
    # the created run carries the REAL session start, not the drain-tick time
    assert runs[0].start_time == datetime.fromisoformat(started_iso)
    # head trimmed only AFTER commit, by exactly the number read
    assert fake_redis.ltrim_calls[-1] == (2, -1)
