import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.deps import (
    CREDENTIAL_KIND_API_KEY,
    PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
    _bind_api_key_project,
    _bind_credential_kind,
    get_accessible_project_ids,
    require_instance_admin,
    require_project_access,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import UserRole


class _NoDb:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("project-bound API key should not need a DB membership lookup")


def _user(role=UserRole.ADMIN):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)


@pytest.mark.asyncio
async def test_project_bound_key_scopes_admin_access():
    project_id = uuid.uuid4()
    user = _bind_api_key_project(_user(UserRole.ADMIN), project_id)

    assert await get_accessible_project_ids(_NoDb(), user) == {project_id}
    assert await resolve_project_scope(_NoDb(), user, None) == (None, {project_id})
    assert await resolve_project_scope(_NoDb(), user, str(project_id)) == (project_id, None)


@pytest.mark.asyncio
async def test_project_bound_key_rejects_other_project_path():
    bound_project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    user = _bind_api_key_project(_user(UserRole.ADMIN), bound_project_id)
    request = SimpleNamespace(path_params={"project_id": str(other_project_id)})
    guard = require_project_access()

    with pytest.raises(HTTPException) as exc:
        await guard(request, db=_NoDb(), current_user=user)

    assert exc.value.status_code == 403


# ── N20: require_role(UserRole.ADMIN) is closed to a project-bound key ───────
#
# Only an ADMIN can bind a key to a project, so a bound key is usually an
# ADMIN's CI credential, and require_role compares its OWNER's role. Before
# N20 a key leaked from one team's pipeline could POST /api/v1/users with
# "role": "ADMIN" and log in as an instance administrator.

_BELOW_ADMIN = [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.QA_LEAD]


def _bound_key_user(role=UserRole.ADMIN, project_id=None):
    """What ``_validate_api_key`` hands every dependency for a project-scoped key."""
    user = _bind_api_key_project(_user(role), project_id or uuid.uuid4())
    return _bind_credential_kind(user, CREDENTIAL_KIND_API_KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.ADMIN, "ADMIN"], ids=["enum", "stored-string"])
async def test_the_admin_role_refuses_a_project_bound_key(role):
    guard = require_role(UserRole.ADMIN)

    with pytest.raises(HTTPException) as exc:
        await guard(current_user=_bound_key_user(role))

    assert exc.value.status_code == 403
    assert exc.value.detail == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL


@pytest.mark.asyncio
async def test_a_route_that_opts_in_admits_the_same_key():
    user = _bound_key_user()
    guard = require_role(UserRole.ADMIN, allow_project_key=True)

    assert await guard(current_user=user) is user


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_project_key", [False, True])
async def test_an_unbound_admin_passes_either_way(allow_project_key):
    """The refusal keys on the BINDING, not on "came in through an API key".

    A user-scoped key (an API key with no project) is as unbound as a JWT; a
    fix that refused every API key would lock out its legitimate automation.
    """
    guard = require_role(UserRole.ADMIN, allow_project_key=allow_project_key)
    jwt_user = _bind_api_key_project(_user(), None)  # what get_current_user binds
    user_scoped_key = _bind_credential_kind(
        _bind_api_key_project(_user(), None), CREDENTIAL_KIND_API_KEY
    )
    never_bound = _user()  # a row no auth dependency has touched

    for user in (jwt_user, user_scoped_key, never_bound):
        assert await guard(current_user=user) is user


@pytest.mark.asyncio
@pytest.mark.parametrize("role", _BELOW_ADMIN)
async def test_below_admin_a_bound_key_is_judged_by_its_role_alone(role):
    user = _bound_key_user(role)

    assert await require_role(role)(current_user=user) is user
    with pytest.raises(HTTPException) as exc:
        await require_role(UserRole.ADMIN)(current_user=user)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Requires at least ADMIN role"


@pytest.mark.asyncio
@pytest.mark.parametrize("min_role", _BELOW_ADMIN)
async def test_a_bound_admin_key_still_passes_every_lower_role(min_role):
    user = _bound_key_user()

    assert await require_role(min_role)(current_user=user) is user


@pytest.mark.parametrize("min_role", _BELOW_ADMIN)
def test_opting_in_below_admin_is_refused_when_the_route_is_built(min_role):
    """The flag means nothing below ADMIN; a route passing it has misread it."""
    with pytest.raises(ValueError, match="allow_project_key"):
        require_role(min_role, allow_project_key=True)


@pytest.mark.asyncio
async def test_require_instance_admin_still_refuses_a_bound_key():
    guard = require_instance_admin()

    with pytest.raises(HTTPException) as exc:
        await guard(current_user=_bound_key_user())
    assert exc.value.status_code == 403
    assert exc.value.detail == PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL

    unbound = _bind_api_key_project(_user(), None)
    assert await guard(current_user=unbound) is unbound
