"""E7.3: leases, fencing tokens, and the heartbeat that runs inside a stage.

These replace ``tests/test_agents_effective_status.py``, which pinned the
read-time 30-minute staleness guess that this story deletes.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import pipeline_lease as pl


# ── field helpers ─────────────────────────────────────────────────────────────


def test_acquire_sets_a_unique_token_and_a_future_expiry():
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    t1, f1 = pl.acquire_lease_fields(now=now)
    t2, _ = pl.acquire_lease_fields(now=now)
    assert t1 != t2, "each acquire must mint a fresh token"
    assert f1["fencing_token"] == t1
    assert f1["lease_expires_at"] == now + timedelta(seconds=pl.LEASE_SECONDS)
    assert f1["heartbeat_at"] == now
    assert f1["lease_owner"]


def test_lease_is_twice_the_heartbeat_so_one_miss_is_survivable():
    assert pl.LEASE_SECONDS == pl.HEARTBEAT_SECONDS * 2


def test_release_clears_every_lease_field():
    fields = pl.release_lease_fields()
    assert fields == {"lease_owner": None, "fencing_token": None, "lease_expires_at": None}


def test_owner_is_host_and_pid():
    owner = pl.new_owner()
    assert ":" in owner and owner.split(":")[-1].isdigit()
    assert len(owner) <= 255


# ── fencing ───────────────────────────────────────────────────────────────────


class _DB:
    def __init__(self, stored):
        self.stored = stored
        self.updates = []

    async def execute(self, stmt):
        text = str(stmt)
        if text.strip().upper().startswith("UPDATE"):
            self.updates.append(stmt)
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            matched = self.stored is not None and f"'{self.stored}'" in compiled
            return SimpleNamespace(first=lambda: (uuid.uuid4(),) if matched else None)
        return SimpleNamespace(scalar_one_or_none=lambda: self.stored)

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_verify_passes_when_the_token_matches():
    assert await pl.verify_lease(_DB("tok"), str(uuid.uuid4()), "tok") is True


@pytest.mark.asyncio
async def test_verify_fails_when_the_row_was_reclaimed():
    assert await pl.verify_lease(_DB("rotated"), str(uuid.uuid4()), "tok") is False


@pytest.mark.asyncio
async def test_verify_fails_when_the_row_is_gone():
    assert await pl.verify_lease(_DB(None), str(uuid.uuid4()), "tok") is False


@pytest.mark.asyncio
async def test_an_unfenced_caller_is_allowed_through():
    """Rows that predate the lease columns, and legacy call sites, keep working."""
    assert await pl.verify_lease(_DB("whatever"), str(uuid.uuid4()), None) is True
    await pl.fence_or_raise(_DB("whatever"), str(uuid.uuid4()), None)


@pytest.mark.asyncio
async def test_fence_or_raise_raises_lease_lost_with_the_id():
    run_id = str(uuid.uuid4())
    with pytest.raises(pl.LeaseLost) as exc:
        await pl.fence_or_raise(_DB("rotated"), run_id, "tok")
    assert exc.value.pipeline_run_id == run_id
    assert exc.value.token == "tok"


def test_lease_lost_is_a_retryable_error_code():
    from app.services.retry_policy import RetryPolicy

    assert RetryPolicy().is_retryable("lease_lost") is True


# ── renew ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_renew_predicates_on_the_token_and_extends():
    db = _DB("tok")
    assert await pl.renew_lease(db, str(uuid.uuid4()), "tok") is True
    compiled = str(db.updates[0].compile(compile_kwargs={"literal_binds": True}))
    assert "fencing_token = 'tok'" in compiled
    assert "lease_expires_at" in compiled and "heartbeat_at" in compiled


@pytest.mark.asyncio
async def test_renew_returns_false_once_the_token_rotated():
    assert await pl.renew_lease(_DB("rotated"), str(uuid.uuid4()), "tok") is False


# ── heartbeat ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_means_no_heartbeat_and_no_event():
    async with pl.held_lease("", None) as lost:
        assert lost is None


@pytest.mark.asyncio
async def test_heartbeat_renews_while_the_body_runs(monkeypatch):
    renewals = []

    async def _renew(_db, run_id, token):
        renewals.append(token)
        return True

    monkeypatch.setattr(pl, "renew_lease", _renew)
    monkeypatch.setattr(pl, "AsyncSessionLocal", _session_factory(), raising=False)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory())

    async with pl.held_lease("p1", "tok", heartbeat_seconds=0.01) as lost:
        await asyncio.sleep(0.06)
        assert not lost.is_set()
    assert len(renewals) >= 2, "the heartbeat must renew repeatedly, not once"


@pytest.mark.asyncio
async def test_heartbeat_signals_when_the_row_is_reclaimed(monkeypatch):
    async def _renew(_db, run_id, token):
        return False  # someone else holds it now

    monkeypatch.setattr(pl, "renew_lease", _renew)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory())

    async with pl.held_lease("p1", "tok", heartbeat_seconds=0.01) as lost:
        await asyncio.wait_for(lost.wait(), timeout=1.0)
        assert lost.is_set()


@pytest.mark.asyncio
async def test_a_transient_renew_error_does_not_stop_the_holder(monkeypatch):
    calls = {"n": 0}

    async def _renew(_db, run_id, token):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return True

    monkeypatch.setattr(pl, "renew_lease", _renew)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory())

    async with pl.held_lease("p1", "tok", heartbeat_seconds=0.01) as lost:
        await asyncio.sleep(0.06)
        assert not lost.is_set(), "one failed renew is survivable while the deadline has room"
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_a_stalled_renew_is_abandoned_and_stops_the_holder(monkeypatch):
    async def _renew(_db, run_id, token):
        await asyncio.sleep(10)  # never returns within the budget
        return True

    monkeypatch.setattr(pl, "renew_lease", _renew)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory())
    monkeypatch.setattr(pl, "LEASE_SECONDS", 0.08)
    monkeypatch.setattr(pl, "RENEW_MARGIN_SECONDS", 0.01)

    async with pl.held_lease("p1", "tok", heartbeat_seconds=0.01) as lost:
        await asyncio.wait_for(lost.wait(), timeout=2.0)
        assert lost.is_set(), "a renew that stalls past its budget must stop the holder"


@pytest.mark.asyncio
async def test_the_heartbeat_task_is_cancelled_when_the_body_exits(monkeypatch):
    async def _renew(_db, run_id, token):
        return True

    monkeypatch.setattr(pl, "renew_lease", _renew)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory())

    before = len(asyncio.all_tasks())
    async with pl.held_lease("p1", "tok", heartbeat_seconds=0.01):
        await asyncio.sleep(0.02)
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) <= before, "the heartbeat must not outlive the stage"


def _session_factory():
    import contextlib

    @contextlib.asynccontextmanager
    async def _factory():
        yield _DB("tok")

    return _factory
