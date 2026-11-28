"""Extra job roles: who may set them, how they are stored and audited (enterprise roles E00-S05, D-16).

* Users: only an ADMIN sets a person's global extra roles (PATCH /users/{id}); never their own; never on an
  AGENT; a primary role is never also an extra role (re-cleaned when the primary role changes); every change
  writes an ``additional_roles_changed`` access-audit row.
* Members: a non-admin may add an extra role on a membership only if they hold every permission of it IN THAT
  PROJECT (their instance grants intersected with their project grants); the refusal names what they lack. The
  release, project and product managers are given only by an administrator (owner decision D-17).
  Omitted extra roles are kept, ``[]`` clears them, and only a newly added extra role is checked.
* Requests: an extra role is one of the four job roles; anything else is a 422 from the schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.postgres import ProjectMember, UserRole
from app.models.schemas import (
    AddProjectMemberRequest,
    AdminCreateUserRequest,
    InviteProjectMembership,
    InviteUserRequest,
    UpdateProjectMemberRoleRequest,
    UpdateUserProfileRequest,
)
from app.routers import users as users_router

pytestmark = pytest.mark.regression


def _person(role, additional=None):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, additional_roles=additional, email="p@example.com", username="person",
        full_name="Person", is_active=True, erased_at=None, is_service_account=False, is_synthetic=False,
        created_at=datetime.now(timezone.utc),
    )


def _result(*, scalar=None, one=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.one_or_none.return_value = one
    r.first.return_value = scalar
    return r


@pytest.fixture
def audit(monkeypatch):
    rows: list[tuple] = []

    async def _log(db, action, actor, **kw):
        rows.append((action, kw.get("before_value"), kw.get("after_value")))

    monkeypatch.setattr(users_router, "log_access_change", _log)
    return rows


# -- request schemas -----------------------------------------------------------

@pytest.mark.parametrize("bad", ["AGENT", "QA_LEAD", "ADMIN", "VIEWER", "SUPERUSER"])
def test_an_extra_role_is_a_job_role_or_a_422(bad):
    for model, kw in (
        (UpdateUserProfileRequest, {}),
        (AdminCreateUserRequest, {"email": "a@example.com", "username": "abc"}),
        (InviteUserRequest, {"email": "a@example.com"}),
        (InviteProjectMembership, {"project_id": uuid.uuid4()}),
        (AddProjectMemberRequest, {"user_id": uuid.uuid4()}),
        (UpdateProjectMemberRoleRequest, {"role": "VIEWER"}),
    ):
        with pytest.raises(ValidationError):
            model(additional_roles=[bad], **kw)


def test_the_four_job_roles_are_accepted():
    jobs = ["RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER"]
    assert AddProjectMemberRequest(user_id=uuid.uuid4(), additional_roles=jobs).additional_roles == jobs


def test_omitted_extra_roles_are_none_on_an_update_and_empty_on_a_create():
    assert UpdateUserProfileRequest().additional_roles is None
    assert UpdateProjectMemberRoleRequest(role="VIEWER").additional_roles is None
    assert AddProjectMemberRequest(user_id=uuid.uuid4()).additional_roles == []


# -- global extra roles --------------------------------------------------------

async def _patch_user(target, payload, actor=None):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(scalar=target))
    db.refresh = AsyncMock()
    return await users_router.update_user_profile(
        target.id, payload, db=db, current_user=actor or _person(UserRole.ADMIN),
    )


async def test_an_admin_sets_a_persons_extra_roles_and_it_is_audited(audit):
    target = _person(UserRole.QA_ENGINEER)
    await _patch_user(target, UpdateUserProfileRequest(additional_roles=["DEVELOPER", "RELEASE_MANAGER"]))
    assert target.additional_roles == ["RELEASE_MANAGER", "DEVELOPER"]  # enum order, not request order
    assert audit == [("additional_roles_changed", {"additional_roles": []},
                      {"additional_roles": ["RELEASE_MANAGER", "DEVELOPER"]})]


async def test_an_empty_list_clears_them_and_omitting_keeps_them(audit):
    target = _person(UserRole.QA_ENGINEER, ["RELEASE_MANAGER"])
    await _patch_user(target, UpdateUserProfileRequest(full_name="Renamed"))
    assert target.additional_roles == ["RELEASE_MANAGER"] and audit == []
    await _patch_user(target, UpdateUserProfileRequest(additional_roles=[]))
    assert target.additional_roles is None
    assert audit[-1][0] == "additional_roles_changed"


async def test_nobody_changes_their_own_extra_roles(audit):
    admin = _person(UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await _patch_user(admin, UpdateUserProfileRequest(additional_roles=["DEVELOPER"]), actor=admin)
    assert exc.value.status_code == 400


async def test_an_agent_holds_no_extra_roles(audit):
    with pytest.raises(HTTPException) as exc:
        await _patch_user(_person(UserRole.AGENT), UpdateUserProfileRequest(additional_roles=["DEVELOPER"]))
    assert exc.value.status_code == 400 and "agent" in exc.value.detail.lower()


async def test_a_new_primary_role_drops_the_same_extra_role(audit, monkeypatch):
    monkeypatch.setattr(users_router, "_assert_not_last_admin", AsyncMock())
    target = _person(UserRole.QA_ENGINEER, ["RELEASE_MANAGER", "DEVELOPER"])
    await _patch_user(target, UpdateUserProfileRequest(role=UserRole.DEVELOPER))
    assert target.role == UserRole.DEVELOPER and target.additional_roles == ["RELEASE_MANAGER"]
    assert [row[0] for row in audit] == ["role_changed", "additional_roles_changed"]


async def test_admin_create_stores_the_extra_roles(monkeypatch):
    added = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result(scalar=None))
    db.add = MagicMock(side_effect=added.append)

    async def _refresh(obj):
        obj.id, obj.created_at, obj.is_active = uuid.uuid4(), datetime.now(timezone.utc), True

    db.refresh = AsyncMock(side_effect=_refresh)
    payload = AdminCreateUserRequest(
        email="new@example.com", username="newbie", role=UserRole.DEVELOPER,
        additional_roles=["DEVELOPER", "PRODUCT_MANAGER"],
    )
    out = await users_router.admin_create_user(payload, db=db, current_user=_person(UserRole.ADMIN))
    assert added[0].additional_roles == ["PRODUCT_MANAGER"]  # the primary role is not repeated
    assert out.additional_roles == [UserRole.PRODUCT_MANAGER]


async def test_invite_memberships_carry_their_extra_roles():
    db = AsyncMock()
    project = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    db.execute = AsyncMock(return_value=_result(scalar=project))
    payload = InviteUserRequest(
        email="i@example.com",
        additional_roles=["RELEASE_MANAGER"],
        project_memberships=[
            InviteProjectMembership(project_id=project.id, role=UserRole.DEVELOPER, additional_roles=["DEVELOPER"]),
        ],
    )
    entries = await users_router._validated_invite_memberships(db, payload)
    assert entries == [{"project_id": str(project.id), "role": "DEVELOPER"}]  # nothing extra once de-duplicated
    payload.project_memberships[0].additional_roles = ["PROJECT_MANAGER"]
    entries = await users_router._validated_invite_memberships(db, payload)
    assert entries[0]["additional_roles"] == ["PROJECT_MANAGER"]


# -- project members ------------------------------------------------------------

def test_a_lead_may_add_a_developer_extra_role_but_not_a_manager_one():
    lead = _person(UserRole.QA_LEAD)
    users_router._enforce_grant_ceiling(lead, UserRole.QA_ENGINEER, uuid.uuid4(), additional_roles=["DEVELOPER"])
    for manager in ("RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER"):
        with pytest.raises(HTTPException) as exc:
            users_router._enforce_grant_ceiling(lead, UserRole.QA_ENGINEER, uuid.uuid4(), additional_roles=[manager])
        assert exc.value.status_code == 403
        assert exc.value.detail == f"Only an administrator can give the {manager} role"


def test_a_mixed_refusal_names_both_reasons():
    with pytest.raises(HTTPException) as exc:
        users_router._enforce_grant_ceiling(
            _person(UserRole.QA_LEAD), UserRole.ADMIN, uuid.uuid4(), additional_roles=["PROJECT_MANAGER"],
        )
    assert "ADMIN needs" in exc.value.detail and "PROJECT_MANAGER is given only by an administrator" in exc.value.detail


def test_the_project_role_caps_what_a_lead_hands_out_there():
    lead = _person(UserRole.QA_LEAD)
    target = uuid.uuid4()
    # Instance roles alone: may grant DEVELOPER.
    users_router._enforce_grant_ceiling(lead, UserRole.DEVELOPER, target)
    # In a project where they are only a TESTER, the intersection lacks quarantine.propose.
    with pytest.raises(HTTPException) as exc:
        users_router._enforce_grant_ceiling(lead, UserRole.DEVELOPER, target, granter_project_roles=(UserRole.TESTER,))
    assert "DEVELOPER needs" in exc.value.detail
    users_router._enforce_grant_ceiling(lead, UserRole.DEVELOPER, target, granter_project_roles=(UserRole.QA_LEAD,))


def test_a_long_list_of_missing_permissions_is_shortened():
    with pytest.raises(HTTPException) as exc:
        users_router._enforce_grant_ceiling(_person(UserRole.TESTER), UserRole.ADMIN, uuid.uuid4())
    assert "ADMIN needs" in exc.value.detail and "more)" in exc.value.detail


async def test_adding_a_member_stores_and_audits_the_extra_roles(audit, monkeypatch):
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    added = []
    member_user = _person(UserRole.QA_ENGINEER)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _result(scalar=SimpleNamespace(id=uuid.uuid4())),  # project
        _result(scalar=member_user),                       # user
        _result(scalar=None),                              # not yet a member
    ])
    db.add = MagicMock(side_effect=added.append)

    async def _refresh(obj):
        obj.id, obj.created_at = uuid.uuid4(), datetime.now(timezone.utc)

    db.refresh = AsyncMock(side_effect=_refresh)
    payload = AddProjectMemberRequest(user_id=member_user.id, role=UserRole.QA_ENGINEER, additional_roles=["DEVELOPER"])
    out = await users_router.add_project_member(
        project_id=uuid.uuid4(), payload=payload, db=db, current_user=_person(UserRole.ADMIN),
    )
    member = next(obj for obj in added if isinstance(obj, ProjectMember))
    assert member.additional_roles == ["DEVELOPER"] and out.additional_roles == [UserRole.DEVELOPER]
    assert audit == [("member_added", None, {"role": "QA_ENGINEER", "additional_roles": ["DEVELOPER"]})]


async def _update_member(member, payload, actor, granter_row=None):
    member_user = _person(UserRole.QA_ENGINEER)
    db = AsyncMock()
    results = []
    if _role_name(actor.role) != "ADMIN":
        results.append(_result(one=granter_row))
    results.append(_result(one=(member, member_user)))
    db.execute = AsyncMock(side_effect=results)

    async def _refresh(obj):
        obj.created_at = datetime.now(timezone.utc)

    db.refresh = AsyncMock(side_effect=_refresh)
    return await users_router.update_project_member_role(
        project_id=member.project_id, user_id=member.user_id, payload=payload, db=db, current_user=actor,
    )


def _role_name(role):
    return getattr(role, "value", role)


def _member(role="QA_ENGINEER", extras=None):
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=uuid.uuid4(), role=role, additional_roles=extras,
        created_at=datetime.now(timezone.utc), source=None,
    )


async def test_updating_a_member_keeps_extra_roles_unless_told(audit, monkeypatch):
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    member = _member(extras=["DEVELOPER"])
    await _update_member(member, UpdateProjectMemberRoleRequest(role=UserRole.TESTER), _person(UserRole.ADMIN))
    assert member.additional_roles == ["DEVELOPER"]
    await _update_member(member, UpdateProjectMemberRoleRequest(role=UserRole.TESTER, additional_roles=[]),
                         _person(UserRole.ADMIN))
    assert member.additional_roles is None
    assert audit[-1] == ("member_role_changed",
                         {"role": UserRole.TESTER, "additional_roles": ["DEVELOPER"]},
                         {"role": UserRole.TESTER, "additional_roles": []})


async def test_a_lead_keeps_an_extra_role_an_admin_gave_but_cannot_add_one(audit, monkeypatch):
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    lead = _person(UserRole.QA_LEAD)
    lead_row = ("QA_LEAD", None)
    member = _member(extras=["RELEASE_MANAGER"])  # an admin gave it
    # Changing the primary role and keeping RELEASE_MANAGER is not a grant of it.
    await _update_member(member, UpdateProjectMemberRoleRequest(role=UserRole.TESTER), lead, lead_row)
    assert member.role == UserRole.TESTER and member.additional_roles == ["RELEASE_MANAGER"]
    # Adding a manager role to someone without it is a grant only an administrator makes (D-17).
    plain = _member()
    with pytest.raises(HTTPException) as exc:
        await _update_member(
            plain,
            UpdateProjectMemberRoleRequest(role=UserRole.TESTER, additional_roles=["RELEASE_MANAGER", "PRODUCT_MANAGER"]),
            lead, lead_row,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Only an administrator can give the RELEASE_MANAGER, PRODUCT_MANAGER role"
    # DEVELOPER is inside the lead's own permissions.
    await _update_member(
        plain, UpdateProjectMemberRoleRequest(role=UserRole.TESTER, additional_roles=["DEVELOPER"]), lead, lead_row,
    )
    assert plain.additional_roles == ["DEVELOPER"]


async def test_the_route_caps_a_lead_by_their_role_in_that_project(audit, monkeypatch):
    """A QA lead globally who is only a TESTER in this project cannot hand out DEVELOPER here (D-2: the lower
    of the two decides); as a QA lead in the project too, they can."""
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    lead = _person(UserRole.QA_LEAD)
    payload = UpdateProjectMemberRoleRequest(role=UserRole.TESTER, additional_roles=["DEVELOPER"])
    with pytest.raises(HTTPException) as exc:
        await _update_member(_member(), payload, lead, granter_row=("TESTER", None))
    assert exc.value.status_code == 403 and "DEVELOPER needs" in exc.value.detail
    member = _member()
    await _update_member(member, payload, lead, granter_row=("QA_LEAD", None))
    assert member.additional_roles == ["DEVELOPER"]


async def test_adding_a_member_is_capped_by_the_granters_project_role(audit, monkeypatch):
    """The add route applies the same cap: a QA lead who is only a TESTER in this project cannot add DEVELOPER."""
    monkeypatch.setattr("app.core.deps.invalidate_membership_cache", AsyncMock())
    lead = _person(UserRole.QA_LEAD)
    target = _person(UserRole.QA_ENGINEER)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _result(scalar=SimpleNamespace(id=uuid.uuid4())),  # project
        _result(scalar=target),                            # user
        _result(one=("TESTER", None)),                     # the granter's membership in this project
    ])
    payload = AddProjectMemberRequest(user_id=target.id, role=UserRole.TESTER, additional_roles=["DEVELOPER"])
    with pytest.raises(HTTPException) as exc:
        await users_router.add_project_member(project_id=uuid.uuid4(), payload=payload, db=db, current_user=lead)
    assert exc.value.status_code == 403 and "DEVELOPER needs" in exc.value.detail
    assert audit == []


async def test_a_member_route_never_grants_agent():
    with pytest.raises(HTTPException) as exc:
        await _update_member(_member(), UpdateProjectMemberRoleRequest(role=UserRole.AGENT), _person(UserRole.ADMIN))
    assert exc.value.status_code == 400
