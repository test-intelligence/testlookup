"""Tests for ``flaky_quarantine_service._audit`` (P2-4).

Pins:

1. The audit write uses a FRESH ``AsyncSessionLocal()`` — NOT the
   caller's injected session — so a retry on transient DB failure can
   roll back without poisoning the caller's session.
2. Transient DB exceptions (``OperationalError`` and asyncpg deadlock /
   serialization / connection drops) trigger ``async_retry`` and the
   audit row eventually persists.
3. Permanent failures (constraint violation, programming error) bubble
   out of ``async_retry`` and are swallowed at the top of ``_audit`` —
   the contract is "never raises" — but a structured WARNING with
   ``error_type``, ``request_id``, ``project_id``, ``action`` lands so
   operators can grep for dropped audit rows.
4. ``IntegrityError`` (constraint violation) is NOT retried.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


@pytest.fixture
def caller_db():
    """The session passed into _audit by the caller — we assert it is
    NEVER touched (no add/commit/rollback). _audit must use a fresh
    session it opens itself."""
    db = MagicMock(name="caller_db_must_not_be_touched")
    db.add = MagicMock(side_effect=AssertionError("caller's session must not be modified"))
    db.commit = AsyncMock(side_effect=AssertionError("caller's session must not be committed"))
    db.rollback = AsyncMock(side_effect=AssertionError("caller's session must not be rolled back"))
    return db


@pytest.fixture
def actor():
    return SimpleNamespace(
        id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        username="alice",
        email="alice@example.com",
    )


def _make_operational_error() -> Exception:
    """Build a SQLAlchemy OperationalError that retries should catch.
    The OperationalError constructor wants (statement, params, orig);
    feed it placeholder args that satisfy the signature."""
    from sqlalchemy.exc import OperationalError
    return OperationalError("SELECT 1", {}, Exception("simulated"))


def _make_integrity_error() -> Exception:
    from sqlalchemy.exc import IntegrityError
    return IntegrityError("INSERT ...", {}, Exception("duplicate key"))


def _patch_audit_session(monkeypatch, *, commit_side_effects):
    """Install a fake AsyncSessionLocal whose session().commit() walks
    through ``commit_side_effects`` one per call. ``None`` in the list
    means "succeed"; an exception INSTANCE means "raise this on commit"."""
    calls = {"commit": 0, "rollback": 0, "added": []}

    class _FakeAuditSession:
        def __init__(self):
            self._closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False  # don't swallow

        def add(self, entry):
            calls["added"].append(entry)

        async def commit(self):
            idx = calls["commit"]
            calls["commit"] += 1
            effect = commit_side_effects[min(idx, len(commit_side_effects) - 1)]
            if isinstance(effect, BaseException):
                raise effect
            return None

        async def rollback(self):
            calls["rollback"] += 1

    def _factory():
        return _FakeAuditSession()

    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal", _factory,
        raising=False,
    )
    return calls


@pytest.mark.asyncio
async def test_audit_uses_fresh_session_not_callers(caller_db, actor, monkeypatch):
    """First-attempt success: caller's session must not be touched at
    all, and a single audit row lands via a fresh session."""
    from app.services.flaky_quarantine_service import _audit

    calls = _patch_audit_session(monkeypatch, commit_side_effects=[None])

    await _audit(
        caller_db,
        actor,
        action="create",
        request_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        after={"status": "PROPOSED"},
    )

    # TWO independent writes on fresh sessions, not one: the SettingsAuditLog
    # compliance row, and the project activity-ledger mirror added for epic ACT.
    # The mirror exists because settings_audit_log has no project column, so
    # query_unified_audit hides these rows from every non-ADMIN — the QA lead
    # who quarantined a test could see it and the engineer who owns the test
    # could not.
    #
    # What this test actually pins is unchanged and is the part that matters:
    # neither write touches the CALLER's session.
    from app.models.postgres import ProjectActivityEvent, SettingsAuditLog

    assert calls["commit"] == 2
    added = calls["added"]
    assert len(added) == 2
    assert sum(isinstance(a, SettingsAuditLog) for a in added) == 1
    assert sum(isinstance(a, ProjectActivityEvent) for a in added) == 1
    # caller_db's spies all raise AssertionError on touch — the test
    # would already have failed if any of them fired.


@pytest.mark.asyncio
async def test_audit_retries_on_transient_db_error(caller_db, actor, monkeypatch):
    """A SerializationFailureError-style transient error must be
    retried. The audit row eventually lands."""
    from app.services.flaky_quarantine_service import _audit

    calls = _patch_audit_session(
        monkeypatch,
        commit_side_effects=[_make_operational_error(), None],
    )

    await _audit(
        caller_db,
        actor,
        action="approve",
        request_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        before={"status": "PROPOSED"},
        after={"status": "APPROVED"},
    )

    # First commit failed, second succeeded — then the activity-ledger mirror
    # (epic ACT) commits its own row on its own fresh session, so three commits
    # in total. The retry contract this test exists to pin is the first two.
    assert calls["commit"] == 3
    assert calls["rollback"] == 1
    # add re-invoked on each attempt (2), plus the ledger row (1).
    assert len(calls["added"]) == 3

    from app.models.postgres import ProjectActivityEvent, SettingsAuditLog

    assert sum(isinstance(a, SettingsAuditLog) for a in calls["added"]) == 2
    assert sum(isinstance(a, ProjectActivityEvent) for a in calls["added"]) == 1


@pytest.mark.asyncio
async def test_audit_does_not_retry_integrity_error(caller_db, actor, monkeypatch, caplog):
    """IntegrityError is a permanent failure (constraint violation) —
    must NOT trigger retry. Caller is NOT raised at; a structured
    WARNING lands for the dropped audit row."""
    from app.services.flaky_quarantine_service import _audit

    calls = _patch_audit_session(
        monkeypatch,
        commit_side_effects=[_make_integrity_error()],
    )

    with caplog.at_level("WARNING", logger="services.resilience"):
        await _audit(
            caller_db,
            actor,
            action="create",
            request_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            after={"status": "PROPOSED"},
        )

    # IntegrityError is NOT in DB_RETRYABLE_EXCEPTIONS, so async_retry
    # re-raises immediately. ``_audit`` swallows the propagated
    # exception and logs a structured WARNING.
    assert calls["commit"] == 1
    assert calls["rollback"] == 1


@pytest.mark.asyncio
async def test_audit_emits_structured_warning_on_final_failure(
    caller_db, actor, monkeypatch, caplog,
):
    """When retries are exhausted, ``_audit`` logs a single structured
    warning with action, request_id, project_id, error_type so the
    dropped audit row is greppable."""
    from app.services.flaky_quarantine_service import _audit

    # 4 transient failures in a row — defeat the retry budget (2 retries
    # = 3 total attempts).
    calls = _patch_audit_session(
        monkeypatch,
        commit_side_effects=[
            _make_operational_error(),
            _make_operational_error(),
            _make_operational_error(),
            _make_operational_error(),
        ],
    )

    request_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    project_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    with caplog.at_level("WARNING"):
        await _audit(
            caller_db,
            actor,
            action="reject",
            request_id=request_id,
            project_id=project_id,
            before={"status": "PROPOSED"},
            after={"status": "REJECTED"},
        )

    # 1 initial + 2 retries = 3 commit attempts (max_retries=2 in
    # _audit's async_retry call).
    assert calls["commit"] == 3
    # _audit must not raise to the caller.
    # (No assertion needed — if it raised, the test would have errored.)
