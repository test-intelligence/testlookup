"""The general profile route records access changes like the dedicated routes do.

The rule: ``PATCH /api/v1/users/{user_id}`` can change a user's role, active
status, e-mail address and username. Each of those that actually changes
writes one access-audit row with the acting administrator, the old value and
the new value, in the same transaction as the change. A field that is absent,
or sent with the value the user already has, writes no row. A refused change
writes no row. A change to the display name alone writes no row: it is not an
access change.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.postgres import AccessAuditLog, UserRole
from app.routers import users as users_router
from app.models.schemas import UpdateUserProfileRequest

pytestmark = pytest.mark.regression


class _Result:
    """An execute() result: the first call finds the user, later ones find nothing."""

    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        # is_last_active_admin counts the OTHER active admins: say there is one.
        return 1

    def first(self):
        return None


def _world(monkeypatch, **user_fields):
    target = SimpleNamespace(
        id=uuid.uuid4(),
        username="pat",
        email="pat@example.com",
        full_name="Pat Example",
        role=UserRole.QA_ENGINEER,
        is_active=True,
        erased_at=None,
    )
    for key, value in user_fields.items():
        setattr(target, key, value)
    admin = SimpleNamespace(id=uuid.uuid4(), username="root-admin", role=UserRole.ADMIN, is_active=True)

    db = MagicMock()
    added: list[object] = []
    db.add = added.append
    db.execute = AsyncMock(side_effect=[_Result(target)] + [_Result() for _ in range(6)])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    from app.services import stream_service

    monkeypatch.setattr(stream_service, "revoke_session_tokens_for_user", AsyncMock())
    return target, admin, db, added


def _audit_rows(added):
    return [row for row in added if isinstance(row, AccessAuditLog)]


async def _patch(target, admin, db, **fields):
    return await users_router.update_user_profile(
        user_id=target.id,
        payload=UpdateUserProfileRequest(**fields),
        db=db,
        current_user=admin,
    )


@pytest.mark.asyncio
async def test_role_change_writes_one_role_changed_row(monkeypatch):
    target, admin, db, added = _world(monkeypatch)

    await _patch(target, admin, db, role=UserRole.ADMIN)

    rows = _audit_rows(added)
    assert [row.action for row in rows] == ["role_changed"]
    row = rows[0]
    assert row.actor_user_id == admin.id
    assert row.actor_name == "root-admin"
    assert row.target_user_id == target.id
    assert row.before_value == {"role": str(UserRole.QA_ENGINEER)}
    assert row.after_value == {"role": str(UserRole.ADMIN)}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_status_change_writes_one_status_changed_row(monkeypatch):
    target, admin, db, added = _world(monkeypatch)

    await _patch(target, admin, db, is_active=False)

    rows = _audit_rows(added)
    assert [row.action for row in rows] == ["status_changed"]
    assert rows[0].actor_user_id == admin.id
    assert rows[0].target_user_id == target.id
    assert rows[0].before_value == {"is_active": True}
    assert rows[0].after_value == {"is_active": False}


@pytest.mark.asyncio
async def test_email_and_username_changes_each_write_a_row(monkeypatch):
    target, admin, db, added = _world(monkeypatch)

    await _patch(target, admin, db, email="pat.new@example.com", username="pat2")

    rows = {row.action: row for row in _audit_rows(added)}
    assert sorted(rows) == ["email_changed", "username_changed"]
    assert rows["email_changed"].before_value == {"email": "pat@example.com"}
    assert rows["email_changed"].after_value == {"email": "pat.new@example.com"}
    assert rows["username_changed"].before_value == {"username": "pat"}
    assert rows["username_changed"].after_value == {"username": "pat2"}
    assert all(row.actor_user_id == admin.id and row.target_user_id == target.id for row in rows.values())


@pytest.mark.asyncio
async def test_all_four_fields_write_four_rows(monkeypatch):
    target, admin, db, added = _world(monkeypatch)

    await _patch(
        target, admin, db,
        role=UserRole.VIEWER, is_active=False, email="other@example.com", username="other",
    )

    assert sorted(row.action for row in _audit_rows(added)) == [
        "email_changed", "role_changed", "status_changed", "username_changed",
    ]


@pytest.mark.asyncio
async def test_display_name_only_writes_no_row_and_still_saves(monkeypatch):
    target, admin, db, added = _world(monkeypatch)

    await _patch(target, admin, db, full_name="Pat Renamed")

    assert target.full_name == "Pat Renamed"
    assert _audit_rows(added) == []
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unchanged_values_write_no_row(monkeypatch):
    target, admin, db, added = _world(monkeypatch)

    await _patch(
        target, admin, db,
        role=UserRole.QA_ENGINEER, is_active=True, email="pat@example.com", username="pat",
    )

    assert _audit_rows(added) == []


@pytest.mark.asyncio
async def test_refused_change_writes_no_row(monkeypatch):
    target, admin, db, added = _world(monkeypatch)
    target.id = admin.id  # an administrator may not change their own role

    with pytest.raises(HTTPException) as refused:
        await _patch(target, admin, db, email="new@example.com", role=UserRole.VIEWER)

    assert refused.value.status_code == 400
    assert _audit_rows(added) == []
    db.commit.assert_not_awaited()
