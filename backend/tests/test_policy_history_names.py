"""W11 GATE-02: the policy history names who created and activated each version.

``ReleaseGatePolicyResponse`` carries ``created_by`` / ``activated_by`` as bare
user ids, ``/users`` is admin-scoped for names, and a QA lead reads the history
too -- so the two history routes resolve the names themselves, in ONE
``SELECT id, username FROM users WHERE id IN (...)`` after the rows (none when
there are no rows). A deleted user reads ``None``. The routes' authorization is
unchanged: the project history keeps ``require_project_access()``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import ReleaseGatePolicy, UserRole
from app.routers import release_gate_policies as router_module
from app.routers.release_gate_policies import get_policy_history, get_system_policy_history

PROJECT_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
PRIYA = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
OMAR = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
GONE = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000dead")
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _policy(version: int, *, created_by, activated_by=None, project_id=PROJECT_ID, active=False):
    return ReleaseGatePolicy(
        id=uuid.uuid4(),
        project_id=project_id,
        version=version,
        name="Payments gate",
        description=None,
        rules={"thresholds": {"go_min": 20}},
        is_active=active,
        is_draft=not active and activated_by is None,
        created_by=created_by,
        activated_by=activated_by,
        activated_at=NOW if activated_by else None,
        created_at=NOW,
        updated_at=None,
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Db:
    """The history rows first, then the users read; records every statement."""

    def __init__(self, policies, users):
        self.policies = policies
        self.users = users
        self.statements: list[str] = []
        self.user_ids: list = []

    async def execute(self, stmt, params=None):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        self.statements.append(sql)
        if sql.startswith("SELECT users.id, users.username FROM users"):
            wanted = next(iter(compiled.params.values()))
            self.user_ids.append(sorted(wanted, key=str))
            return _Result([(uid, name) for uid, name in self.users.items() if uid in wanted])
        if sql.startswith("SELECT project_members.id"):
            return _Result([])  # not a member
        return _Result(self.policies)


@pytest.mark.asyncio
async def test_each_version_names_its_creator_and_activator():
    rows = [
        _policy(2, created_by=OMAR, activated_by=PRIYA, active=True),
        _policy(1, created_by=PRIYA),
    ]
    db = _Db(rows, {PRIYA: "priya", OMAR: "omar"})

    out = await get_policy_history(project_id=PROJECT_ID, current_user=None, db=db, _=None)

    assert [(r.version, r.created_by_name, r.activated_by_name) for r in out] == [
        (2, "omar", "priya"),
        (1, "priya", None),
    ]
    # Rows + ONE users read naming both people (each once).
    assert len(db.statements) == 2
    assert db.user_ids == [sorted([PRIYA, OMAR], key=str)]


@pytest.mark.asyncio
async def test_a_deleted_creator_reads_none():
    db = _Db([_policy(1, created_by=GONE)], {PRIYA: "priya"})

    out = await get_policy_history(project_id=PROJECT_ID, current_user=None, db=db, _=None)

    assert out[0].created_by_name is None
    assert out[0].created_by == GONE


@pytest.mark.asyncio
async def test_the_system_history_names_the_same_way():
    rows = [
        _policy(3, created_by=PRIYA, activated_by=OMAR, project_id=None, active=True),
        _policy(2, created_by=OMAR, project_id=None),
    ]
    db = _Db(rows, {PRIYA: "priya", OMAR: "omar"})

    out = await get_system_policy_history(current_user=None, db=db)

    assert [(r.version, r.created_by_name, r.activated_by_name) for r in out] == [
        (3, "priya", "omar"),
        (2, "omar", None),
    ]
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_no_rows_reads_no_users():
    db = _Db([], {PRIYA: "priya"})

    assert await get_policy_history(project_id=PROJECT_ID, current_user=None, db=db, _=None) == []
    assert await get_system_policy_history(current_user=None, db=db) == []
    assert len(db.statements) == 2
    assert not any(s.startswith("SELECT users.id") for s in db.statements)


def test_another_tenants_project_is_refused_by_the_dependency():
    """The authorization is unchanged: a non-member gets 403 before any read."""
    app = FastAPI()
    app.include_router(router_module.router)
    outsider = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD, is_active=True)
    db = _Db([_policy(1, created_by=PRIYA)], {PRIYA: "priya"})

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: outsider

    with TestClient(app) as client:
        resp = client.get(f"/api/v1/release-gate-policies/history/{PROJECT_ID}")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "You do not have access to this project"
    assert not any(s.startswith("SELECT release_gate_policies") for s in db.statements)
    assert not any(s.startswith("SELECT users.id") for s in db.statements)


def test_the_route_serialises_the_names():
    """Through the response model: the two optional fields reach the JSON."""
    app = FastAPI()
    app.include_router(router_module.router)
    admin = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, is_active=True)
    db = _Db([_policy(1, created_by=PRIYA, activated_by=OMAR, active=True)], {PRIYA: "priya", OMAR: "omar"})

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: admin

    with TestClient(app) as client:
        resp = client.get(f"/api/v1/release-gate-policies/history/{PROJECT_ID}")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["created_by_name"] == "priya"
    assert body[0]["activated_by_name"] == "omar"
    assert body[0]["created_by"] == str(PRIYA)
