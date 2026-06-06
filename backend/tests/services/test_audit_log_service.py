"""Tests for the P2-6 audit-log helper.

Pins:

1. ``record_outcome(db, ...)`` adds the audit row to the caller's
   session — no commit, no rollback. Caller controls durability.
2. ``record_attempt(...)`` uses a FRESH session and commits
   independently — survives a caller rollback.
3. ``record_attempt`` retries transient DB errors but NOT
   ``IntegrityError`` (constraint violations).
4. ``record_attempt`` emits a structured WARNING on terminal failure
   and never raises to the caller.
5. ``changed_fields`` derivation: explicit list wins; otherwise the
   diff of before/after is used.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")


def _make_operational_error() -> Exception:
    from sqlalchemy.exc import OperationalError
    return OperationalError("SELECT 1", {}, Exception("simulated"))


def _make_integrity_error() -> Exception:
    from sqlalchemy.exc import IntegrityError
    return IntegrityError("INSERT ...", {}, Exception("duplicate key"))


def _patch_fresh_session(monkeypatch, *, commit_side_effects):
    """Install a fake AsyncSessionLocal for record_attempt to use."""
    calls = {"commit": 0, "rollback": 0, "added": []}

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
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

    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal",
        _FakeSession,
        raising=False,
    )
    return calls


# ── changed_fields derivation ────────────────────────────────────────────────


def test_changed_fields_explicit_wins():
    from app.services.audit_log_service import _build_changed_fields
    out = _build_changed_fields({"a": 1}, {"a": 2}, explicit=["custom"])
    assert out == ["custom"]


def test_changed_fields_diff_before_after():
    from app.services.audit_log_service import _build_changed_fields
    out = _build_changed_fields({"a": 1, "b": 2}, {"a": 1, "b": 99, "c": 3})
    # ``b`` changed, ``c`` added.
    assert out == ["b", "c"]


def test_changed_fields_after_only_returns_keys():
    from app.services.audit_log_service import _build_changed_fields
    out = _build_changed_fields(None, {"name": "x", "status": "active"})
    assert out == ["name", "status"]


def test_changed_fields_neither_returns_empty():
    from app.services.audit_log_service import _build_changed_fields
    assert _build_changed_fields(None, None) == []


# ── record_outcome (caller's session) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_outcome_adds_to_caller_session_without_committing():
    from app.services.audit_log_service import record_outcome

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock(side_effect=AssertionError(
        "record_outcome must NOT commit — caller owns the transaction"
    ))

    await record_outcome(
        db,
        setting_key="project.42",
        action="toggle",
        actor_id=uuid.uuid4(),
        actor_name="alice",
        after={"enabled": True},
    )

    db.add.assert_called_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_outcome_swallows_build_errors_and_warns():
    """If SettingsAuditLog construction fails for some reason (e.g. a
    field type bug), record_outcome must NOT raise into the caller —
    the audit is best-effort. The caller's primary mutation continues.

    We assert the warning by spying the service logger directly. Capturing
    structlog's *output* (capfd/capsys) is order-fragile: ``configure_logging``
    binds the stdlib StreamHandler to whichever ``sys.stdout`` was current the
    first time it ran and caches loggers, so an earlier test in the full suite
    can route this line to a stale stream that ``capfd`` never sees. Spying the
    logger is independent of structlog config, streams, and test ordering."""
    from app.services.audit_log_service import record_outcome

    # A db that throws on .add() simulates "row build failed" — easier
    # than crafting an invalid SettingsAuditLog.
    db = MagicMock()
    db.add = MagicMock(side_effect=RuntimeError("model construction failed"))

    with patch("app.services.audit_log_service.logger") as mock_logger:
        await record_outcome(
            db,
            setting_key="project.42",
            action="toggle",
            actor_id=uuid.uuid4(),
        )

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.args[0] == "audit_log_outcome_dropped"


# ── record_attempt (fresh session) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_attempt_uses_fresh_session_not_a_caller(monkeypatch):
    """record_attempt opens its own AsyncSessionLocal — no caller
    session passed in at all."""
    from app.services.audit_log_service import record_attempt

    calls = _patch_fresh_session(monkeypatch, commit_side_effects=[None])

    await record_attempt(
        setting_key="project_reset.full",
        action="reset",
        actor_id=uuid.uuid4(),
        after={"counts": {"test_runs": 100}},
    )

    assert calls["commit"] == 1
    assert len(calls["added"]) == 1


@pytest.mark.asyncio
async def test_record_attempt_retries_transient_db_error(monkeypatch):
    """A transient OperationalError must trigger retry; the audit row
    eventually lands."""
    from app.services.audit_log_service import record_attempt

    calls = _patch_fresh_session(
        monkeypatch,
        commit_side_effects=[_make_operational_error(), None],
    )

    await record_attempt(
        setting_key="webhook_subscription:42",
        action="update",
        actor_id=uuid.uuid4(),
    )

    assert calls["commit"] == 2
    assert calls["rollback"] == 1


@pytest.mark.asyncio
async def test_record_attempt_does_not_retry_integrity_error(monkeypatch):
    """IntegrityError is permanent — must NOT be retried."""
    from app.services.audit_log_service import record_attempt

    calls = _patch_fresh_session(
        monkeypatch,
        commit_side_effects=[_make_integrity_error()],
    )

    await record_attempt(
        setting_key="x",
        action="bad",
    )

    assert calls["commit"] == 1
    assert calls["rollback"] == 1


@pytest.mark.asyncio
async def test_record_attempt_emits_warning_on_terminal_failure(monkeypatch):
    """When retries are exhausted, a structured WARNING lands and the
    function returns normally (never raises).

    Spy the service logger directly rather than capturing structlog output —
    output capture (capfd/capsys) is order-fragile in the full suite (see the
    note on ``test_record_outcome_swallows_build_errors_and_warns``)."""
    from app.services.audit_log_service import record_attempt

    _patch_fresh_session(
        monkeypatch,
        commit_side_effects=[_make_operational_error()] * 5,
    )

    with patch("app.services.audit_log_service.logger") as mock_logger:
        await record_attempt(
            setting_key="x",
            action="will_fail",
            max_retries=2,
        )

    assert any(
        call.args and call.args[0] == "audit_log_attempt_dropped"
        for call in mock_logger.warning.call_args_list
    )
