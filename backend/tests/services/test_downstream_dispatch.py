"""W25e E-1 T-A1 — the immediate publish of a closed live run's staged intent (contract §1.1, §2.1, amendment A-1).

Fakes only; the outbox helpers are patched on ``app.services.run_downstream_outbox``
(``publish_staged_now`` looks them up there at call time, as the relay does).
The real-PostgreSQL twin is ``tests/integration/test_downstream_dispatch_postgres.py``.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import run_downstream_outbox as outbox
from app.worker import downstream_dispatch as dispatch


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    """Records commit / rollback in one ordered journal with the helpers."""

    def __init__(self, calls: list[str], rows=()):
        self.calls = calls
        self.rows = list(rows)
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.rows)

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")


class _NoExecuteSession:
    """The route-test fakes' shape (``type("DB", (), {})()``): no ``execute``.

    Records every attribute it was asked for and does not have.
    """

    def __init__(self):
        self.asked: list[str] = []

    def __getattr__(self, name):
        self.asked.append(name)
        raise AttributeError(name)


class _SlogRecorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def warning(self, event, **kwargs):
        self.events.append((event, kwargs))


def _sending_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        operation="persist_live_session",
        status="sending",
        dispatch_token=uuid.uuid4(),
        dispatch_failures=0,
    )


def _patch_helpers(monkeypatch, calls, *, rows, publish=None, mark=True):
    async def _claim(db, *, run_id, operations):
        calls.append("claim")
        return list(rows)

    def _publish(row):
        calls.append("publish")
        if publish is not None:
            publish(row)

    async def _mark(db, *, outbox_id, dispatch_token):
        calls.append("mark")
        return mark

    mark_published = AsyncMock(side_effect=_mark)
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(outbox, "claim_staged_dispatches", _claim)
    monkeypatch.setattr(outbox, "_publish_downstream", _publish)
    monkeypatch.setattr(outbox, "mark_downstream_dispatch_published", mark_published)
    monkeypatch.setattr(outbox, "mark_downstream_dispatch_failed", mark_failed)
    return mark_published, mark_failed


async def test_nothing_claimable_rolls_back_and_publishes_nothing(monkeypatch):
    calls: list[str] = []
    mark_published, _ = _patch_helpers(monkeypatch, calls, rows=[])

    outcome = await dispatch.publish_staged_now(_Session(calls), run_id=uuid.uuid4())

    assert outcome == "nothing_pending"
    assert calls == ["claim", "rollback"]
    mark_published.assert_not_awaited()


async def test_claim_commits_before_the_broker_sees_the_message_then_marks(monkeypatch):
    calls: list[str] = []
    row = _sending_row()
    token = row.dispatch_token
    seen: list = []
    mark_published, mark_failed = _patch_helpers(
        monkeypatch, calls, rows=[row], publish=seen.append
    )
    run_id = uuid.uuid4()
    db = _Session(calls)

    outcome = await dispatch.publish_staged_now(db, run_id=str(run_id))

    assert outcome == "published"
    assert calls == ["claim", "commit", "publish", "mark", "commit"]
    assert seen == [row]
    mark_published.assert_awaited_once_with(db, outbox_id=row.id, dispatch_token=token)
    mark_failed.assert_not_awaited()


async def test_the_claim_receives_the_canonical_run_uuid_and_operations(monkeypatch):
    received: dict = {}

    async def _claim(db, *, run_id, operations):
        received.update(run_id=run_id, operations=operations)
        return []

    monkeypatch.setattr(outbox, "claim_staged_dispatches", _claim)
    run_id = uuid.uuid4()

    assert await dispatch.publish_staged_now(_Session([]), run_id=str(run_id)) == "nothing_pending"
    assert received == {"run_id": run_id, "operations": ("persist_live_session",)}


async def test_a_broker_error_defers_without_marking_failed_or_raising(monkeypatch):
    calls: list[str] = []
    row = _sending_row()
    token = row.dispatch_token

    def _broker_down(_row):
        raise ConnectionError("zz-broker-sentinel-q")

    mark_published, mark_failed = _patch_helpers(
        monkeypatch, calls, rows=[row], publish=_broker_down
    )
    recorder = _SlogRecorder()
    monkeypatch.setattr(dispatch, "_slog", recorder)
    run_id = uuid.uuid4()

    outcome = await dispatch.publish_staged_now(_Session(calls), run_id=run_id)

    assert outcome == "deferred"
    assert calls == ["claim", "commit", "publish", "rollback"]
    mark_failed.assert_not_awaited()
    mark_published.assert_not_awaited()
    # The row is exactly as the claim left it: 'sending', same token — the relay
    # reclaims it with that token once its 30 s lease expires.
    assert row.status == "sending"
    assert row.dispatch_token == token
    assert row.dispatch_failures == 0
    assert recorder.events == [
        (
            "run_downstream_publish_now_deferred",
            {"run_id": str(run_id), "error_type": "ConnectionError"},
        )
    ]
    assert "zz-broker-sentinel-q" not in repr(recorder.events)


async def test_a_refused_mark_rolls_back_and_defers(monkeypatch):
    calls: list[str] = []
    row = _sending_row()
    _, mark_failed = _patch_helpers(monkeypatch, calls, rows=[row], mark=False)
    monkeypatch.setattr(dispatch, "_slog", _SlogRecorder())

    outcome = await dispatch.publish_staged_now(_Session(calls), run_id=uuid.uuid4())

    assert outcome == "deferred"
    assert calls == ["claim", "commit", "publish", "mark", "rollback"]
    mark_failed.assert_not_awaited()


async def test_a_failing_claim_defers(monkeypatch):
    calls: list[str] = []

    async def _claim(db, *, run_id, operations):
        calls.append("claim")
        raise RuntimeError("zz-db-sentinel-q")

    monkeypatch.setattr(outbox, "claim_staged_dispatches", _claim)
    monkeypatch.setattr(dispatch, "_slog", _SlogRecorder())

    outcome = await dispatch.publish_staged_now(_Session(calls), run_id=uuid.uuid4())

    assert outcome == "deferred"
    assert calls == ["claim", "rollback"]


async def test_a_bad_run_id_defers_without_claiming(monkeypatch):
    claim = AsyncMock(return_value=[])
    monkeypatch.setattr(outbox, "claim_staged_dispatches", claim)
    recorder = _SlogRecorder()
    monkeypatch.setattr(dispatch, "_slog", recorder)

    outcome = await dispatch.publish_staged_now(_Session([]), run_id="zz-not-a-run-id")

    assert outcome == "deferred"
    claim.assert_not_awaited()
    assert recorder.events[0][1]["error_type"] == "ValueError"


async def test_a_session_without_execute_defers_and_never_touches_commit(monkeypatch):
    """The route-test fakes have no ``execute``: the real claim fails first, so
    their recorded ``["close", "commit", "finalize"]`` order is unchanged."""
    monkeypatch.setattr(dispatch, "_slog", _SlogRecorder())
    db = _NoExecuteSession()

    outcome = await dispatch.publish_staged_now(db, run_id=uuid.uuid4())

    assert outcome == "deferred"
    assert "execute" in db.asked
    assert "commit" not in db.asked


async def test_a_blocked_broker_publish_times_out_off_the_event_loop(monkeypatch):
    """Amendment A-1: the synchronous ``apply_async`` runs in a thread, bounded.

    The publisher blocks its thread for up to 1 s; the timeout is 0.05 s. The
    call defers (no mark of any kind), and a coroutine beside it keeps running
    while the publisher is blocked — the API's event loop was never blocked.
    """
    calls: list[str] = []
    row = _sending_row()
    release = threading.Event()
    ticks = {"n": 0, "at_block": None}

    def _black_hole(_row):
        ticks["at_block"] = ticks["n"]
        release.wait(1.0)

    mark_published, mark_failed = _patch_helpers(
        monkeypatch, calls, rows=[row], publish=_black_hole
    )
    monkeypatch.setattr(dispatch, "PUBLISH_NOW_TIMEOUT_SECONDS", 0.05)
    recorder = _SlogRecorder()
    monkeypatch.setattr(dispatch, "_slog", recorder)
    stop = asyncio.Event()

    async def _beside():
        while not stop.is_set():
            ticks["n"] += 1
            await asyncio.sleep(0.002)

    ticker = asyncio.create_task(_beside())
    try:
        await asyncio.sleep(0)
        outcome = await dispatch.publish_staged_now(_Session(calls), run_id=uuid.uuid4())
        ticks_at_return = ticks["n"]
    finally:
        release.set()
        stop.set()
        await ticker

    assert outcome == "deferred"
    mark_published.assert_not_awaited()
    mark_failed.assert_not_awaited()
    assert calls == ["claim", "commit", "publish", "rollback"]
    assert ticks["at_block"] is not None
    assert ticks_at_return > ticks["at_block"], "the event loop was blocked by the publish"
    assert recorder.events[0][1]["error_type"] == "TimeoutError"
    assert row.status == "sending"


# ── claim_staged_dispatches (stage-only, in the service) ──────────────────────


async def test_the_staged_claim_is_one_runs_pending_rows_skip_locked():
    db = _Session([])
    run_id = uuid.uuid4()

    assert await outbox.claim_staged_dispatches(
        db, run_id=run_id, operations=("persist_live_session",)
    ) == []

    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split())
    assert "run_downstream_outbox.run_id = " in sql
    assert "run_downstream_outbox.operation IN" in sql
    assert "run_downstream_outbox.status = " in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY run_downstream_outbox.created_at, run_downstream_outbox.id" in sql
    params = compiled.params
    assert run_id in params.values()
    assert "pending" in params.values()
    assert ["persist_live_session"] in [
        list(value) for value in params.values() if isinstance(value, (list, tuple))
    ]


async def test_rows_the_relay_took_or_not_yet_due_are_skipped():
    now = datetime.now(timezone.utc)
    taken = SimpleNamespace(
        status="sending", next_attempt_at=None, dispatch_failures=0,
        dispatch_token=uuid.uuid4(), attempts=1,
    )
    not_due = SimpleNamespace(
        status="pending", next_attempt_at=now + timedelta(minutes=5),
        dispatch_failures=0, dispatch_token=None, attempts=0,
    )
    due = SimpleNamespace(
        status="pending", next_attempt_at=None, dispatch_failures=0,
        dispatch_token=None, attempts=0, processing_task_id="zz-old-q",
        lease_expires_at=None, last_error="zz-prior-q",
    )
    db = _Session([], rows=[taken, not_due, due])

    claimed = await outbox.claim_staged_dispatches(
        db, run_id=uuid.uuid4(), operations=("persist_live_session",)
    )

    assert claimed == [due]
    assert taken.status == "sending" and taken.attempts == 1
    assert not_due.status == "pending" and not_due.dispatch_token is None
    assert due.status == "sending"
    assert due.attempts == 1
    assert isinstance(due.dispatch_token, uuid.UUID)
    assert due.processing_task_id is None and due.last_error is None
    lease = (due.lease_expires_at - now).total_seconds()
    assert outbox.IMMEDIATE_LEASE_SECONDS - 5 <= lease <= outbox.IMMEDIATE_LEASE_SECONDS + 5
    assert due.next_attempt_at == due.lease_expires_at


async def test_an_exhausted_pending_row_becomes_failed_and_is_not_returned():
    exhausted = SimpleNamespace(
        status="pending", next_attempt_at=None,
        dispatch_failures=outbox.MAX_DISPATCH_ATTEMPTS, dispatch_token=None,
        attempts=8, lease_expires_at=None, last_error=None,
    )
    db = _Session([], rows=[exhausted])

    claimed = await outbox.claim_staged_dispatches(
        db, run_id=uuid.uuid4(), operations=("persist_live_session",)
    )

    assert claimed == []
    assert exhausted.status == "failed"
    assert exhausted.last_error == "dispatch_attempts_exhausted"


@pytest.mark.parametrize("operations", [("zz-not-an-operation-q",), ()])
async def test_an_unknown_or_empty_operation_is_refused(operations):
    db = _Session([])
    with pytest.raises(ValueError):
        await outbox.claim_staged_dispatches(db, run_id=uuid.uuid4(), operations=operations)
    assert db.statements == []


def test_the_dispatch_module_imports_no_notifier():
    """Nothing here can send mail (the homelab's SMTP is live)."""
    import inspect

    source = inspect.getsource(dispatch).lower()
    for needle in ("notification", "smtp", "send_email"):
        assert needle not in source
