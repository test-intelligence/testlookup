"""The permission catalogue (enterprise roles E00-S01/S02): ranks, roles, grants.

The properties every later epic leans on:

* the five ladder roles' permission sets are exactly "rank >= the permission's
  legacy rank", so moving a route to ``require_permission`` changes nothing for
  them;
* a job role or AGENT counts as VIEWER wherever no permission is named;
* on a project write the instance and project grants intersect, which for two
  ladder roles is "the lower of the two decides" (security audit 2026-11-18);
* an AGENT never holds a forbidden permission, whatever it is granted;
* a person can only grant a role whose permissions they hold.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import permissions as perms
from app.core.permissions import (
    AGENT_FORBIDDEN,
    JOB_ROLES,
    LADDER,
    PERMISSION_SPECS,
    ROLE_PERMISSIONS,
    AgentCeiling,
    Permission,
    agent_grants,
    assigned_roles,
    can_grant,
    effective_grants,
    granted,
    has_permission,
    rank_index,
    user_roles,
)
from app.models.postgres import UserRole

NEW_ROLES = [r for r in UserRole if r not in LADDER]


# ── ranks ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", list(UserRole))
def test_rank_index_never_raises_for_a_role(role):
    assert 0 <= rank_index(role) < len(LADDER)


@pytest.mark.parametrize("role", NEW_ROLES)
def test_new_roles_rank_as_viewer(role):
    assert rank_index(role) == rank_index(UserRole.VIEWER) == 0
    assert rank_index(role.value) == 0


def test_rank_index_refuses_a_string_that_is_not_a_role():
    with pytest.raises(ValueError):
        rank_index("SUPERUSER")


def test_rank_index_accepts_the_legacy_prefix():
    assert rank_index("UserRole.QA_LEAD") == rank_index(UserRole.QA_LEAD)


# ── the catalogue ────────────────────────────────────────────────────────────

def test_every_permission_has_a_spec_with_a_ladder_rank():
    assert set(PERMISSION_SPECS) == set(Permission)
    for permission, spec in PERMISSION_SPECS.items():
        assert spec.legacy_min_rank in LADDER, permission
        assert spec.description, permission
        assert permission.value == permission.value.lower(), permission


@pytest.mark.parametrize("role", LADDER)
@pytest.mark.parametrize("permission", list(Permission))
def test_ladder_roles_hold_exactly_what_their_rank_reaches(role, permission):
    expected = rank_index(role) >= rank_index(permission.legacy_min_rank)
    assert has_permission([role], permission) is expected


def test_ladder_permission_sets_are_nested():
    for lower, higher in zip(LADDER, LADDER[1:]):
        assert ROLE_PERMISSIONS[lower] <= ROLE_PERMISSIONS[higher]


def test_admin_holds_every_permission():
    assert ROLE_PERMISSIONS[UserRole.ADMIN] == frozenset(Permission)


@pytest.mark.parametrize("role", NEW_ROLES)
def test_every_role_has_a_permission_set_that_includes_reading(role):
    assert Permission.PROJECT_READ in ROLE_PERMISSIONS[role]
    assert Permission.REQUIREMENT_READ in ROLE_PERMISSIONS[role]


@pytest.mark.parametrize("role", list(JOB_ROLES))
def test_job_roles_never_hold_instance_or_member_administration(role):
    for permission in (Permission.INSTANCE_ADMIN, Permission.MEMBER_MANAGE, Permission.AGENT_MANAGE,
                       Permission.RUN_DELETE, Permission.RELEASE_DELETE, Permission.GATE_POLICY_PUBLISH):
        assert permission not in ROLE_PERMISSIONS[role], (role, permission)


def test_release_manager_decides_releases_and_developer_does_not():
    assert has_permission([UserRole.RELEASE_MANAGER], Permission.RELEASE_DECIDE)
    assert not has_permission([UserRole.DEVELOPER], Permission.RELEASE_DECIDE)
    assert not has_permission([UserRole.PROJECT_MANAGER], Permission.RELEASE_DECIDE)
    assert has_permission([UserRole.PRODUCT_MANAGER], Permission.ACCEPTANCE_SIGNOFF)


def test_conditional_grants_count_only_when_the_caller_checks_the_condition():
    roles = [UserRole.DEVELOPER]
    assert not has_permission(roles, Permission.FAILURE_TRIAGE_ASSIGNED)
    assert has_permission(roles, Permission.FAILURE_TRIAGE_ASSIGNED, allow_conditional=True)


def test_an_unconditional_grant_from_another_role_wins():
    roles = [UserRole.QA_ENGINEER, UserRole.DEVELOPER]  # QA_ENGINEER triages assigned failures outright
    assert has_permission(roles, Permission.FAILURE_TRIAGE_ASSIGNED)


def test_an_unregistered_condition_is_never_satisfied():
    assert perms.check_condition(["no_such_condition"], object(), object()) is False
    assert perms.check_condition([], object(), object()) is True


# ── extra job roles ──────────────────────────────────────────────────────────

def test_assigned_roles_keeps_only_valid_job_roles():
    roles = assigned_roles("QA_ENGINEER", ["RELEASE_MANAGER", "ADMIN", "AGENT", "nonsense", "RELEASE_MANAGER"])
    assert roles == (UserRole.QA_ENGINEER, UserRole.RELEASE_MANAGER)


def test_an_agent_carries_no_extra_roles():
    assert assigned_roles("AGENT", ["RELEASE_MANAGER"]) == (UserRole.AGENT,)


def test_extra_roles_add_permissions_but_not_rank():
    user = SimpleNamespace(role="QA_ENGINEER", additional_roles=["RELEASE_MANAGER"])
    roles = user_roles(user)
    assert has_permission(roles, Permission.RELEASE_DECIDE)
    assert rank_index(user.role) == rank_index(UserRole.QA_ENGINEER)


def test_a_user_without_the_column_has_only_the_primary_role():
    assert user_roles(SimpleNamespace(role="VIEWER")) == (UserRole.VIEWER,)


# ── project writes: the intersection ─────────────────────────────────────────

@pytest.mark.parametrize("instance", LADDER)
@pytest.mark.parametrize("project", LADDER)
def test_for_ladder_roles_the_intersection_is_the_lower_role(instance, project):
    lower = instance if rank_index(instance) <= rank_index(project) else project
    grants = effective_grants([instance], [project], is_write=True)
    assert set(grants) == set(ROLE_PERMISSIONS[lower])


def test_a_project_role_caps_the_job_role_on_writes_d2():
    grants = effective_grants([UserRole.RELEASE_MANAGER], [UserRole.VIEWER], is_write=True)
    assert not granted(grants, Permission.RELEASE_WRITE)
    grants = effective_grants([UserRole.VIEWER], [UserRole.RELEASE_MANAGER], is_write=True)
    assert not granted(grants, Permission.RELEASE_WRITE)
    grants = effective_grants([UserRole.RELEASE_MANAGER], [UserRole.RELEASE_MANAGER], is_write=True)
    assert granted(grants, Permission.RELEASE_WRITE)


def test_reads_use_the_project_roles_and_no_membership_uses_the_instance_roles():
    assert granted(effective_grants([UserRole.VIEWER], [UserRole.RELEASE_MANAGER], is_write=False),
                   Permission.AUDIT_READ_PROJECT)
    assert granted(effective_grants([UserRole.RELEASE_MANAGER], None, is_write=True), Permission.RELEASE_WRITE)


def test_a_conditional_grant_on_either_side_stays_conditional():
    grants = effective_grants([UserRole.QA_ENGINEER, UserRole.DEVELOPER], [UserRole.DEVELOPER], is_write=True)
    assert granted(grants, Permission.FAILURE_TRIAGE_ASSIGNED, allow_conditional=True)
    assert not granted(grants, Permission.FAILURE_TRIAGE_ASSIGNED)


# ── agents ───────────────────────────────────────────────────────────────────

def test_agent_forbidden_is_exactly_the_no_and_never_ceilings():
    expected = {p for p, s in PERMISSION_SPECS.items() if s.agent_ceiling in (AgentCeiling.NO, AgentCeiling.NEVER)}
    assert AGENT_FORBIDDEN == expected
    for permission in (Permission.RELEASE_SIGNOFF, Permission.RELEASE_DECIDE, Permission.ACCEPTANCE_SIGNOFF,
                       Permission.RISK_ACCEPT, Permission.AGENT_PROPOSAL_APPROVE, Permission.APIKEY_CREATE_OWN,
                       Permission.MEMBER_MANAGE, Permission.INSTANCE_ADMIN, Permission.RELEASE_DELETE):
        assert permission in AGENT_FORBIDDEN, permission


@pytest.mark.parametrize("permission", sorted(AGENT_FORBIDDEN, key=lambda p: p.value))
def test_an_agent_never_holds_a_forbidden_permission_even_when_granted(permission):
    grants = agent_grants([UserRole.AGENT], [permission])
    assert permission not in grants
    assert not has_permission([UserRole.AGENT], permission)


def test_an_agent_reads_by_default_and_holds_what_it_is_granted():
    assert has_permission([UserRole.AGENT], Permission.PROJECT_READ)
    assert not has_permission([UserRole.AGENT], Permission.ANALYSIS_TRIGGER)
    assert Permission.ANALYSIS_TRIGGER in agent_grants([UserRole.AGENT], [Permission.ANALYSIS_TRIGGER])


def test_the_agent_role_holds_nothing_forbidden():
    assert not (ROLE_PERMISSIONS[UserRole.AGENT] & AGENT_FORBIDDEN)


# ── granting roles ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("granter", LADDER)
@pytest.mark.parametrize("target", LADDER)
def test_for_ladder_roles_can_grant_is_not_above_your_own_rank(granter, target):
    assert can_grant([granter], target) is (rank_index(target) <= rank_index(granter))


def test_a_qa_lead_may_grant_developer_and_tester_but_not_the_manager_roles():
    assert can_grant([UserRole.QA_LEAD], UserRole.DEVELOPER)
    assert can_grant([UserRole.QA_LEAD], UserRole.TESTER)
    # D-17: the manager roles are given only by someone holding instance.admin, even where every permission of
    # the role is one the granter holds (PROJECT_MANAGER, PRODUCT_MANAGER are subsets of QA_LEAD).
    for role in (UserRole.RELEASE_MANAGER, UserRole.PROJECT_MANAGER, UserRole.PRODUCT_MANAGER):
        assert not can_grant([UserRole.QA_LEAD], role), role
        assert not can_grant([UserRole.QA_LEAD, role], role), role
    assert perms.ADMIN_ONLY_GRANTS == {UserRole.RELEASE_MANAGER, UserRole.PROJECT_MANAGER, UserRole.PRODUCT_MANAGER}
    assert perms.missing_for_grant([UserRole.QA_LEAD], UserRole.RELEASE_MANAGER) == ["gate_policy.write"]


def test_a_qa_engineer_cannot_grant_developer():
    assert not can_grant([UserRole.QA_ENGINEER], UserRole.DEVELOPER)  # quarantine.propose is a QA_LEAD permission


def test_nobody_grants_agent_here_and_admin_grants_everything_else():
    assert not can_grant([UserRole.ADMIN], UserRole.AGENT)
    for role in UserRole:
        if role is not UserRole.AGENT:
            assert can_grant([UserRole.ADMIN], role), role


def test_refusal_detail_names_the_permission_and_its_holders():
    detail = perms.permission_refusal_detail(Permission.RELEASE_WRITE, in_project=True)
    assert "'release.write' permission in this project" in detail
    assert "QA_LEAD" in detail and "ADMIN" in detail and "RELEASE_MANAGER" in detail
    assert "VIEWER" not in detail and "AGENT" not in detail


# -- extra job roles (E00-S05, D-16) -------------------------------------------------------------


def test_the_api_extra_role_enum_is_the_catalogues_job_roles():
    from typing import get_args

    from app.models.schemas import ExtraRole

    assert list(get_args(ExtraRole)) == [role.value for role in perms.EXTRA_ROLE_CHOICES]
    assert set(perms.EXTRA_ROLE_CHOICES) == perms.JOB_ROLES


def test_clean_additional_roles_orders_dedupes_and_drops_the_primary():
    clean = perms.clean_additional_roles
    assert clean(None) == [] and clean([]) == []
    assert clean(["DEVELOPER", "RELEASE_MANAGER", "DEVELOPER"]) == ["RELEASE_MANAGER", "DEVELOPER"]
    assert clean(["DEVELOPER", "RELEASE_MANAGER"], primary="DEVELOPER") == ["RELEASE_MANAGER"]
    assert clean("PRODUCT_MANAGER") == ["PRODUCT_MANAGER"]


@pytest.mark.parametrize("bad", ["AGENT", "QA_LEAD", "ADMIN", "nope"])
def test_clean_additional_roles_refuses_anything_but_a_job_role(bad):
    with pytest.raises(ValueError, match="cannot be an extra role"):
        perms.clean_additional_roles(["DEVELOPER", bad])


def test_stored_extra_roles_are_read_leniently():
    assert perms.job_roles_in(["DEVELOPER", "AGENT", "junk", "RELEASE_MANAGER"]) == (
        UserRole.RELEASE_MANAGER, UserRole.DEVELOPER,
    )
    assert perms.job_roles_in(None) == ()


def test_a_project_grant_set_caps_what_can_be_granted():
    lead = [UserRole.QA_LEAD]
    assert can_grant(lead, UserRole.DEVELOPER)
    capped = perms.effective_grants(lead, [UserRole.TESTER], is_write=True)
    assert not can_grant(lead, UserRole.DEVELOPER, grants=capped)
    assert "quarantine.propose" in perms.missing_for_grant(lead, UserRole.DEVELOPER, grants=capped)
