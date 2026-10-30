"""W23c AGT-10: the review-gate enforcement date (contract section 2.1 G-1 ... G-4, R-6).

``gate_enforced()`` stays synchronous; it reads the env flag OR this process's
snapshot of the stored date. These tests drive the snapshot directly: the pure
``gate_state``, the refresh (fresh = no I/O; stale = Redis, then Postgres on an
OWN session; failures keep the last value), the PUT's ``after_commit``, a
two-pod simulation, and the would-refuse summary ("Not measured", never 0).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.db.postgres as pg
from app.core.config import settings
from app.services import report_distribution_policy as policy
from app.services import review_gate_setting as rgs
from app.services import review_gate_vocab as vocab


@pytest.fixture(autouse=True)
def _fresh_gate():
    rgs.reset_snapshot_for_tests()
    yield
    rgs.reset_snapshot_for_tests()
    rgs.pin_snapshot_for_tests(None)  # the conftest session pin, restored


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def _log(self, level: str):
        def emit(event: str, **kwargs) -> None:
            self.events.append((level, event, kwargs))

        return emit

    def __getattr__(self, level: str):
        return self._log(level)


class _FakeRedis:
    def __init__(self, stored: str | None = None, *, fail: bool = False) -> None:
        self.stored = stored
        self.fail = fail
        self.calls: list[tuple] = []

    async def get(self, key):
        self.calls.append(("get", key))
        if self.fail:
            raise ConnectionError("redis down")
        return self.stored

    async def setex(self, key, ttl, value):
        self.calls.append(("setex", key, ttl, value))
        if self.fail:
            raise ConnectionError("redis down")
        self.stored = value

    async def delete(self, *keys):
        self.calls.append(("delete", *keys))
        if self.fail:
            raise ConnectionError("redis down")
        self.stored = None


class _Row:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _OwnSession:
    """The refresh's own short session: one ``execute`` returning the row."""

    def __init__(self, row, *, fail: bool = False) -> None:
        self.row = row
        self.fail = fail
        self.executed = 0
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        self.executed += 1
        if self.fail:
            raise OSError("postgres down")
        return _Row(self.row)


def _wire(monkeypatch, *, redis: _FakeRedis, own: _OwnSession) -> None:
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr(pg, "AsyncSessionLocal", own, raising=False)


def _value(instant: datetime | None) -> dict:
    return {"enforced_from": instant.isoformat() if instant else None}


# ── gate_state: PURE ────────────────────────────────────────────────────────


def test_gate_state_unset_past_future_now_and_the_env_override():
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=3)
    assert rgs.gate_state(None, env=False, now=now) == (False, "unset")
    assert rgs.gate_state(past, env=False, now=now) == (True, "date")
    assert rgs.gate_state(future, env=False, now=now) == (False, "scheduled")
    assert rgs.gate_state(now, env=False, now=now) == (True, "date")  # exactly now enforces
    for instant in (None, past, future, now):
        assert rgs.gate_state(instant, env=True, now=now) == (True, "env")
    # Another offset, the same instant: compared as instants, not as wall clocks.
    plus_one = timezone(timedelta(hours=1))
    assert rgs.gate_state(now.astimezone(plus_one), env=False, now=now) == (True, "date")
    assert set(source for _e, source in [
        rgs.gate_state(None, env=False, now=now),
        rgs.gate_state(past, env=False, now=now),
        rgs.gate_state(future, env=False, now=now),
        rgs.gate_state(None, env=True, now=now),
    ]) == set(vocab.GATE_SOURCES)


def test_enforced_now_and_gate_enforced_read_the_pinned_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    rgs.pin_snapshot_for_tests(None)
    assert rgs.enforced_now() is False
    assert policy.gate_enforced() is False
    rgs.pin_snapshot_for_tests(now - timedelta(minutes=5))
    assert rgs.enforced_now() is True
    assert policy.gate_enforced() is True
    rgs.pin_snapshot_for_tests(now + timedelta(days=1))
    assert rgs.enforced_now() is False
    assert rgs.enforced_now(now + timedelta(days=2)) is True
    assert policy.gate_enforced() is False
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    assert policy.gate_enforced() is True  # the env flag is the hard override


def test_a_never_loaded_process_reads_unset(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", False)
    assert rgs.current_snapshot().loaded is False
    assert rgs.enforced_now() is False
    assert policy.gate_enforced() is False


def test_the_vocabulary_timings_are_the_contracts():
    assert rgs.SNAPSHOT_TTL_SECONDS == vocab.SNAPSHOT_TTL_SECONDS == 30
    assert rgs.LOAD_TIMEOUT_SECONDS == vocab.LOAD_TIMEOUT_SECONDS == 1.0
    assert rgs.WOULD_REFUSE_TIMEOUT_SECONDS == 2.0


def test_no_migration_writes_the_review_gate_key():
    """D17: unset on upgrade. No migration names the key (the G-1 guard)."""
    versions = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
    offenders = [
        path.name
        for path in sorted(versions.glob("*.py"))
        if "review_gate" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ── refresh_snapshot ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fresh_snapshot_makes_no_redis_or_database_call(monkeypatch):
    redis, own = _FakeRedis(), _OwnSession(None)
    _wire(monkeypatch, redis=redis, own=own)
    clock = [1000.0]
    monkeypatch.setattr(rgs, "_clock", lambda: clock[0])
    rgs.seed_snapshot(None)  # loaded at t=1000
    clock[0] = 1000.0 + 29.0
    snapshot = await rgs.refresh_snapshot()
    assert snapshot.loaded is True
    assert redis.calls == [] and own.opened == 0


@pytest.mark.asyncio
async def test_a_stale_snapshot_reads_the_redis_mirror_first(monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    redis, own = _FakeRedis(json.dumps(_value(past))), _OwnSession(None)
    _wire(monkeypatch, redis=redis, own=own)
    snapshot = await rgs.refresh_snapshot()
    assert snapshot.loaded is True and snapshot.enforced_from == past
    assert redis.calls == [("get", vocab.CACHE_KEY)]
    assert own.opened == 0  # a mirror hit reads no Postgres
    assert rgs.enforced_now() is True


@pytest.mark.asyncio
async def test_a_mirror_miss_reads_postgres_on_its_own_session_never_the_callers(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(days=4)
    redis = _FakeRedis(None)
    own = _OwnSession((_value(future), datetime.now(timezone.utc)))
    _wire(monkeypatch, redis=redis, own=own)
    caller = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("caller's session used")))

    # Through a real policy entry point: the refresh runs first, on its own session.
    labels, note, decision = await policy.gate_kind_labels(
        caller, run_id="r", project_id="p", kind_labels={}, channel="github_pr_comment"
    )
    assert (labels, note, decision) == ({}, None, None)
    caller.execute.assert_not_awaited()
    assert own.opened == 1 and own.executed == 1
    assert rgs.current_snapshot().enforced_from == future
    setex = [call for call in redis.calls if call[0] == "setex"]
    assert setex == [
        ("setex", vocab.CACHE_KEY, vocab.CACHE_TTL_SECONDS, json.dumps(_value(future)))
    ]


@pytest.mark.asyncio
async def test_a_redis_error_falls_back_to_postgres(monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    redis = _FakeRedis(fail=True)
    own = _OwnSession((_value(past), datetime.now(timezone.utc)))
    _wire(monkeypatch, redis=redis, own=own)
    snapshot = await rgs.refresh_snapshot()
    assert snapshot.enforced_from == past and own.executed == 1
    assert rgs.enforced_now() is True


@pytest.mark.asyncio
async def test_both_stores_failing_keeps_the_last_value_and_logs_without_a_date(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rgs, "_slog", recorder)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    clock = [5000.0]
    monkeypatch.setattr(rgs, "_clock", lambda: clock[0])
    rgs.seed_snapshot(past)
    clock[0] += 31.0
    _wire(monkeypatch, redis=_FakeRedis(fail=True), own=_OwnSession(None, fail=True))
    snapshot = await rgs.refresh_snapshot()
    assert snapshot.loaded is True and snapshot.enforced_from == past
    assert snapshot.fetched_at == clock[0]  # stamped: a back-off, not a retry per call
    failed = [e for e in recorder.events if e[1] == "review_gate_refresh_failed"]
    assert len(failed) == 1
    assert failed[0][2] == {"error_type": "OSError"}
    assert past.isoformat() not in repr(recorder.events)


@pytest.mark.asyncio
async def test_a_slow_load_is_bounded_and_keeps_the_last_value(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rgs, "_slog", recorder)
    monkeypatch.setattr(rgs, "LOAD_TIMEOUT_SECONDS", 0.05)

    async def slow():
        await asyncio.sleep(2.0)
        return datetime.now(timezone.utc)

    monkeypatch.setattr(rgs, "_load_shared", slow)
    started = asyncio.get_running_loop().time()
    snapshot = await rgs.refresh_snapshot()
    assert asyncio.get_running_loop().time() - started < 1.0
    assert snapshot.loaded is False and snapshot.enforced_from is None  # never loaded: unset
    assert [e[1] for e in recorder.events] == ["review_gate_refresh_failed"]
    assert rgs.enforced_now() is False


@pytest.mark.asyncio
async def test_a_pinned_snapshot_is_never_refreshed_even_when_forced(monkeypatch):
    redis = _FakeRedis(json.dumps(_value(datetime.now(timezone.utc))))
    own = _OwnSession(None)
    _wire(monkeypatch, redis=redis, own=own)
    rgs.pin_snapshot_for_tests(None)
    await rgs.refresh_snapshot(force=True)
    await rgs.after_commit(datetime.now(timezone.utc) - timedelta(days=1))
    assert rgs.current_snapshot().enforced_from is None
    assert [call[0] for call in redis.calls] == ["delete"]  # the PUT still drops the mirror
    assert own.opened == 0


# ── after_commit and two pods ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_after_commit_sets_this_pods_snapshot_and_deletes_the_mirror(monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    redis = _FakeRedis(json.dumps(_value(None)))
    _wire(monkeypatch, redis=redis, own=_OwnSession(None))
    await rgs.after_commit(past)
    assert rgs.current_snapshot().loaded is True
    assert rgs.current_snapshot().enforced_from == past
    assert rgs.enforced_now() is True
    assert redis.calls == [("delete", vocab.CACHE_KEY)] and redis.stored is None


@pytest.mark.asyncio
async def test_after_commit_never_raises_when_redis_is_down(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rgs, "_slog", recorder)
    _wire(monkeypatch, redis=_FakeRedis(fail=True), own=_OwnSession(None))
    await rgs.after_commit(None)
    assert rgs.current_snapshot().loaded is True
    assert [e[1] for e in recorder.events] == ["review_gate_cache_not_deleted"]


@pytest.mark.asyncio
async def test_another_pod_sees_the_put_once_its_snapshot_ttl_passes(monkeypatch):
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=1)
    pod_a, pod_b = rgs._GateState(), rgs._GateState()
    redis = _FakeRedis(json.dumps(_value(None)))
    own = _OwnSession((_value(None), now))
    _wire(monkeypatch, redis=redis, own=own)
    clock = [100.0]
    monkeypatch.setattr(rgs, "_clock", lambda: clock[0])

    monkeypatch.setattr(rgs, "_STATE", pod_b)
    await rgs.refresh_snapshot()  # pod B loads "unset" from the mirror at t=100
    assert rgs.enforced_now() is False

    clock[0] = 110.0
    monkeypatch.setattr(rgs, "_STATE", pod_a)
    own.row = (_value(past), now)  # the PUT committed the past date
    await rgs.after_commit(past)
    assert rgs.enforced_now() is True  # the deciding pod is exact at once
    assert redis.stored is None

    monkeypatch.setattr(rgs, "_STATE", pod_b)
    clock[0] = 129.0
    await rgs.refresh_snapshot()
    assert rgs.enforced_now() is False  # pod B is still inside its 30 s
    assert own.executed == 0

    clock[0] = 131.0
    await rgs.refresh_snapshot()
    assert rgs.enforced_now() is True  # mirror gone -> Postgres -> the new date
    assert own.executed == 1
    assert redis.stored == json.dumps(_value(past))  # and the mirror is filled again


# ── load_setting / stage_setting ────────────────────────────────────────────


class _Session:
    """A request session fake that records every statement."""

    def __init__(self, rows: list) -> None:
        self.rows = list(rows)
        self.statements: list = []
        self.added: list = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _Row(self.rows.pop(0) if self.rows else None)

    def add(self, row) -> None:
        self.added.append(row)


def _sql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_load_setting_reads_a_malformed_or_naive_value_as_unset(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rgs, "_slog", recorder)
    stamp = datetime.now(timezone.utc)
    for raw in ({"enforced_from": "Zq-not-a-date"}, {"enforced_from": "2026-11-02T09:00:00"}, ["x"]):
        assert await rgs.load_setting(_Session([(raw, stamp)])) is None
    assert {e[1] for e in recorder.events} == {"review_gate_value_malformed"}
    assert "Zq-not-a-date" not in repr(recorder.events)
    instant = stamp.astimezone(timezone(timedelta(hours=2)))
    session = _Session([({"enforced_from": instant.isoformat()}, stamp)])
    assert await rgs.load_setting_record(session) == (stamp, stamp)
    assert await rgs.load_setting(_Session([None])) is None  # no row = unset


@pytest.mark.asyncio
async def test_stage_setting_upserts_and_audits_a_change_and_nothing_for_the_same_value():
    from app.models.postgres import SettingsAuditLog

    future = datetime.now(timezone.utc) + timedelta(days=5)
    actor = SimpleNamespace(id=None, username="Zq-admin-xyz", full_name=None)

    first = _Session([None])
    assert await rgs.stage_setting(first, enforced_from=future, actor=actor) == (None, future)
    assert len(first.statements) == 2
    assert "FOR UPDATE" in _sql(first.statements[0])
    upsert = _sql(first.statements[1])
    assert "INSERT INTO app_settings" in upsert and "ON CONFLICT (key) DO UPDATE" in upsert
    assert [type(row) for row in first.added] == [SettingsAuditLog]
    audit = first.added[0]
    assert (audit.setting_key, audit.action, audit.changed_fields) == (
        "review_gate", "updated", ["enforced_from"]
    )

    again = _Session([(_value(future),)])
    plus_two = future.astimezone(timezone(timedelta(hours=2)))  # the same instant
    assert await rgs.stage_setting(again, enforced_from=plus_two, actor=actor) == (future, future)
    assert len(again.statements) == 1 and again.added == []  # no write, no audit row

    cleared = _Session([(_value(future),)])
    assert await rgs.stage_setting(cleared, enforced_from=None, actor=actor) == (future, None)
    assert len(cleared.statements) == 2 and len(cleared.added) == 1


# ── would_refuse_summary ────────────────────────────────────────────────────


class _AggregateResult:
    def __init__(self, payload) -> None:
        self._payload = payload

    def one(self):
        return self._payload

    def all(self):
        return self._payload


class _AggregateSession:
    def __init__(self, totals, per_channel, *, fail: bool = False, delay: float = 0.0) -> None:
        self._answers = [totals, per_channel]
        self.fail = fail
        self.delay = delay
        self.statements: list = []
        self.writes = 0

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise OSError("audit table unreadable")
        return _AggregateResult(self._answers.pop(0))

    def add(self, *_a):  # pragma: no cover - a read path must not write
        self.writes += 1


@pytest.mark.asyncio
async def test_would_refuse_counts_decisions_distinct_runs_and_channels():
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=90)
    session = _AggregateSession(
        (oldest, 42, 12),
        [("share_link_html", 20), ("report_pdf", 11), ("github_pr_comment", 11)],
    )
    summary = await rgs.would_refuse_summary(session, now=now)
    assert summary == {
        "measured": True,
        "window_days": 30,
        "since": now - timedelta(days=30),
        "decisions": 42,
        "runs": 12,
        "by_channel": [
            {"channel": "share_link_html", "decisions": 20},
            {"channel": "report_pdf", "decisions": 11},
            {"channel": "github_pr_comment", "decisions": 11},
        ],
    }
    totals_sql, channel_sql = (_sql(s) for s in session.statements)
    assert "count(DISTINCT CAST(access_audit_logs.after_value ->> " in totals_sql
    assert "min(access_audit_logs.created_at)" in totals_sql
    assert "GROUP BY" in channel_sql and "ORDER BY count(*) DESC" in channel_sql
    for sql in (totals_sql, channel_sql):
        assert "access_audit_logs.action = " in sql and "access_audit_logs.created_at >= " in sql
    assert session.writes == 0


@pytest.mark.asyncio
async def test_would_refuse_since_is_clipped_to_the_oldest_audit_row():
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=12)
    summary = await rgs.would_refuse_summary(_AggregateSession((oldest, 3, 2), [("report_pdf", 3)]), now=now)
    assert summary["since"] == oldest
    empty = await rgs.would_refuse_summary(_AggregateSession((None, 0, 0), []), now=now)
    assert empty["measured"] is True and empty["decisions"] == 0 and empty["by_channel"] == []


@pytest.mark.asyncio
async def test_an_unreadable_audit_log_is_not_measured_never_zero(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rgs, "_slog", recorder)
    now = datetime.now(timezone.utc)
    expected = {
        "measured": False, "window_days": 30, "since": None,
        "decisions": None, "runs": None, "by_channel": None,
    }
    failing = _AggregateSession(None, None, fail=True)
    assert await rgs.would_refuse_summary(failing, now=now) == expected
    monkeypatch.setattr(rgs, "WOULD_REFUSE_TIMEOUT_SECONDS", 0.05)
    slow = _AggregateSession((now, 1, 1), [("report_pdf", 1)], delay=1.0)
    assert await rgs.would_refuse_summary(slow, now=now) == expected
    assert [e[1] for e in recorder.events] == ["review_gate_would_refuse_not_measured"] * 2
