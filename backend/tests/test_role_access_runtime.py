"""The reviewed access matrix, checked against the running app (enterprise roles E00-S02).

``security_role_access_matrix.json`` is computed statically from each route's guards. This drives real HTTP
requests through the app -- authentication, ``require_role`` / ``require_permission``, the project role floor --
for a sample of write routes, as each of the ten roles (held globally and in the project), and checks the
answer: a role the matrix leaves out gets 403; a role it admits gets past the guard (here: 422 for an empty
body, or 404 for a membership that does not exist). It also checks the lower-of-two rule end to end (D-2) and
that ``GET /api/v1/me/permissions`` answers every role.

SQLite in memory, a closed set of tables (most of the schema is PostgreSQL-only), as in ``test_mfa.py``.
"""
from __future__ import annotations

import itertools
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import types as sqltypes  # noqa: E402
from sqlalchemy.dialects.sqlite import base as _sqlite_types  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.db.postgres import Base, get_db  # noqa: E402
from app.models.postgres import (  # noqa: E402
    AccessAuditLog,
    ApiKey,
    AppSetting,
    IdentityEvent,
    Project,
    ProjectMember,
    RefreshTokenRecord,
    SettingsAuditLog,
    User,
    UserRole,
)

MATRIX = json.loads((Path(__file__).parent / "security_role_access_matrix.json").read_text(encoding="utf-8"))

_TABLES = [m.__table__ for m in (User, Project, ProjectMember, ApiKey, AppSetting, IdentityEvent,
                                 RefreshTokenRecord, SettingsAuditLog, AccessAuditLog)]

#: (method, path template, JSON body): one write route per rank, each refusing before it touches anything else.
SAMPLE = [
    ("POST", "/api/v1/test-management/cases", {}),                         # TESTER
    ("POST", "/api/v1/projects/{project_id}/timeline-notes", {}),          # QA_ENGINEER
    ("POST", "/api/v1/projects/{project_id}/members", {}),                 # QA_LEAD
    ("PATCH", "/api/v1/projects/{project_id}/members/{user_id}", {}),      # QA_LEAD
    ("DELETE", "/api/v1/projects/{project_id}/members/{user_id}", None),   # ADMIN
    ("POST", "/api/v1/users", {}),                                         # ADMIN
]


class _AwareDateTime(_sqlite_types.DATETIME):
    """Return aware datetimes, as PostgreSQL does (see ``test_mfa.py``)."""

    def result_processor(self, dialect, coltype):
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _redis(monkeypatch, fake_redis):
    import app.db.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: fake_redis)
    return fake_redis


@pytest.fixture(autouse=True)
def _no_membership_cache(monkeypatch):
    """Read memberships from the database on every request (no cached sets across role changes)."""
    from app.core import deps

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(deps, "invalidate_membership_cache", _none)


_hosts = itertools.count(1)


@pytest.fixture
async def client(db):
    from app.main import app, limiter

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001 -- best-effort test hygiene
        pass

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    n = next(_hosts)
    transport = ASGITransport(app=app, client=(f"10.77.{n % 250}.{(n // 250) % 250}", 5000))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


async def _person(db, project, role, project_role=None, additional=None, project_additional=None):
    user = User(
        email=f"{uuid.uuid4().hex[:10]}@example.com", username=f"u{uuid.uuid4().hex[:10]}", hashed_password="x",
        role=role.value, additional_roles=additional, is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(ProjectMember(
        user_id=user.id, project_id=project.id, role=(project_role or role).value, additional_roles=project_additional,
    ))
    await db.commit()
    return user


async def _project(db):
    project = Project(id=uuid.uuid4(), name=f"P {uuid.uuid4().hex[:6]}", slug=f"p-{uuid.uuid4().hex[:8]}",
                      is_active=True)
    db.add(project)
    await db.commit()
    return project


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _send(client, method, template, body, project, user):
    # Only the placeholders this path names: a random user id for a membership that does not exist (404 once
    # past the guard), the sample project's id.
    values = {"project_id": project.id, "user_id": uuid.uuid4()}
    path = template.format(**{name: value for name, value in values.items() if "{" + name + "}" in template})
    kwargs = {"headers": _auth(user)}
    if body is not None:
        kwargs["json"] = body
    return await client.request(method, path, **kwargs)


@pytest.mark.parametrize("method,template,body", SAMPLE, ids=[f"{m} {t}" for m, t, _ in SAMPLE])
async def test_every_role_gets_the_matrix_answer_from_the_running_app(client, db, method, template, body):
    admitted = set(MATRIX[f"{method} {template}"])
    project = await _project(db)
    wrong = []
    for role in list(UserRole):
        user = await _person(db, project, role)
        response = await _send(client, method, template, body, project, user)
        expected = {404, 422} if role.value in admitted else {403}
        if response.status_code not in expected:
            wrong.append((role.value, response.status_code, response.text[:160]))
    assert not wrong, wrong


async def test_the_lower_of_the_two_roles_decides_at_runtime(client, db):
    project = await _project(db)
    template = "/api/v1/projects/{project_id}/members"
    capped = await _person(db, project, UserRole.QA_LEAD, project_role=UserRole.VIEWER)
    raised = await _person(db, project, UserRole.VIEWER, project_role=UserRole.QA_LEAD)
    lead = await _person(db, project, UserRole.QA_LEAD)
    assert (await _send(client, "POST", template, {}, project, capped)).status_code == 403
    assert (await _send(client, "POST", template, {}, project, raised)).status_code == 403
    assert (await _send(client, "POST", template, {}, project, lead)).status_code == 422


async def test_an_extra_job_role_gives_no_rank_at_runtime(client, db):
    """Until a route names a permission, extra roles add nothing on it (fail closed)."""
    project = await _project(db)
    engineer_rm = await _person(db, project, UserRole.QA_ENGINEER, additional=["RELEASE_MANAGER"],
                                project_additional=["RELEASE_MANAGER"])
    response = await _send(client, "POST", "/api/v1/projects/{project_id}/members", {}, project, engineer_rm)
    assert response.status_code == 403


async def test_me_permissions_answers_every_role(client, db):
    project = await _project(db)
    for role in list(UserRole):
        user = await _person(db, project, role)
        response = await client.get(f"/api/v1/me/permissions?project_id={project.id}", headers=_auth(user))
        assert response.status_code == 200, (role, response.text[:200])
        body = response.json()
        assert body["role"] == role.value and body["project_role"] == role.value
        assert "project.read" in body["read_permissions"], role
    capped = await _person(db, project, UserRole.QA_LEAD, project_role=UserRole.VIEWER)
    body = (await client.get(f"/api/v1/me/permissions?project_id={project.id}", headers=_auth(capped))).json()
    assert "member.manage" not in body["write_permissions"]
    other = await _project(db)
    response = await client.get(f"/api/v1/me/permissions?project_id={other.id}", headers=_auth(capped))
    assert response.status_code == 403
