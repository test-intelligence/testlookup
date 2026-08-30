"""Revoking access and changing a role wrote no audit row.

``POST /projects/{id}/members`` called ``log_access_change("member_added", ...)``.
Its two siblings did not:

* ``PATCH /projects/{id}/members/{user_id}`` -- a privilege change inside the
  project -- wrote nothing.
* ``DELETE /projects/{id}/members/{user_id}`` -- revocation -- wrote nothing.

For an *access* audit trail that asymmetry is the worst one available: it
recorded who was given access and never who lost it, so a review reading the
log would see a user as a current member of a project they had been removed
from, and an admin escalating a member's project role left the original, lower
grant as the only trace.

The design already knew about both. ``tests/test_epic6_admin_scalability.py``
listed ``member_removed`` and ``member_role_changed`` in a
``TestAuditActions::test_action_types`` whose body asserted only
``isinstance(action, str)`` and ``len(action) <= 50`` -- true of any string
literal, so it could not fail and verified nothing about the code while reading
as coverage of the vocabulary. That test is corrected in the same change; this
file drives the endpoints.

See [[feedback_a_test_can_pass_having_done_nothing]].
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.postgres import AccessAuditLog, UserRole

pytestmark = pytest.mark.regression


class _Result:
    """Stands in for the SQLAlchemy Result the routers unpack."""

    def __init__(self, *, one=None, scalar=None):
        self._one = one
        self._scalar = scalar

    def one_or_none(self):
        return self._one

    def scalar_one_or_none(self):
        return self._scalar


def _actor():
    """An ADMIN actor: `update_project_member_role` runs `_enforce_grant_ceiling`
    against the caller's own role before it reaches the audit write, so the
    actor needs a real UserRole rather than a bare mock attribute."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.username = "admin-user"
    user.role = UserRole.ADMIN
    return user


def _audit_rows(db) -> list[AccessAuditLog]:
    """Every AccessAuditLog staged on the session, in order."""
    return [
        call.args[0]
        for call in db.add.call_args_list
        if call.args and isinstance(call.args[0], AccessAuditLog)
    ]


@pytest.mark.asyncio
async def test_a_role_change_records_both_the_old_and_new_role(monkeypatch):
    from app.routers import users

    member = MagicMock()
    member.role = "QA_ENGINEER"
    target = MagicMock()
    target.id = uuid.uuid4()

    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_Result(one=(member, target)))
    monkeypatch.setattr(users, "_build_project_member_response", lambda m, u: {"ok": True})
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())

    project_id, user_id = uuid.uuid4(), uuid.uuid4()
    payload = MagicMock()
    payload.role = "QA_LEAD"

    await users.update_project_member_role(
        project_id=project_id, user_id=user_id, payload=payload,
        db=db, current_user=_actor(), _=None,
    )

    rows = _audit_rows(db)
    assert len(rows) == 1, "the role change staged no audit row"
    row = rows[0]
    assert row.action == "member_role_changed"
    assert row.project_id == project_id
    assert row.target_user_id == user_id
    # The before/after pair is the point: an audit row saying only "role
    # changed" cannot answer what it changed from.
    assert row.before_value == {"role": "QA_ENGINEER"}
    assert row.after_value == {"role": "QA_LEAD"}


@pytest.mark.asyncio
async def test_the_previous_role_is_read_before_the_mutation(monkeypatch):
    """`member` is a live ORM object. Reading `member.role` after the
    assignment would record the new value as the old one, and the audit row
    would show a change from X to X — present, and useless."""
    from app.routers import users

    member = MagicMock()
    member.role = "VIEWER"
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_Result(one=(member, MagicMock())))
    monkeypatch.setattr(users, "_build_project_member_response", lambda m, u: {})
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())

    payload = MagicMock()
    payload.role = "ADMIN"

    await users.update_project_member_role(
        project_id=uuid.uuid4(), user_id=uuid.uuid4(), payload=payload,
        db=db, current_user=_actor(), _=None,
    )

    row = _audit_rows(db)[0]
    assert row.before_value != row.after_value, (
        "before and after are identical — the old role was read after the write"
    )
    assert row.before_value == {"role": "VIEWER"}


@pytest.mark.asyncio
async def test_a_revocation_records_what_was_revoked(monkeypatch):
    from app.routers import users

    member = MagicMock()
    member.role = "QA_LEAD"

    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(scalar=member))
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())

    project_id, user_id = uuid.uuid4(), uuid.uuid4()

    await users.remove_project_member(
        project_id=project_id, user_id=user_id,
        db=db, current_user=_actor(), _=None,
    )

    rows = _audit_rows(db)
    assert len(rows) == 1, "the revocation staged no audit row"
    row = rows[0]
    assert row.action == "member_removed"
    assert row.project_id == project_id
    assert row.target_user_id == user_id
    # Captured before the delete — afterwards there is nothing left to say
    # WHAT was revoked, only that something was.
    assert row.before_value == {"role": "QA_LEAD"}
    assert row.after_value is None


@pytest.mark.asyncio
async def test_the_audit_row_is_staged_before_the_commit(monkeypatch):
    """The router owns the transaction, so the audit row must be staged on the
    same session that commits — otherwise a revocation could succeed with its
    audit row rolled back, which is worse than no audit at all."""
    from app.routers import users

    member = MagicMock()
    member.role = "VIEWER"
    order: list[str] = []

    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda obj: order.append("add"))
    db.delete = AsyncMock(side_effect=lambda obj: order.append("delete"))
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    db.execute = AsyncMock(return_value=_Result(scalar=member))
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())

    await users.remove_project_member(
        project_id=uuid.uuid4(), user_id=uuid.uuid4(),
        db=db, current_user=_actor(), _=None,
    )

    assert order.index("add") < order.index("commit")


@pytest.mark.asyncio
async def test_a_missing_membership_audits_nothing(monkeypatch):
    """A 404 is not an access change. Logging one would put revocations in the
    trail that never happened."""
    from fastapi import HTTPException

    from app.routers import users

    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_Result(scalar=None))

    with pytest.raises(HTTPException) as exc:
        await users.remove_project_member(
            project_id=uuid.uuid4(), user_id=uuid.uuid4(),
            db=db, current_user=_actor(), _=None,
        )

    assert exc.value.status_code == 404
    assert _audit_rows(db) == []
