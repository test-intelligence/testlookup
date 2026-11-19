"""A write into a project needs the route's role in THAT project too.

A user has two roles: one on the instance (``users.role``) and one on each
project they belong to (``project_members.role``). A route that writes states
its minimum with ``require_role(X)``, which reads the instance role, and names
its project through a project guard, which used to check only that a
membership row exists. So a person who is a QA lead on the instance but was
added to one project as a viewer wrote to that project as a lead.

The rule now: on a write (any method but GET/HEAD/OPTIONS) the lower of the
two roles decides. The caller needs the route's ``require_role`` minimum on
the instance AND in the project the request names. An instance ADMIN passes.
Reads are unchanged, and a route with no ``require_role`` is unchanged.

The rule sits in the shared guards, not in each route, so the tests drive a
small app built from the real dependencies.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core import deps
from app.core.deps import (
    get_accessible_project_ids,
    get_current_user_or_api_key,
    require_project_access,
    require_project_role,
    require_release_access,
    require_role,
    require_run_access,
    resolve_authorized_test_case,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import UserRole

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
RELEASE = uuid.UUID("44444444-4444-4444-8444-444444444444")
CASE = uuid.UUID("55555555-5555-4555-8555-555555555555")

FOR_THIS_PROJECT = "role for this project"


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
    """Answers the handful of lookups the guards make, from two dicts."""

    def __init__(self, memberships: dict[uuid.UUID, str]):
        self.memberships = memberships  # project id -> the caller's role there

    async def execute(self, stmt):
        compiled = stmt.compile()
        sql, params = str(compiled), compiled.params
        selected = sql.split("FROM", 1)[0]

        def bound(prefix):
            return next((v for k, v in params.items() if k.startswith(prefix)), None)

        if "test_cases" in sql:
            case = SimpleNamespace(id=CASE, test_run_id=RUN)
            return _Result(rows=[(case, SimpleNamespace(id=RUN, project_id=PROJECT))])
        if "FROM test_runs" in sql:
            return _Result(scalar=PROJECT)
        if "FROM releases" in sql:
            return _Result(scalar=PROJECT)
        if "FROM project_members" in sql:
            project = bound("project_id")
            if project is None:  # every project of the caller, maybe by role
                wanted = bound("role")
                return _Result(rows=[
                    (pid,) for pid, role in self.memberships.items() if wanted is None or role in wanted
                ])
            role = self.memberships.get(project)
            if role is None:
                return _Result()
            wants_role, wants_id = "project_members.role" in selected, "project_members.id" in selected
            if wants_role and wants_id:  # the whole row
                return _Result(scalar=SimpleNamespace(role=role))
            return _Result(scalar=role if wants_role else uuid.uuid4())
        raise AssertionError(f"unexpected statement: {sql}")


class _Body(BaseModel):
    project_id: str | None = None


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/projects/{project_id}/settings")
    async def write_as_lead(
        _access=Depends(require_project_access()),
        _user=Depends(require_role(UserRole.QA_LEAD)),
    ):
        return {"ok": True}

    # The same two guards the other way round: the answer must not depend on
    # which dependency FastAPI happens to resolve first.
    @app.post("/projects/{project_id}/settings-role-first")
    async def write_as_lead_role_first(
        _user=Depends(require_role(UserRole.QA_LEAD)),
        _access=Depends(require_project_access()),
    ):
        return {"ok": True}

    @app.post("/projects/{project_id}/settings-decorator", dependencies=[Depends(require_role(UserRole.QA_LEAD))])
    async def write_as_lead_decorator(_access=Depends(require_project_access())):
        return {"ok": True}

    @app.get("/projects/{project_id}/settings")
    async def read_as_lead(
        _access=Depends(require_project_access()),
        _user=Depends(require_role(UserRole.QA_LEAD)),
    ):
        return {"ok": True}

    @app.post("/projects/{project_id}/notes")
    async def write_as_member(_access=Depends(require_project_access())):
        return {"ok": True}

    @app.post("/runs/{run_id}/defects")
    async def write_on_run(
        _access=Depends(require_run_access()),
        _user=Depends(require_role(UserRole.QA_ENGINEER)),
    ):
        return {"ok": True}

    @app.post("/releases/{release_id}/gate")
    async def write_on_release(
        _access=Depends(require_release_access()),
        _user=Depends(require_role(UserRole.QA_LEAD)),
    ):
        return {"ok": True}

    @app.post("/defects")
    async def write_with_body_project(
        body: _Body,
        db=Depends(get_db),
        user=Depends(require_role(UserRole.QA_ENGINEER)),
    ):
        project_id, allowed = await resolve_project_scope(db, user, body.project_id)
        return {"project_id": str(project_id) if project_id else None,
                "allowed": sorted(str(p) for p in allowed) if allowed is not None else None}

    @app.post("/sweep")
    async def write_across_projects(db=Depends(get_db), user=Depends(require_role(UserRole.QA_ENGINEER))):
        ids = await get_accessible_project_ids(db, user)
        return {"projects": sorted(str(p) for p in ids) if ids is not None else None}

    @app.get("/overview")
    async def read_across_projects(db=Depends(get_db), user=Depends(require_role(UserRole.QA_ENGINEER))):
        ids = await get_accessible_project_ids(db, user)
        return {"projects": sorted(str(p) for p in ids) if ids is not None else None}

    @app.post("/cases/{case_id}/verdict")
    async def write_on_case(case_id: uuid.UUID, db=Depends(get_db), user=Depends(require_role(UserRole.QA_ENGINEER))):
        await resolve_authorized_test_case(db, user, case_id)
        return {"ok": True}

    @app.put("/projects/{project_id}/agent-config")
    async def write_with_project_role(_user=Depends(require_project_role(UserRole.QA_LEAD))):
        return {"ok": True}

    return app


def _client(instance_role: str, memberships: dict[uuid.UUID, str]) -> TestClient:
    app = _app()
    user = SimpleNamespace(
        id=uuid.uuid4(), role=instance_role, is_active=True, must_change_password=False, username="u",
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
    """Membership comes from the fake session, never from a cache."""
    import app.db.redis_client as redis_client

    def _down():
        raise RuntimeError("no redis in this test")

    monkeypatch.setattr(redis_client, "get_redis", _down)
    monkeypatch.setattr(deps, "_count_auth_failure", lambda reason: None)


LEAD_ROUTES = [
    f"/projects/{PROJECT}/settings",
    f"/projects/{PROJECT}/settings-role-first",
    f"/projects/{PROJECT}/settings-decorator",
    f"/releases/{RELEASE}/gate",
]


# -- the reported case: instance QA lead, project viewer ---------------------


@pytest.mark.parametrize("path", LEAD_ROUTES)
@pytest.mark.parametrize("project_role", ["VIEWER", "TESTER", "QA_ENGINEER"])
def test_an_instance_lead_with_a_lower_project_role_cannot_write_as_a_lead(path, project_role):
    response = _client("QA_LEAD", {PROJECT: project_role}).post(path)

    assert response.status_code == 403
    assert "QA_LEAD" in response.json()["detail"] and FOR_THIS_PROJECT in response.json()["detail"]


@pytest.mark.parametrize("path", LEAD_ROUTES)
def test_a_lead_on_both_writes(path):
    assert _client("QA_LEAD", {PROJECT: "QA_LEAD"}).post(path).status_code == 200


@pytest.mark.parametrize("path", LEAD_ROUTES)
def test_an_instance_admin_writes_without_a_membership(path):
    assert _client("ADMIN", {}).post(path).status_code == 200


@pytest.mark.parametrize("path", LEAD_ROUTES)
def test_a_project_lead_below_the_instance_minimum_is_still_refused(path):
    """The instance role keeps its say: the rule adds a check, it removes none."""
    response = _client("QA_ENGINEER", {PROJECT: "QA_LEAD"}).post(path)

    assert response.status_code == 403
    assert FOR_THIS_PROJECT not in response.json()["detail"]


def test_a_non_member_gets_the_access_refusal_not_the_role_one():
    response = _client("QA_LEAD", {OTHER: "QA_LEAD"}).post(f"/projects/{PROJECT}/settings")

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this project"


# -- what does not change ----------------------------------------------------


def test_a_read_is_not_held_to_the_project_role():
    assert _client("QA_LEAD", {PROJECT: "VIEWER"}).get(f"/projects/{PROJECT}/settings").status_code == 200


def test_a_write_route_with_no_role_minimum_takes_any_member():
    assert _client("VIEWER", {PROJECT: "VIEWER"}).post(f"/projects/{PROJECT}/notes").status_code == 200


# -- every way a route names its project -------------------------------------


def test_a_run_id_is_held_to_the_role_in_the_runs_project():
    assert _client("QA_LEAD", {PROJECT: "TESTER"}).post(f"/runs/{RUN}/defects").status_code == 403
    assert _client("QA_LEAD", {PROJECT: "QA_ENGINEER"}).post(f"/runs/{RUN}/defects").status_code == 200


def test_a_test_case_id_is_held_to_the_role_in_its_project():
    assert _client("QA_LEAD", {PROJECT: "VIEWER"}).post(f"/cases/{CASE}/verdict").status_code == 403
    assert _client("QA_LEAD", {PROJECT: "QA_ENGINEER"}).post(f"/cases/{CASE}/verdict").status_code == 200


def test_a_project_id_in_the_body_is_held_to_the_role_in_that_project():
    refused = _client("QA_LEAD", {PROJECT: "VIEWER"}).post("/defects", json={"project_id": str(PROJECT)})
    assert refused.status_code == 403
    assert "QA_ENGINEER" in refused.json()["detail"] and FOR_THIS_PROJECT in refused.json()["detail"]

    allowed = _client("QA_LEAD", {PROJECT: "QA_ENGINEER"}).post("/defects", json={"project_id": str(PROJECT)})
    assert allowed.status_code == 200 and allowed.json()["project_id"] == str(PROJECT)


def test_a_body_project_the_caller_is_not_in_keeps_the_access_refusal():
    response = _client("QA_LEAD", {OTHER: "QA_LEAD"}).post("/defects", json={"project_id": str(PROJECT)})

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this project"


def test_a_write_naming_no_project_reaches_only_projects_where_the_role_is_held():
    client = _client("QA_LEAD", {PROJECT: "VIEWER", OTHER: "QA_ENGINEER"})

    assert client.post("/sweep").json() == {"projects": [str(OTHER)]}
    assert client.post("/defects", json={}).json() == {"project_id": None, "allowed": [str(OTHER)]}
    # A read on the same data still spans every membership.
    assert client.get("/overview").json() == {"projects": sorted([str(PROJECT), str(OTHER)])}


def test_an_admin_write_naming_no_project_stays_unrestricted():
    assert _client("ADMIN", {}).post("/sweep").json() == {"projects": None}


# -- require_project_role follows the same rule on a write -------------------


def test_the_project_role_guard_needs_the_instance_role_too_on_a_write():
    path = f"/projects/{PROJECT}/agent-config"

    assert _client("VIEWER", {PROJECT: "QA_LEAD"}).put(path).status_code == 403
    assert _client("QA_LEAD", {PROJECT: "VIEWER"}).put(path).status_code == 403
    assert _client("QA_LEAD", {PROJECT: "QA_LEAD"}).put(path).status_code == 200
    assert _client("ADMIN", {}).put(path).status_code == 200


# -- the minimum is read from the route, so it cannot be skipped by order ----


def test_the_route_minimum_is_the_highest_require_role_on_the_route():
    app = FastAPI()

    @app.post("/x", dependencies=[Depends(require_role(UserRole.TESTER))])
    async def handler(_user=Depends(require_role(UserRole.QA_LEAD))):
        return {}

    @app.post("/y")
    async def plain():
        return {}

    by_path = {route.path: route for route in app.routes}
    request = SimpleNamespace(scope={"route": by_path["/x"]}, method="POST")
    assert deps._route_role_floor(request) is UserRole.QA_LEAD
    assert deps._route_role_floor(SimpleNamespace(scope={"route": by_path["/y"]}, method="POST")) is None
    assert deps._route_role_floor(SimpleNamespace(scope={}, method="POST")) is None


def test_a_user_object_that_did_not_come_through_authentication_carries_no_minimum():
    """A service-loaded row or a test double is never read as carrying one."""
    from unittest.mock import MagicMock

    assert deps._project_role_floor(MagicMock()) is None
    assert deps._project_role_floor(SimpleNamespace()) is None
