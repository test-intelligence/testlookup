"""Webhook subscription CRUD — commit-cleanup contract (P1 follow-up).

Pins the 2026-05-16 webhook cleanup: ``create_subscription`` /
``update_subscription`` / ``delete_subscription`` previously committed
the primary mutation 1-3× before calling ``_audit`` to preserve "audit
failure doesn't lose the subscription" semantics. The cleanup moves
audit to a fresh ``AsyncSessionLocal()`` (P2-4 pattern) and lets
``get_db`` commit the primary mutation once at request end.

Contract:
1. The subscription mutation flushes but never commits on the injected
   session.
2. ``_audit`` opens its own session — it must NOT touch the caller's
   ``db``.
3. Existing callers (router handlers using ``Depends(get_db)``) still
   get the same end-state: subscription row + audit row both persisted.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")


def _fake_caller_db():
    """Caller's session — commit + rollback assertions guard against the
    pre-cleanup multi-commit pattern coming back."""
    db = MagicMock(name="caller_db")
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    # ``create_subscription`` now project-guards with ``db.get(Project,
    # project_id)`` (see Phase-OS-Deploy follow-up). Default to a non-None
    # sentinel so the guard passes; tests targeting the missing-project
    # path override this with ``AsyncMock(return_value=None)``.
    db.get = AsyncMock(return_value=SimpleNamespace(id="project-sentinel"))
    db.commit = AsyncMock(side_effect=AssertionError(
        "caller's session must not be committed — get_db owns the transaction"
    ))
    db.rollback = AsyncMock(side_effect=AssertionError(
        "caller's session must not be rolled back"
    ))
    db.refresh = AsyncMock()
    return db


def _fake_actor():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.com",
    )


@pytest.fixture(autouse=True)
def _stub_audit_session(monkeypatch):
    """Stub the fresh-session factory ``_audit`` uses so the test
    doesn't try to open a real DB connection. Returns a tracker so
    tests can assert the audit row landed."""
    rows: list = []

    class _FakeAuditSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        def add(self, entry):
            rows.append(entry)
        async def commit(self):
            return None
        async def rollback(self):
            return None

    def _factory():
        return _FakeAuditSession()

    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal", _factory, raising=False,
    )
    return rows


@pytest.fixture(autouse=True)
def _stub_secret_store(monkeypatch):
    """``store_secret`` writes to the caller's session — we don't care
    about its behaviour here, only that the audit/commit contract holds."""
    async def _noop(*args, **kwargs):
        return ""
    monkeypatch.setattr(
        "app.services.secret_service.store_secret", _noop, raising=False,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_subscription_flushes_but_does_not_commit(_stub_audit_session):
    """The router (Depends(get_db)) commits once at request end."""
    from app.services.webhook_service import create_subscription

    db = _fake_caller_db()
    actor = _fake_actor()
    project_id = uuid.uuid4()

    await create_subscription(
        db,
        project_id=project_id,
        actor=actor,
        name="release-events",
        target_url="https://example.test/hook",
        events=["run.completed"],
        enabled=True,
        max_retries=3,
        secret=None,
    )

    # Subscription was added + flushed (id materialised).
    assert db.add.called
    db.flush.assert_awaited_once()
    # Critical: the caller's session was never committed inside the service.
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    # Audit row was written via the fresh session, not via db.add a second time.
    assert len(_stub_audit_session) == 1


@pytest.mark.asyncio
async def test_update_subscription_does_not_commit_on_caller_session(_stub_audit_session):
    """Existing row mutations flow through to get_db's request-end commit."""
    from app.services.webhook_service import update_subscription

    sub_id = uuid.uuid4()
    actor = _fake_actor()
    existing_row = SimpleNamespace(
        id=sub_id,
        name="old-name",
        target_url="https://old.example/hook",
        events=["run.completed"],
        enabled=True,
        max_retries=5,
        has_secret=False,
        updated_by_user_id=None,
        updated_at=None,
    )

    # ``get_subscription`` is private to the module and synchronously
    # awaits a db.execute(...). The simplest stub is to monkeypatch it.
    db = _fake_caller_db()

    import app.services.webhook_service as svc

    async def _fake_get_sub(_db, _id):
        return existing_row
    original = svc.get_subscription
    svc.get_subscription = _fake_get_sub
    try:
        row = await update_subscription(
            db,
            subscription_id=sub_id,
            actor=actor,
            name="new-name",
            target_url=None,
            events=None,
            enabled=None,
            max_retries=None,
            secret=None,
        )
    finally:
        svc.get_subscription = original

    assert row is existing_row
    assert existing_row.name == "new-name"
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    assert len(_stub_audit_session) == 1


@pytest.mark.asyncio
async def test_delete_subscription_does_not_commit_on_caller_session(_stub_audit_session):
    from app.services.webhook_service import delete_subscription

    sub_id = uuid.uuid4()
    actor = _fake_actor()
    existing_row = SimpleNamespace(id=sub_id)

    db = _fake_caller_db()

    import app.services.webhook_service as svc

    async def _fake_get_sub(_db, _id):
        return existing_row
    original = svc.get_subscription
    svc.get_subscription = _fake_get_sub
    try:
        await delete_subscription(db, sub_id, actor)
    finally:
        svc.get_subscription = original

    db.delete.assert_awaited_once_with(existing_row)
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    assert len(_stub_audit_session) == 1
