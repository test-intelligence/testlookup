"""Regression: BUG-002 — notification dispatch raised asyncpg
``InterfaceError: cannot perform operation: another operation is in progress``.

Bug pinned (live homelab run 493d5c1f, 2026-06-06):

    Notification dispatch failed: (sqlalchemy...asyncpg.InterfaceError)
    <class 'asyncpg.exceptions._base.InterfaceError'>: cannot perform
    operation: another operation is in progress

Root cause: ``manager._load_and_notify`` fans deliveries out with
``asyncio.gather``. The EMAIL channel went through
``email_service.send_notification`` → ``_get_smtp_cfg``, which opened its OWN
``AsyncSessionLocal`` to read ``smtp_config`` from Postgres. With N email
recipients dispatched concurrently, N sessions opened simultaneously and raced
on the shared engine connection — asyncpg permits only one operation at a time
per connection, hence "another operation is in progress" the moment a second
coroutine's ``transaction.start`` / ``execute`` fired before the first finished.

Fix landed: 2026-06-06. ``_load_and_notify`` resolves the SMTP config exactly
once, before the gather, and threads it through ``_dispatch_to_channel`` →
``send_notification(..., smtp_cfg=...)``. No DB work runs concurrently across
the gathered coroutines any more.

What this file pins:

  * The DB-backed SMTP resolver is invoked at most ONCE per dispatch even when
    several email recipients are notified concurrently.
  * No two SMTP-config DB reads overlap in time (the overlap is exactly what
    asyncpg rejects). A guard models the single-connection constraint and
    raises the real error class if two reads overlap — this test FAILS against
    the pre-fix code and PASSES after.
"""
from __future__ import annotations

import asyncio
import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ``aiosmtplib`` is an optional dependency that may not be installed in the
# local test environment. Stub it before importing the notification package so
# collection doesn't fail; the tests mock the send path anyway.
if "aiosmtplib" not in sys.modules:
    _aiosmtplib_stub = types.ModuleType("aiosmtplib")
    _aiosmtplib_stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _aiosmtplib_stub

from app.services.notification import manager
from app.models.postgres import (
    NotificationChannel,
    NotificationEventType,
)


class _AnotherOperationInProgress(Exception):
    """Stand-in for ``asyncpg.exceptions._base.InterfaceError``.

    The message mirrors the real asyncpg error so the assertion below pins the
    exact failure mode reported in production.
    """

    def __init__(self) -> None:
        super().__init__("cannot perform operation: another operation is in progress")


class _SingleConnectionGuard:
    """Models the asyncpg single-connection constraint shared by the pool.

    Every SMTP-config session that the email path opens executes against this
    one guard (simulating the shared pooled connection that asyncpg serialises).
    If a second ``execute`` begins while the first is still in flight, it raises
    the same InterfaceError seen in production.
    """

    def __init__(self) -> None:
        self.in_flight = 0
        self.read_count = 0
        self.max_concurrency = 0

    async def execute(self):
        self.in_flight += 1
        self.read_count += 1
        self.max_concurrency = max(self.max_concurrency, self.in_flight)
        try:
            if self.in_flight > 1:
                # Two operations on the same connection at once → exactly the
                # asyncpg failure BUG-002 is about.
                raise _AnotherOperationInProgress()
            # Simulate I/O latency so concurrent coroutines actually overlap.
            await asyncio.sleep(0.02)
        finally:
            self.in_flight -= 1


def _smtp_row():
    """An AppSetting-shaped row carrying an enabled SMTP config."""
    return SimpleNamespace(
        key="smtp_config",
        value={
            "enabled": True,
            "host": "smtp.test",
            "port": 587,
            "user": "u",
            "password": "p",
            "from_address": "noreply@test",
            "tls": False,
        },
    )


def _make_pref(user_id, project_id):
    """A NotificationPreference subscribed to RUN_FAILED on the email channel."""
    return SimpleNamespace(
        user_id=user_id,
        project_id=project_id,
        channel=NotificationChannel.EMAIL,
        enabled=True,
        events=[NotificationEventType.RUN_FAILED.value],
        failure_rate_threshold=80.0,
        email_override=None,
        slack_webhook_url=None,
        teams_webhook_url=None,
    )


@pytest.mark.asyncio
async def test_concurrent_email_dispatch_does_not_share_a_db_session(monkeypatch):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Five subscribed email recipients — enough to surface the race.
    prefs_rows = [
        (_make_pref(uuid.uuid4(), project_id), f"user{i}@test")
        for i in range(5)
    ]

    guard = _SingleConnectionGuard()

    # ── Fake session for the manager's own preference-load session ──
    def _prefs_result():
        res = MagicMock()
        res.all = MagicMock(return_value=prefs_rows)
        return res

    class _ManagerSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *a, **k):
            return _prefs_result()

        def add(self, *a, **k):
            return None

        async def commit(self):
            return None

    # ── Fake session for email_service._get_smtp_cfg() — every call shares
    #    the single-connection guard, modelling the pool's one connection. ──
    class _SmtpSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *a, **k):
            await guard.execute()
            res = MagicMock()
            res.scalar_one_or_none = MagicMock(return_value=_smtp_row())
            return res

    monkeypatch.setattr(manager, "AsyncSessionLocal", _ManagerSession)
    # ``email_service._get_smtp_cfg`` does ``from app.db.postgres import
    # AsyncSessionLocal`` locally, which resolves via PEP 562 __getattr__ to
    # ``get_session_factory()``. Patch the factory so every SMTP-config read
    # uses the shared single-connection guard.
    import app.db.postgres as _pg
    monkeypatch.setattr(_pg, "get_session_factory", lambda: _SmtpSession)

    # Mock ONLY the network send so the REAL send_notification ->
    # _get_smtp_cfg DB path runs. This is the path that opened a session per
    # concurrent coroutine before the fix.
    sent = []

    async def _fake_aiosmtplib_send(msg, **kwargs):
        await asyncio.sleep(0.02)
        sent.append(msg["To"])

    monkeypatch.setattr(
        manager.email_service.aiosmtplib, "send", _fake_aiosmtplib_send
    )

    # Drive the real dispatch path concurrently.
    await manager.dispatch_run_notifications(
        project_id=project_id,
        run_id=run_id,
        build_number="42",
        pass_rate=50.0,
        total_tests=10,
        failed_tests=5,
        project_name="Demo",
        dashboard_url="http://x/runs",
    )

    # No two SMTP-config reads overlapped → no asyncpg InterfaceError.
    # (Pre-fix: each of the 5 concurrent email coroutines opened its own
    # _get_smtp_cfg session on the shared connection guard → max_concurrency
    # climbs above 1 and the guard raises the asyncpg error.)
    assert guard.max_concurrency <= 1, (
        "SMTP-config DB reads overlapped on a shared connection — this is the "
        "asyncpg 'another operation in progress' race (BUG-002)."
    )
    # The DB-backed config resolver ran at most once for the whole fan-out.
    assert guard.read_count <= 1, (
        f"_get_smtp_cfg hit the DB {guard.read_count} times; it must be "
        "resolved once before the concurrent fan-out."
    )
    # All five recipients were still served (SMTP enabled via the resolved cfg).
    assert len(sent) == 5


@pytest.mark.asyncio
async def test_send_notification_accepts_preresolved_cfg(monkeypatch):
    """The email service must use a passed-in cfg without touching the DB."""
    from app.services.notification import email_service

    db_hit = {"n": 0}

    async def _boom():
        db_hit["n"] += 1
        raise AssertionError("_get_smtp_cfg must not run when smtp_cfg is provided")

    monkeypatch.setattr(email_service, "_get_smtp_cfg", _boom)

    sent = {}

    async def _fake_aiosmtplib_send(msg, **kwargs):
        sent["host"] = kwargs.get("hostname")

    monkeypatch.setattr(email_service.aiosmtplib, "send", _fake_aiosmtplib_send)

    await email_service.send_notification(
        to="u@test",
        title="t",
        body="b",
        event_type="run_failed",
        metadata={},
        smtp_cfg={
            "enabled": True,
            "host": "smtp.preresolved",
            "port": 587,
            "from_address": "noreply@test",
            "tls": False,
        },
    )

    assert db_hit["n"] == 0
    assert sent.get("host") == "smtp.preresolved"


# ── Delivery mechanics, not the offline ceiling ──────────────────────────
#
# AI_OFFLINE_MODE defaults to True, and since re-audit H10 that refuses any
# notification bound for a destination outside the box — which is every
# destination these tests use. They are about the transport, so they declare
# the online path; the ceiling itself is covered in
# tests/regression/test_offline_notification_egress.py.


@pytest.fixture(autouse=True)
def _delivery_is_online(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
