"""A permission guard admits job roles, and a write still needs it in THAT project.

Enterprise roles E00-S03. ``require_permission(P)`` is a ``require_role`` guard
whose floor comes from the catalogue: the five ladder roles are answered by rank
as before, and the job roles that hold ``P`` are admitted too. The 2026-11-18
rule carries over (owner decision D-2): on a write, the caller's instance
permissions and their permissions in the project INTERSECT, so a person's
global role is their job and their project role caps it. Extra job roles
(``additional_roles``, D-16) add permissions without adding rank.

Like ``test_project_writes_need_the_role_in_that_project.py``, this drives a
small app built from the real dependencies against a fake session.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import deps
from app.core.deps import (
    get_accessible_project_ids,
    get_current_user_or_api_key,
    require_permission,
    require_project_access,
    require_project_role,
    require_release_access,
    require_role,
)
from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.postgres import UserRole

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
RELEASE = uuid.UUID("44444444-4444-4444-8444-444444444444")

Membership = tuple[str, list[str] | None]


class _Result:
    def __init__(self, scalar=None, rows=None):
        self._scalar, self._rows = scalar, rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Answers the guards' membership lookups from ``project id -> (role, additional_roles)``."""

    def __init__(self, memberships: dict[uuid.UUID, Membership]):
        self.memberships = memberships

    async def execute(self, stmt):
        compiled = stmt.compile()
        sql, params = str(compiled), compiled.params
        selected = sql.split("FROM", 1)[0]

        def bound(prefix):
            return next((v for k, v in params.items() if k.startswith(prefix)), None)

        if "FROM releases" in sql:
            return _Result(scalar=PROJECT)
        if "FROM project_members" not in sql:
            raise AssertionError(f"unexpected statement: {sql}")
        wants_role = "project_members.role" in selected
        wants_extra = "project_members.additional_roles" in selected
        wants_id = "project_members.id" in selected
        project = bound("project_id")
        if project is None:  # every project of the caller
            if wants_extra:
                return _Result(rows=[(pid, role, extra) for pid, (role, extra) in self.memberships.items()])
            wanted = bound("role")
            return _Result(rows=[(pid,) for pid, (role, _) in self.memberships.items()
                                 if wanted is None or role in wanted])
        member = self.memberships.get(project)
        if member is None:
            return _Result()
        role, extra = member
        if wants_role and wants_id:  # the whole row
            return _Result(scalar=SimpleNamespace(role=role, additional_roles=extra))
        if wants_role and wants_extra:
            return _Result(rows=[(role, extra)])
        return _Result(scalar=role if wants_role else uuid.uuid4())


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/projects/{project_id}/releases")
    async def write_release(
        _access=Depends(require_project_access()),
        _user=Depends(require_permission(Permission.RELEASE_WRITE)),
    ):
        return {"ok": True}

    @app.get("/projects/{project_id}/releases")
    async def read_release(
        _access=Depends(require_project_access()),
        _user=Depends(require_permission(Permission.RELEASE_WRITE)),
    ):
        return {"ok": True}

    @app.post("/releases/{release_id}/gate")
    async def evaluate_gate(
        _access=Depends(require_release_access()),
        _user=Depends(require_permission(Permission.RELEASE_GATE_EVALUATE)),
    ):
        return {"ok": True}

    @app.put("/projects/{project_id}/release-settings")
    async def project_role_write(
        _user=Depends(require_project_role(UserRole.QA_LEAD, permission=Permission.RELEASE_WRITE)),
    ):
        return {"ok": True}

    @app.post("/projects/{project_id}/triage")
    async def triage(
        _access=Depends(require_project_access()),
        _user=Depends(require_permission(Permission.FAILURE_TRIAGE_ASSIGNED)),
    ):
        return {"ok": True}

    @app.post("/projects/{project_id}/triage-owned")
    async def triage_owned(
        _access=Depends(require_project_access()),
        _user=Depends(require_permission(Permission.FAILURE_TRIAGE_ASSIGNED, conditions_checked_in_handler=True)),
    ):
        return {"ok": True}

    @app.post("/projects/{project_id}/two-guards")
    async def two_guards(
        _access=Depends(require_project_access()),
        _engineer=Depends(require_role(UserRole.QA_ENGINEER)),
        _release=Depends(require_permission(Permission.RELEASE_WRITE)),
    ):
        return {"ok": True}

    @app.post("/sweep")
    async def sweep(db=Depends(get_db), user=Depends(require_permission(Permission.RELEASE_WRITE))):
        ids = await get_accessible_project_ids(db, user)
        return {"projects": sorted(str(p) for p in ids) if ids is not None else None}

    return app


def _client(role: str, memberships: dict[uuid.UUID, Membership], additional: list[str] | None = None) -> TestClient:
    app = _app()
    user = SimpleNamespace(
        id=uuid.uuid4(), role=role, additional_roles=additional, is_active=True,
        must_change_password=False, username="u",
    )
    db = _Db(memberships)

    async def _user():
        return user

    async def _get_db():
        yield db

    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_membership_cache(monkeypatch):
    import app.db.redis_client as redis_client

    def _down():
        raise RuntimeError("no redis in this test")

    monkeypatch.setattr(redis_client, "get_redis", _down)
    monkeypatch.setattr(deps, "_count_auth_failure", lambda reason: None)


def _post(client, path):
    return client.post(path)


WRITE = f"/projects/{PROJECT}/releases"


# -- ladder roles: answered by rank, as before ----------------------------------

@pytest.mark.parametrize("role,status", [("ADMIN", 200), ("QA_LEAD", 200), ("QA_ENGINEER", 403), ("VIEWER", 403)])
def test_ladder_roles_are_answered_by_rank(role, status):
    response = _post(_client(role, {PROJECT: (role, None)}), WRITE)
    assert response.status_code == status, response.text


def test_a_refusal_names_the_permission_and_who_holds_it():
    response = _post(_client("QA_ENGINEER", {PROJECT: ("QA_ENGINEER", None)}), WRITE)
    detail = response.json()["detail"]
    assert "'release.write' permission" in detail
    assert "RELEASE_MANAGER" in detail and "QA_LEAD" in detail


def test_a_lead_capped_to_viewer_in_the_project_still_cannot_write():
    response = _post(_client("QA_LEAD", {PROJECT: ("VIEWER", None)}), WRITE)
    assert response.status_code == 403, response.text


# -- job roles: admitted by permission, capped per project (D-2) -----------------

def test_a_release_manager_writes_releases_in_their_project():
    assert _post(_client("RELEASE_MANAGER", {PROJECT: ("RELEASE_MANAGER", None)}), WRITE).status_code == 200


def test_the_project_role_caps_the_job_role():
    response = _post(_client("RELEASE_MANAGER", {PROJECT: ("VIEWER", None)}), WRITE)
    assert response.status_code == 403
    assert "in this project" in response.json()["detail"]


def test_a_project_role_never_raises_the_instance_role():
    response = _post(_client("VIEWER", {PROJECT: ("RELEASE_MANAGER", None)}), WRITE)
    assert response.status_code == 403


def test_a_lead_whose_project_role_is_release_manager_writes_releases():
    # QA_LEAD ∩ RELEASE_MANAGER still holds release.write
    assert _post(_client("QA_LEAD", {PROJECT: ("RELEASE_MANAGER", None)}), WRITE).status_code == 200


def test_reads_check_the_instance_role_like_any_require_role_guard():
    assert _client("RELEASE_MANAGER", {PROJECT: ("RELEASE_MANAGER", None)}).get(WRITE).status_code == 200
    assert _client("VIEWER", {PROJECT: ("RELEASE_MANAGER", None)}).get(WRITE).status_code == 403


def test_release_access_guard_holds_the_member_to_the_permission():
    path = f"/releases/{RELEASE}/gate"
    assert _post(_client("RELEASE_MANAGER", {PROJECT: ("RELEASE_MANAGER", None)}), path).status_code == 200
    assert _post(_client("RELEASE_MANAGER", {PROJECT: ("DEVELOPER", None)}), path).status_code == 403


def test_require_project_role_takes_a_permission_too():
    path = f"/projects/{PROJECT}/release-settings"
    assert _client("RELEASE_MANAGER", {PROJECT: ("RELEASE_MANAGER", None)}).put(path).status_code == 200
    assert _client("RELEASE_MANAGER", {PROJECT: ("VIEWER", None)}).put(path).status_code == 403
    assert _client("ADMIN", {PROJECT: ("VIEWER", None)}).put(path).status_code == 200
    assert _client("QA_ENGINEER", {PROJECT: ("QA_ENGINEER", None)}).put(path).status_code == 403


# -- extra job roles (D-16) ------------------------------------------------------

def test_an_extra_job_role_adds_the_permission():
    client = _client("QA_ENGINEER", {PROJECT: ("QA_ENGINEER", ["RELEASE_MANAGER"])}, additional=["RELEASE_MANAGER"])
    assert _post(client, WRITE).status_code == 200


def test_an_extra_role_on_one_side_only_is_capped():
    instance_only = _client("QA_ENGINEER", {PROJECT: ("QA_ENGINEER", None)}, additional=["RELEASE_MANAGER"])
    assert _post(instance_only, WRITE).status_code == 403
    project_only = _client("QA_ENGINEER", {PROJECT: ("QA_ENGINEER", ["RELEASE_MANAGER"])})
    assert _post(project_only, WRITE).status_code == 403


def test_a_stored_extra_role_that_is_a_rank_is_ignored():
    client = _client("VIEWER", {PROJECT: ("VIEWER", ["ADMIN"])}, additional=["ADMIN", "QA_LEAD"])
    assert _post(client, WRITE).status_code == 403


# -- conditional grants ----------------------------------------------------------

def test_a_conditional_grant_counts_only_where_the_handler_checks_it():
    member = {PROJECT: ("DEVELOPER", None)}
    assert _post(_client("DEVELOPER", member), f"/projects/{PROJECT}/triage").status_code == 403
    assert _post(_client("DEVELOPER", member), f"/projects/{PROJECT}/triage-owned").status_code == 200


def test_qa_engineers_triage_unconditionally():
    member = {PROJECT: ("QA_ENGINEER", None)}
    assert _post(_client("QA_ENGINEER", member), f"/projects/{PROJECT}/triage").status_code == 200


# -- several guards, several projects, agents --------------------------------------

def test_every_guard_on_a_route_must_pass():
    path = f"/projects/{PROJECT}/two-guards"
    rm = _post(_client("RELEASE_MANAGER", {PROJECT: ("RELEASE_MANAGER", None)}), path)
    assert rm.status_code == 403 and "QA_ENGINEER" in rm.json()["detail"]
    assert _post(_client("QA_LEAD", {PROJECT: ("QA_LEAD", None)}), path).status_code == 200


def test_a_write_across_projects_reaches_only_projects_where_the_permission_holds():
    client = _client("RELEASE_MANAGER", {PROJECT: ("RELEASE_MANAGER", None), OTHER: ("VIEWER", None)})
    assert client.post("/sweep").json() == {"projects": [str(PROJECT)]}


def test_ladder_writes_across_projects_are_unchanged():
    client = _client("QA_LEAD", {PROJECT: ("QA_LEAD", None), OTHER: ("VIEWER", None)})
    assert client.post("/sweep").json() == {"projects": [str(PROJECT)]}


def test_an_agent_cannot_write_releases():
    assert _post(_client("AGENT", {PROJECT: ("AGENT", None)}), WRITE).status_code == 403


# -- the guard factories refuse a drifting floor -----------------------------------

def test_require_role_refuses_a_permission_from_another_rank():
    with pytest.raises(ValueError, match="require_permission"):
        require_role(UserRole.QA_ENGINEER, permission=Permission.RELEASE_WRITE)


def test_require_role_takes_ranks_only():
    with pytest.raises(ValueError):
        require_role(UserRole.RELEASE_MANAGER)
    with pytest.raises(ValueError):
        require_project_role(UserRole.DEVELOPER)


def test_require_permission_is_a_require_role_guard_with_the_catalogue_floor():
    guard = require_permission(Permission.RELEASE_WRITE)
    assert guard.__qualname__.startswith("require_role.")
    assert getattr(guard, deps._MIN_ROLE_ATTR) is UserRole.QA_LEAD
    assert getattr(guard, deps._PERMISSION_ATTR) is Permission.RELEASE_WRITE
