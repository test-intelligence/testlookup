"""Pin the project-existence guard contract across entity-create services.

The pattern (originally landed in
``test_management_service.create_managed_test_case``):

    project = await db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(404, detail="Project ... not found ...")

Each entity-creation service must apply this guard *before* any insert
so a stale ``activeProjectId`` on the frontend (or a hand-rolled API
call against a deleted project) surfaces an actionable 404 instead of
an opaque 500 from an asyncpg ForeignKeyViolationError at commit time.

These tests don't try to exercise the rest of the create-path — they
mock just enough to reach the guard and verify it fires. Service-
specific behaviour stays covered by the other test files; this one
defends the contract at the boundary.

Phase OS-Deploy follow-up (`docs/BACKLOG.md` § "Project-existence
guard on remaining entity-create services").
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _fake_user():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="alice",
        email="alice@example.com",
        full_name="Alice",
    )


# ── release_service.create_release ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_release_404s_when_project_missing():
    from app.services.release_service import create_release

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=None)

    body = SimpleNamespace(
        project_id=str(uuid.uuid4()),
        name="v1.0.0",
        version="1.0.0",
        description=None,
        status="planning",
        planned_date=None,
        phases=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_release(db, body)
    assert exc_info.value.status_code == 404
    # Guard must fire BEFORE any DB write — no Release row was staged.
    db.add.assert_not_called()


# ── analytics_service.create_manual_defect ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_manual_defect_404s_when_project_missing():
    from app.services.analytics_service import create_manual_defect

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await create_manual_defect(
            db,
            project_id=uuid.uuid4(),
            payload={"title": "broken", "severity": "P1"},
        )
    assert exc_info.value.status_code == 404
    db.add.assert_not_called()
    # And `_find_recent_test_case_id` would issue a SELECT — guard must
    # short-circuit before that too, so no DB read happens either.
    db.execute.assert_not_awaited()


# ── knowledge_source_service.create_source ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_source_404s_when_project_missing(monkeypatch):
    from app.services import knowledge_source_service as svc

    # Bypass the RAG feature-flag gate + access check so the test
    # exercises the existence guard specifically.
    monkeypatch.setattr(svc, "require_rag_enabled_async", AsyncMock())
    monkeypatch.setattr(svc, "_check_project_access", AsyncMock())

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_source(
            db,
            project_id=uuid.uuid4(),
            payload={"source_type": "external_url", "canonical_url": "https://x"},
            user=_fake_user(),
        )
    assert exc_info.value.status_code == 404


# ── webhook_service.create_subscription ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_subscription_404s_when_project_missing(monkeypatch):
    from app.services.webhook_service import create_subscription

    # The function calls validate_events first; the test payload uses a
    # known-valid event so validation doesn't short-circuit before the
    # guard runs.
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await create_subscription(
            db,
            project_id=uuid.uuid4(),
            actor=_fake_user(),
            name="release-events",
            target_url="https://example.test/hook",
            events=["run.completed"],
            enabled=True,
            max_retries=3,
            secret=None,
        )
    assert exc_info.value.status_code == 404
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
