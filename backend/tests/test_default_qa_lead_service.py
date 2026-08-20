"""Unit tests for the per-project default QA-lead auto-provisioning service.

Covers:
  * ``ensure_default_qa_lead`` creates a synthetic user + ProjectMember +
    stamps ``Project.default_qa_lead_user_id`` when none is configured.
  * Re-running on a project that already has a default lead is a no-op
    (no duplicate User, no duplicate ProjectMember).
  * ``reset_default_qa_lead_password`` rehashes the password and returns
    the value passed back to the caller, defaulting to the documented
    constant when none is supplied.

DB execution is fully mocked — these are pure unit tests against the
existing ``FakeExecuteResult`` helpers plus AsyncMock.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.postgres import UserRole


def _scalar(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _project(*, default_qa_lead_user_id=None, slug="demo", name="Demo Project"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        name=name,
        default_qa_lead_user_id=default_qa_lead_user_id,
    )


@pytest.mark.asyncio
async def test_ensure_default_qa_lead_creates_user_when_missing():
    from app.services.default_qa_lead_service import ensure_default_qa_lead

    project = _project()
    db = AsyncMock()
    # 1: SELECT existing User by email (none); 2: SELECT existing ProjectMember (none).
    db.execute = AsyncMock(side_effect=[_scalar(None), _scalar(None)])
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock()

    user = await ensure_default_qa_lead(db, project)

    # New User + new ProjectMember staged. The ``flush`` ladder is twice
    # for the user/member writes plus once for the FK stamp.
    assert db.add.call_count == 2
    assert db.flush.await_count >= 2
    assert project.default_qa_lead_user_id == user.id
    assert user.role == UserRole.QA_LEAD.value
    assert user.is_active is True


@pytest.mark.asyncio
async def test_ensure_default_qa_lead_is_idempotent_when_already_set():
    """Re-running the helper on a project with a healthy FK must not
    touch the User or ProjectMember tables."""
    from app.services.default_qa_lead_service import ensure_default_qa_lead

    existing_user_id = uuid.uuid4()
    existing_user = SimpleNamespace(id=existing_user_id, role=UserRole.QA_LEAD.value)
    project = _project(default_qa_lead_user_id=existing_user_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=existing_user)
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    user = await ensure_default_qa_lead(db, project)

    assert user is existing_user
    db.add.assert_not_called()
    db.execute.assert_not_called()  # short-circuited before any SELECT


@pytest.mark.asyncio
async def test_reset_default_qa_lead_password_returns_a_fresh_secret_when_blank():
    """A blank reset still returns a usable value, but never a shared one.

    This used to assert the returned password equalled a module constant --
    one hard-coded string shared by every synthetic QA-lead account in every
    deployment. Verified live before it was removed: that credential logged in
    as QA_LEAD and could enumerate users, projects and runs.
    """
    from app.services.default_qa_lead_service import (
        reset_default_qa_lead_password,
    )

    existing_user_id = uuid.uuid4()
    existing_user = SimpleNamespace(
        id=existing_user_id,
        role=UserRole.QA_LEAD.value,
        hashed_password="legacy-hash",
        must_change_password=True,
    )
    project = _project(default_qa_lead_user_id=existing_user_id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=existing_user)
    db.flush = AsyncMock()

    user, password = await reset_default_qa_lead_password(db, project)
    _, second = await reset_default_qa_lead_password(db, project)

    # Still returns something the operator can use...
    assert password and len(password) >= 24
    # ...but a different secret each time, so no value is shared across
    # accounts or deployments.
    assert password != second
    assert user is existing_user
    assert user.hashed_password != "legacy-hash"
    assert user.must_change_password is False


@pytest.mark.asyncio
async def test_reset_default_qa_lead_password_accepts_custom_value():
    from app.services.default_qa_lead_service import reset_default_qa_lead_password

    existing_user_id = uuid.uuid4()
    existing_user = SimpleNamespace(
        id=existing_user_id,
        role=UserRole.QA_LEAD.value,
        hashed_password="legacy",
        must_change_password=False,
    )
    project = _project(default_qa_lead_user_id=existing_user_id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=existing_user)
    db.flush = AsyncMock()

    user, password = await reset_default_qa_lead_password(db, project, new_password="Hunter2!")

    assert password == "Hunter2!"
    assert user.hashed_password != "legacy"
