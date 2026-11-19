"""GAP-B05: ``require_project_role`` decides by the project membership, and is never alone.

``app/core/deps.py`` ``require_project_role(min_role)`` reads the caller's
``project_members`` row for the path's ``project_id``; that membership role
decides. With no membership it falls back to the caller's GLOBAL role (its
docstring says so), which is safe only because every route that uses it also
runs ``require_project_access()``, which refuses a non-member. Nothing pinned
either half: no behavioural test of the two branches (a QA_LEAD of project A
administering project B happened, ``test_reviewer_quality_router.py:84``), and
no ratchet that the 12 uses keep their ``require_project_access`` partner.

On a WRITE the lower of the two roles decides (security audit 2026-11-18,
``tests/regression/test_project_writes_need_the_role_in_that_project.py``): a
project QA lead whose instance role is lower does not write as a lead, and an
instance ADMIN is never held to a membership role. On a read the membership
role decides, as before.

The dependency is called directly, with a database fake that answers the
membership lookup from the ids bound into the real statement, so a lookup
that loses its project (or user) filter answers wrongly here. The ratchet
walks every route's dependency tree.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.core.deps import require_project_role
from app.models.postgres import ProjectMember, User, UserRole

pytestmark = pytest.mark.asyncio

PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()


class _MembershipDb:
    """The one query ``require_project_role`` makes, answered from ``(user_id, project_id) -> role``."""

    def __init__(self, memberships: dict[tuple[uuid.UUID, uuid.UUID], UserRole]):
        self.memberships = memberships
        self.lookups: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def execute(self, statement):
        params = statement.compile().params
        user_ids = [v for k, v in params.items() if k.startswith("user_id")]
        project_ids = [v for k, v in params.items() if k.startswith("project_id")]
        assert len(user_ids) == 1 and len(project_ids) == 1, (
            f"the membership lookup must filter on exactly one user and one project: {params}"
        )
        key = (user_ids[0], project_ids[0])
        self.lookups.append(key)
        role = self.memberships.get(key)
        row = (
            None
            if role is None
            else ProjectMember(user_id=key[0], project_id=key[1], role=role.value)
        )
        return SimpleNamespace(scalar_one_or_none=lambda: row)


def _user(global_role: UserRole) -> User:
    uid = uuid.uuid4()
    return User(
        id=uid,
        username=f"pr-{uid.hex[:8]}",
        email=f"pr-{uid.hex[:8]}@example.test",
        role=global_role.value,
        is_active=True,
        hashed_password="!unusable",
    )


def _request(project_id: uuid.UUID, method: str = "PUT") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": f"/api/v1/projects/{project_id}/workflows",
            "path_params": {"project_id": str(project_id)},
            "headers": [],
            "query_string": b"",
        }
    )


async def _check(min_role: UserRole, user: User, memberships: dict, project=PROJECT, method: str = "PUT"):
    db = _MembershipDb(memberships)
    result = await require_project_role(min_role)(
        request=_request(project, method), db=db, current_user=user
    )
    return result, db


# ── the membership decides ──────────────────────────────────────────────────


async def test_a_viewer_member_is_refused_even_with_a_global_qa_lead_role():
    user = _user(UserRole.QA_LEAD)

    with pytest.raises(HTTPException) as refused:
        await _check(UserRole.QA_LEAD, user, {(user.id, PROJECT): UserRole.VIEWER})

    assert refused.value.status_code == 403
    assert refused.value.detail == "Requires at least QA_LEAD role for this project"


async def test_a_qa_lead_member_reads_even_with_a_global_viewer_role():
    user = _user(UserRole.VIEWER)

    result, db = await _check(
        UserRole.QA_LEAD, user, {(user.id, PROJECT): UserRole.QA_LEAD}, method="GET"
    )

    assert result is user
    assert db.lookups == [(user.id, PROJECT)]


async def test_a_qa_lead_member_with_a_global_viewer_role_does_not_write_as_a_lead():
    """On a write the lower of the two roles decides."""
    user = _user(UserRole.VIEWER)

    with pytest.raises(HTTPException) as refused:
        await _check(UserRole.QA_LEAD, user, {(user.id, PROJECT): UserRole.QA_LEAD})

    assert refused.value.status_code == 403
    assert refused.value.detail == "Requires at least QA_LEAD role for this project"


async def test_an_instance_admin_is_not_held_to_a_membership_role():
    user = _user(UserRole.ADMIN)

    result, _db = await _check(UserRole.QA_LEAD, user, {(user.id, PROJECT): UserRole.VIEWER})

    assert result is user


async def test_a_membership_in_another_project_does_not_count():
    user = _user(UserRole.VIEWER)

    with pytest.raises(HTTPException) as refused:
        await _check(
            UserRole.QA_LEAD, user, {(user.id, OTHER_PROJECT): UserRole.QA_LEAD}
        )

    assert refused.value.status_code == 403


@pytest.mark.parametrize(
    "membership, allowed",
    [
        (UserRole.TESTER, False),
        (UserRole.QA_ENGINEER, True),
        (UserRole.QA_LEAD, True),
        (UserRole.ADMIN, True),
    ],
)
@pytest.mark.parametrize("global_role, method", [(UserRole.QA_LEAD, "PUT"), (UserRole.VIEWER, "GET")])
async def test_the_membership_role_is_compared_against_the_floor(membership, allowed, global_role, method):
    user = _user(global_role)
    memberships = {(user.id, PROJECT): membership}

    if allowed:
        result, _db = await _check(UserRole.QA_ENGINEER, user, memberships, method=method)
        assert result is user
    else:
        with pytest.raises(HTTPException) as refused:
            await _check(UserRole.QA_ENGINEER, user, memberships, method=method)
        assert refused.value.status_code == 403


# ── no membership: the global role (documented), made safe by the pairing ───


async def test_without_a_membership_the_global_role_decides():
    lead, viewer = _user(UserRole.QA_LEAD), _user(UserRole.VIEWER)

    result, db = await _check(UserRole.QA_LEAD, lead, {})
    assert result is lead
    assert db.lookups == [(lead.id, PROJECT)]

    with pytest.raises(HTTPException) as refused:
        await _check(UserRole.QA_LEAD, viewer, {})
    assert refused.value.status_code == 403


# ── the ratchet: never without require_project_access on the same id ────────


def _calls(dependant, seen=None):
    """Every dependency callable in a route's tree, depth first."""
    seen = set() if seen is None else seen
    for sub in dependant.dependencies:
        if sub.call is not None and id(sub.call) not in seen:
            seen.add(id(sub.call))
            yield sub.call
        yield from _calls(sub, seen)


def _closure(call) -> dict:
    try:
        return dict(inspect.getclosurevars(call).nonlocals)
    except TypeError:
        return {}


def _is(call, factory: str) -> bool:
    return getattr(call, "__module__", "") == "app.core.deps" and getattr(
        call, "__qualname__", ""
    ).startswith(f"{factory}.<locals>.")


def _project_role_routes():
    from app.main import app

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = list(_calls(route.dependant))
        if any(_is(c, "require_project_role") for c in calls):
            yield route, calls


def test_every_project_role_gate_is_paired_with_project_access_on_the_path_id():
    found, unpaired = [], []
    for route, calls in _project_role_routes():
        label = f"{','.join(sorted(route.methods))} {route.path}"
        found.append(label)
        access_params = {
            _closure(c).get("project_id_param")
            for c in calls
            if _is(c, "require_project_access")
        }
        # require_project_role reads the ``project_id`` PATH parameter; without
        # it the gate silently falls back to the global role.
        if "{project_id}" not in route.path or "project_id" not in access_params:
            unpaired.append(
                f"{label} (require_project_access params: {access_params or 'none'})"
            )

    # Walker sanity: a walk that found nothing would pass the check below.
    assert "PUT /api/v1/projects/{project_id}/fixer/config" in found, found
    assert not unpaired, (
        "require_project_role falls back to the caller's GLOBAL role for a non-member; "
        "pair it with require_project_access() on {project_id}:\n  "
        + "\n  ".join(unpaired)
    )
