"""``GET /api/v1/me/permissions`` (enterprise roles E00-S04).

Reads use the project roles; writes the intersection of instance and project roles; no membership means the
instance roles; a project the caller cannot read is refused by ``resolve_project_scope`` (403). The answer for
a ladder role is exactly "every permission at or below its rank", so the SPA's ``can()`` agrees with the
guards the routes already run.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.permissions import Permission, rank_index
from app.models.postgres import UserRole
from app.routers import me_permissions


def _user(role, additional=None):
    return SimpleNamespace(id=uuid.uuid4(), role=role, additional_roles=additional)


def _db(row=None):
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = row
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def scope(monkeypatch):
    calls = []

    async def _resolve(db, user, requested):
        calls.append(requested)
        if requested == "forbidden":
            raise HTTPException(status_code=403, detail="Access denied to this project")
        return uuid.UUID(requested), None

    monkeypatch.setattr(me_permissions, "resolve_project_scope", _resolve)
    return calls


async def _ask(user, project_id=None, row=None):
    return await me_permissions.get_my_permissions(project_id=project_id, db=_db(row), current_user=user)


def _ladder_set(role):
    return sorted(p.value for p in Permission if rank_index(role) >= rank_index(p.legacy_min_rank))


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.QA_LEAD,
                                  UserRole.ADMIN])
async def test_a_ladder_role_holds_every_permission_at_or_below_its_rank(role):
    out = await _ask(_user(role))
    assert out.role == role and out.additional_roles == []
    assert out.read_permissions == out.write_permissions == _ladder_set(role)
    assert out.project_id is None and out.project_role is None


async def test_a_job_role_holds_its_explicit_set_and_conditions_are_named():
    out = await _ask(_user(UserRole.DEVELOPER))
    assert "run.ingest" in out.write_permissions and "release.write" not in out.write_permissions
    assert out.conditional["failure.triage.assigned"] == ["owns_failure"]


async def test_an_extra_role_adds_its_permissions():
    plain = await _ask(_user(UserRole.QA_ENGINEER))
    with_rm = await _ask(_user(UserRole.QA_ENGINEER, ["RELEASE_MANAGER"]))
    assert "gate_policy.write" not in plain.write_permissions
    assert "gate_policy.write" in with_rm.write_permissions
    assert with_rm.additional_roles == ["RELEASE_MANAGER"]


async def test_in_a_project_reads_follow_the_project_role_and_writes_the_lower_of_the_two(scope):
    pid = str(uuid.uuid4())
    # Global QA_LEAD, capped to VIEWER in this project.
    out = await _ask(_user(UserRole.QA_LEAD), pid, row=("VIEWER", None))
    assert out.project_role == UserRole.VIEWER and str(out.project_id) == pid
    assert out.read_permissions == out.write_permissions == _ladder_set(UserRole.VIEWER)
    # Global VIEWER who is RELEASE_MANAGER in the project: may read like one, never write like one (D-2).
    out = await _ask(_user(UserRole.VIEWER), pid, row=("RELEASE_MANAGER", None))
    assert "release.write" in out.read_permissions
    assert "release.write" not in out.write_permissions


async def test_project_extra_roles_count_on_both_sides(scope):
    pid = str(uuid.uuid4())
    caller = _user(UserRole.QA_ENGINEER, ["RELEASE_MANAGER"])
    capped = await _ask(caller, pid, row=("QA_ENGINEER", None))
    held = await _ask(caller, pid, row=("QA_ENGINEER", ["RELEASE_MANAGER"]))
    assert "gate_policy.write" not in capped.write_permissions
    assert "gate_policy.write" in held.write_permissions
    assert held.project_additional_roles == ["RELEASE_MANAGER"]


async def test_no_membership_answers_with_the_instance_roles(scope):
    out = await _ask(_user(UserRole.ADMIN), str(uuid.uuid4()), row=None)
    assert out.project_role is None and out.write_permissions == _ladder_set(UserRole.ADMIN)


async def test_a_foreign_project_is_refused(scope):
    with pytest.raises(HTTPException) as exc:
        await _ask(_user(UserRole.QA_LEAD), "forbidden")
    assert exc.value.status_code == 403


def test_the_route_is_a_plain_signed_in_read():
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/v1/me/permissions")
    assert route.methods == {"GET"}
    deps = [d.call for d in route.dependant.dependencies]
    assert me_permissions.get_current_active_user in deps
