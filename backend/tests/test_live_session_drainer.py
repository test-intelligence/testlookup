"""Tests for the Phase 4.5 incremental drain service.

These pin the contract that closes the buffer-eviction gap:

* The drain reads from ``LIVE_TESTCASES_KEY`` and writes per-test rows
  to ``test_cases`` mid-session — not only at ``close_session``.
* It pulls aggregates from the authoritative ``LIVE_STATE_KEY`` HINCRBY
  hash so a cap-overflowed run reports an accurate ``total_tests``.
* It is idempotent under overlapping ticks via a per-run SET-NX lock.
* It LTRIMs the buffer ONLY after a successful commit, so a DB failure
  leaves the events intact for the next tick to retry.
* ``drain_all_active_runs`` honours the ``LIVE_SESSION_DRAIN_ENABLED``
  kill switch.

DB / Redis are mocked — pure unit tests, no live infra required.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal async-Redis stub for the drainer.

    * ``lrange`` returns whatever was pre-seeded.
    * ``ltrim`` records the call so tests can assert the head was popped.
    * ``set`` with ``nx=True`` simulates the SET-NX lock — first caller
      wins, subsequent return False.
    """

    def __init__(self, *, buffer_entries: list[str] | None = None,
                 lock_already_held: bool = False) -> None:
        self.buffer_entries = list(buffer_entries or [])
        self.lock_already_held = lock_already_held
        self.calls: list[tuple] = []

    async def set(self, key, value, nx=False, ex=None):
        self.calls.append(("set", key, value, nx, ex))
        if nx and self.lock_already_held:
            return None
        return True

    async def delete(self, key):
        self.calls.append(("delete", key))
        return 1

    async def lrange(self, key, start, end):
        self.calls.append(("lrange", key, start, end))
        if end == -1:
            return list(self.buffer_entries)
        # 0-indexed inclusive end, but the drainer passes ``chunk - 1``.
        return list(self.buffer_entries[start:end + 1])

    async def ltrim(self, key, start, end):
        self.calls.append(("ltrim", key, start, end))
        # Simulate the trim so a subsequent lrange in the same test
        # reflects the post-trim state.
        if end == -1 and start >= 0:
            self.buffer_entries = self.buffer_entries[start:]
        return True


class _FakeExecResult:
    def __init__(self, *, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    """Async-context session. The drainer issues one SELECT (existing
    TestRun) followed by zero-or-more execute(insert(...), rows) calls.
    Records added objects + insert payloads so tests assert end-state."""

    def __init__(self, *, existing_run=None):
        self.added = []
        self.executes: list[tuple] = []
        self.committed = False
        self._existing_run = existing_run

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt, params=None):
        if params is not None:
            # Bulk insert: stmt is `insert(TestCase)`, params is a list of rows.
            self.executes.append(("insert", params))
            return _FakeExecResult()
        # SELECT TestRun
        self.executes.append(("select", stmt))
        return _FakeExecResult(scalar=self._existing_run)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.committed = True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_event(test_name: str, status: str = "PASSED") -> str:
    return json.dumps({"test_name": test_name, "status": status})


def _patch_targets(monkeypatch, redis, session, *, state: dict | None = None):
    """Patch all the drainer's external touchpoints. ``state`` is the
    return value of ``RedisLiveRunState.get`` — None means no aggregates."""
    monkeypatch.setattr(
        "app.services.live_session_drainer.get_redis", lambda: redis,
    )

    class _SessionFactory:
        def __call__(self):
            return session

    monkeypatch.setattr(
        "app.services.live_session_drainer.AsyncSessionLocal",
        _SessionFactory(),
    )
    monkeypatch.setattr(
        "app.services.live_session_drainer.RedisLiveRunState.get",
        AsyncMock(return_value=state),
    )
    # canonical_test_run_uuid is imported lazily inside drain_run_buffer
    # — patch on the source module so the lazy import picks up our stub.
    monkeypatch.setattr(
        "app.services.stream_service.canonical_test_run_uuid",
        lambda run_id: uuid.UUID("11111111-2222-3333-4444-555555555555"),
    )


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_happy_path_inserts_rows_and_ltrims(monkeypatch):
    """Buffer has 3 events → drainer inserts 3 rows, LTRIMs the head
    off, and creates a TestRun row with IN_PROGRESS status."""
    from app.models.postgres import LaunchStatus
    from app.services.live_session_drainer import drain_run_buffer

    redis = _FakeRedis(buffer_entries=[
        _make_event("test_a", "PASSED"),
        _make_event("test_b", "FAILED"),
        _make_event("test_c", "PASSED"),
    ])
    session = _FakeSession(existing_run=None)
    _patch_targets(monkeypatch, redis, session, state={
        "passed": 2, "failed": 1, "skipped": 0, "broken": 0, "total": 3,
        "suite_name": "Smoke",
    })

    result = await drain_run_buffer(
        run_id="run-x", project_id=str(uuid.uuid4()),
        build_number="b1",
    )

    assert result["drained"] == 3
    assert result["lock_held"] is True
    # TestRun added with the HINCRBY aggregates + IN_PROGRESS status.
    assert len(session.added) == 1
    run_row = session.added[0]
    assert run_row.total_tests == 3
    assert run_row.passed_tests == 2
    assert run_row.failed_tests == 1
    assert run_row.status == LaunchStatus.IN_PROGRESS
    assert run_row.primary_suite_name == "Smoke"
    # Bulk insert called with 3 rows.
    inserts = [e for e in session.executes if e[0] == "insert"]
    assert len(inserts) == 1
    assert len(inserts[0][1]) == 3
    # LTRIM uses the drained count as the new head: ltrim(key, 3, -1).
    ltrims = [c for c in redis.calls if c[0] == "ltrim"]
    assert len(ltrims) == 1
    assert ltrims[0][2] == 3
    assert ltrims[0][3] == -1
    # Lock acquired AND released.
    assert any(c[0] == "set" and c[3] is True for c in redis.calls)
    assert any(c[0] == "delete" for c in redis.calls)
    assert session.committed is True


@pytest.mark.asyncio
async def test_drain_uses_state_aggregates_not_event_counts(monkeypatch):
    """Cap-overflow scenario: state says 162K total but the buffer only
    holds the most recent 50K events. The drain must write the run with
    state's totals, NOT the slice size — otherwise TestRun.total_tests
    regresses to the buffer size on every drain tick."""
    from app.services.live_session_drainer import drain_run_buffer

    redis = _FakeRedis(buffer_entries=[
        _make_event(f"t{i}", "PASSED") for i in range(10)
    ])
    session = _FakeSession(existing_run=None)
    _patch_targets(monkeypatch, redis, session, state={
        "passed": 162_000, "failed": 0, "skipped": 0, "broken": 0,
        "total": 162_000,
    })

    await drain_run_buffer(
        run_id="run-y", project_id=str(uuid.uuid4()),
    )

    run_row = session.added[0]
    # Aggregates from state, not from len(events).
    assert run_row.total_tests == 162_000
    assert run_row.passed_tests == 162_000
    # Per-test rows still come from the buffer slice.
    inserts = [e for e in session.executes if e[0] == "insert"]
    assert len(inserts[0][1]) == 10


@pytest.mark.asyncio
async def test_drain_lock_held_skips_safely(monkeypatch):
    """Concurrent drain tick → SET NX returns False → caller bails
    early with drained=0, no DB writes, no LTRIM."""
    from app.services.live_session_drainer import drain_run_buffer

    redis = _FakeRedis(
        buffer_entries=[_make_event("t1", "PASSED")],
        lock_already_held=True,
    )
    session = _FakeSession()
    _patch_targets(monkeypatch, redis, session, state={"passed": 1, "total": 1})

    result = await drain_run_buffer(
        run_id="run-z", project_id=str(uuid.uuid4()),
    )

    assert result["drained"] == 0
    assert result["lock_held"] is False
    # No DB session opened — no executes, no commits.
    assert session.executes == []
    assert session.committed is False
    # No LTRIM called.
    assert not any(c[0] == "ltrim" for c in redis.calls)


@pytest.mark.asyncio
async def test_drain_empty_buffer_is_noop(monkeypatch):
    """LRANGE returns no entries → drainer skips everything but lock
    acquire/release, returning drained=0."""
    from app.services.live_session_drainer import drain_run_buffer

    redis = _FakeRedis(buffer_entries=[])
    session = _FakeSession()
    _patch_targets(monkeypatch, redis, session, state={})

    result = await drain_run_buffer(
        run_id="run-empty", project_id=str(uuid.uuid4()),
    )

    assert result["drained"] == 0
    assert result["lock_held"] is True
    assert session.executes == []
    assert not any(c[0] == "ltrim" for c in redis.calls)


@pytest.mark.asyncio
async def test_drain_updates_existing_run_aggregates_only(monkeypatch):
    """Second drain on the same run: TestRun already exists; the drainer
    updates aggregates from state but does NOT change the stored status
    (that's close_session's job via upsert_test_run)."""
    from app.models.postgres import LaunchStatus
    from app.services.live_session_drainer import drain_run_buffer

    existing_run = SimpleNamespace(
        id=uuid.uuid4(),
        status=LaunchStatus.IN_PROGRESS,
        total_tests=10,
        passed_tests=8,
        failed_tests=2,
        skipped_tests=0,
        broken_tests=0,
        primary_suite_name="Smoke",
        suite_names=["Smoke"],
        end_time=None,
    )
    redis = _FakeRedis(buffer_entries=[
        _make_event("test_new", "PASSED"),
    ])
    session = _FakeSession(existing_run=existing_run)
    _patch_targets(monkeypatch, redis, session, state={
        "passed": 9, "failed": 2, "skipped": 0, "broken": 0, "total": 11,
    })

    await drain_run_buffer(
        run_id="run-update", project_id=str(uuid.uuid4()),
    )

    # No new row added — only the existing one mutated.
    assert session.added == []
    assert existing_run.total_tests == 11
    assert existing_run.passed_tests == 9
    # Status untouched.
    assert existing_run.status == LaunchStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_drain_all_active_runs_honours_kill_switch(monkeypatch):
    """``LIVE_SESSION_DRAIN_ENABLED=False`` short-circuits the beat task
    before touching Redis."""
    from app.core import config
    from app.services import live_session_drainer

    monkeypatch.setattr(config.settings, "LIVE_SESSION_DRAIN_ENABLED", False)
    # If the kill switch leaks, this would explode (no redis patched).
    monkeypatch.setattr(
        live_session_drainer.RedisLiveRunState,
        "get_all_active",
        AsyncMock(side_effect=AssertionError("must not be called when disabled")),
    )

    result = await live_session_drainer.drain_all_active_runs()
    assert result == {
        "runs_scanned": 0, "drained": 0, "errors": 0, "disabled": True,
    }


@pytest.mark.asyncio
async def test_drain_all_active_runs_iterates_active_set(monkeypatch):
    """End-to-end: ``get_all_active`` returns 2 runs → drainer is called
    for each → totals are summed."""
    from app.core import config
    from app.services import live_session_drainer

    monkeypatch.setattr(config.settings, "LIVE_SESSION_DRAIN_ENABLED", True)
    monkeypatch.setattr(
        live_session_drainer.RedisLiveRunState,
        "get_all_active",
        AsyncMock(return_value=[
            {"run_id": "r1", "project_id": "p1", "build_number": "b1"},
            {"run_id": "r2", "project_id": "p2", "build_number": "b2"},
        ]),
    )
    drain_mock = AsyncMock(side_effect=[
        {"drained": 5, "lock_held": True, "run_uuid": "x"},
        {"drained": 3, "lock_held": True, "run_uuid": "y"},
    ])
    monkeypatch.setattr(live_session_drainer, "drain_run_buffer", drain_mock)

    result = await live_session_drainer.drain_all_active_runs()
    assert result["runs_scanned"] == 2
    assert result["drained"] == 8
    assert result["errors"] == 0
    # Confirm each run was drained with the right ids.
    call_kwargs = [c.kwargs for c in drain_mock.await_args_list]
    assert {c["run_id"] for c in call_kwargs} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_drain_all_active_runs_tolerates_per_run_failures(monkeypatch):
    """One bad run shouldn't starve the rest. An exception increments
    the error counter but doesn't abort the sweep."""
    from app.core import config
    from app.services import live_session_drainer

    monkeypatch.setattr(config.settings, "LIVE_SESSION_DRAIN_ENABLED", True)
    monkeypatch.setattr(
        live_session_drainer.RedisLiveRunState,
        "get_all_active",
        AsyncMock(return_value=[
            {"run_id": "r1", "project_id": "p1"},
            {"run_id": "r2", "project_id": "p2"},
        ]),
    )
    monkeypatch.setattr(
        live_session_drainer, "drain_run_buffer",
        AsyncMock(side_effect=[
            Exception("redis hiccup"),
            {"drained": 7, "lock_held": True, "run_uuid": "y"},
        ]),
    )

    result = await live_session_drainer.drain_all_active_runs()
    assert result["runs_scanned"] == 2
    assert result["errors"] == 1
    assert result["drained"] == 7
