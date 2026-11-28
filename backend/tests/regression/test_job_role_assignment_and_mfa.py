"""Assigning the new roles, and the MFA floor for them (enterprise roles E00-S05).

* Grant ceiling: a non-admin may grant a project role only if they hold every
  permission of it (``permissions.can_grant``). For two ladder roles that is the
  old "not above your own rank"; a QA lead may hand out DEVELOPER. The release,
  project and product managers are given only by an administrator (owner
  decision D-17), and nobody grants AGENT through the members routes.
* MFA (owner decision D-9): RELEASE_MANAGER and PRODUCT_MANAGER sign and decide
  releases, so a policy that requires MFA from QA_LEAD up covers them, as a
  primary role or an extra one. Other job roles count as their rank, VIEWER.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.postgres import UserRole
from app.routers.users import _enforce_grant_ceiling
from app.services.mfa_service import MfaPolicy, policy_requires_mfa

pytestmark = pytest.mark.regression


def _user(role, additional=None):
    return SimpleNamespace(id=uuid.uuid4(), role=role, additional_roles=additional)


@pytest.mark.parametrize("target", [UserRole.DEVELOPER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.VIEWER])
def test_a_qa_lead_may_grant_roles_inside_their_own_permissions(target):
    _enforce_grant_ceiling(_user(UserRole.QA_LEAD), target, uuid.uuid4())  # no raise


@pytest.mark.parametrize("target", [UserRole.RELEASE_MANAGER, UserRole.PROJECT_MANAGER, UserRole.PRODUCT_MANAGER,
                                    UserRole.ADMIN])
def test_a_qa_lead_may_not_grant_these(target):
    with pytest.raises(HTTPException) as exc:
        _enforce_grant_ceiling(_user(UserRole.QA_LEAD), target, uuid.uuid4())
    assert exc.value.status_code == 403


@pytest.mark.parametrize("target", [UserRole.RELEASE_MANAGER, UserRole.PROJECT_MANAGER, UserRole.PRODUCT_MANAGER])
def test_the_manager_roles_are_given_only_by_an_administrator(target):
    """D-17: whatever the granter holds (a QA lead holds every PROJECT_MANAGER / PRODUCT_MANAGER permission)."""
    with pytest.raises(HTTPException) as exc:
        _enforce_grant_ceiling(_user(UserRole.QA_LEAD), target, uuid.uuid4())
    assert exc.value.detail == f"Only an administrator can give the {target.value} role"
    _enforce_grant_ceiling(_user(UserRole.ADMIN), target, uuid.uuid4())  # no raise


def test_agent_is_never_granted_through_the_members_routes():
    for granter in (UserRole.QA_LEAD, UserRole.ADMIN):
        with pytest.raises(HTTPException) as exc:
            _enforce_grant_ceiling(_user(granter), UserRole.AGENT, uuid.uuid4())
        assert exc.value.status_code == 400


def test_a_qa_engineer_may_not_grant_developer():
    with pytest.raises(HTTPException):
        _enforce_grant_ceiling(_user(UserRole.QA_ENGINEER), UserRole.DEVELOPER, uuid.uuid4())


def test_a_release_manager_may_not_grant_a_qa_lead():
    with pytest.raises(HTTPException):
        _enforce_grant_ceiling(_user(UserRole.RELEASE_MANAGER), UserRole.QA_LEAD, uuid.uuid4())


def test_holding_a_manager_role_does_not_let_a_lead_give_it():
    # D-17: a QA lead who is also a release manager holds every RM permission, and still may not hand it out.
    lead_and_rm = _user(UserRole.QA_LEAD, additional=["RELEASE_MANAGER"])
    with pytest.raises(HTTPException) as exc:
        _enforce_grant_ceiling(lead_and_rm, UserRole.RELEASE_MANAGER, uuid.uuid4())
    assert "Only an administrator" in exc.value.detail


def test_admin_grants_every_role_except_agent():
    for role in UserRole:
        if role is not UserRole.AGENT:
            _enforce_grant_ceiling(_user(UserRole.ADMIN), role, uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        _enforce_grant_ceiling(_user(UserRole.ADMIN), UserRole.AGENT, uuid.uuid4())
    assert exc.value.status_code == 400 and "agent registration" in exc.value.detail


def test_nobody_changes_their_own_project_role():
    lead = _user(UserRole.QA_LEAD)
    with pytest.raises(HTTPException):
        _enforce_grant_ceiling(lead, UserRole.DEVELOPER, lead.id)


# -- MFA ---------------------------------------------------------------------

LEAD_UP = MfaPolicy(require_mfa=True, required_for_role="QA_LEAD")


@pytest.mark.parametrize("role,required", [
    ("RELEASE_MANAGER", True), ("PRODUCT_MANAGER", True),
    ("PROJECT_MANAGER", False), ("DEVELOPER", False), ("AGENT", False),
    ("QA_LEAD", True), ("ADMIN", True), ("QA_ENGINEER", False), ("VIEWER", False),
])
def test_mfa_from_qa_lead_up_covers_the_release_deciders(role, required):
    assert policy_requires_mfa(_user(role), LEAD_UP) is required


def test_an_extra_release_role_brings_the_mfa_requirement():
    assert policy_requires_mfa(_user("QA_ENGINEER", additional=["PRODUCT_MANAGER"]), LEAD_UP) is True
    assert policy_requires_mfa(_user("QA_ENGINEER", additional=["DEVELOPER"]), LEAD_UP) is False


def test_a_viewer_floor_still_means_everyone():
    everyone = MfaPolicy(require_mfa=True, required_for_role="VIEWER")
    for role in UserRole:
        assert policy_requires_mfa(_user(role.value), everyone) is True, role


def test_an_unknown_role_is_still_in_scope():
    assert policy_requires_mfa(_user("SUPERUSER"), LEAD_UP) is True
