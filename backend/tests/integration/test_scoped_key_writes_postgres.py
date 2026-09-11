"""Re-audit N32 / N31 against a real database: a scoped key writes only with a write scope.

Scopes were enforced in two places: ``require_role`` at QA_LEAD and above, and
the project guards' writes. Every write gated at QA_ENGINEER or merely signed
in (suites, saved views, triage, feedback, notification preferences, ...)
still ran with the scoped key's OWNER role, so a ``["stream:write"]`` CI key
wrote through all of them. And the key form offered five scope names nothing
read (N31): a key "limited" to ``report:read`` acted with full access.

The rule now:

* a key with a non-empty scope list, on any method but GET/HEAD/OPTIONS,
  needs ``project:write`` or ``project:admin`` -- enforced in
  ``get_current_active_user``, which every role and project guard resolves;
* QA_LEAD+ (``require_role``, ``require_project_role``) needs ``project:admin``;
* two handlers take a scoped key's writes and confine them themselves
  (``takes_scoped_key_writes``): ``POST /api/v1/keys``, ``DELETE /api/v1/keys/{key_id}``;
* the ingest routes authenticate through their own dependencies and never
  reach the rule;
* ``POST /api/v1/keys`` refuses a scope outside ``stream:write``,
  ``project:write``, ``project:admin`` with a 422.

Legacy (empty-scope) keys and JWTs are unchanged.

Real app, real ``api_keys`` rows, no auth override; the app's engine is
disposed at the start and the end. Bodies are deliberately empty: a
dependency's 403 comes before the body's 422, so 422 means "authorized".
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


#: name -> (scopes, bound to project A). All owned by one ADMIN.
_KEYS = {
    "stream": (["stream:write"], True),
    "stream_unbound": (["stream:write"], False),
    "writer": (["project:write"], True),
    "writer_unbound": (["project:write"], False),
    "admin": (["project:admin"], True),
    "legacy": ([], True),
}


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        AccessAuditLog,
        ApiKey,
        Project,
        ProjectActivityEvent,
        User,
        UserRole,
    )

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_asyncio.Redis.from_url(_env("REDIS_URL"), decode_responses=True)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    project_a = uuid.uuid4()
    owner = uuid.uuid4()
    raw = {name: f"qai_{secrets.token_urlsafe(32)}" for name in _KEYS}
    key_ids = {name: uuid.uuid4() for name in _KEYS}

    async with sessions() as db:
        name = f"n32-a-{tag}"
        db.add(Project(id=project_a, name=name, slug=name, is_active=True))
        db.add(User(
            id=owner, email=f"n32-owner-{tag}@example.com", username=f"n32_owner_{tag}",
            full_name="N32 key owner", hashed_password="!unusable", role=UserRole.ADMIN.value,
        ))
        await db.flush()
        for key, (scopes, bound) in _KEYS.items():
            db.add(ApiKey(
                id=key_ids[key], user_id=owner, name=f"n32 {key}",
                key_hash=hashlib.sha256(raw[key].encode()).hexdigest(),
                key_hint=raw[key][:8] + "...", scopes=scopes,
                project_id=project_a if bound else None,
            ))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, owner=owner, project_a=project_a,
        key_ids=key_ids, models=SimpleNamespace(ApiKey=ApiKey),
        headers={
            **{key: {"X-API-Key": value} for key, value in raw.items()},
            "jwt": {"Authorization": f"Bearer {create_access_token(str(owner))}"},
        },
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(ApiKey).where(ApiKey.user_id == owner),
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_a),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id == owner),
            delete(Project).where(Project.id == project_a),
            delete(User).where(User.email.like(f"n32-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"n32 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


# ── the write class below QA_LEAD ───────────────────────────────────────────


def _writes(world):
    """One route per gate shape below QA_LEAD, each with a required body."""
    return {
        # signed in only (get_current_active_user)
        "notification preference": ("POST", "/api/v1/notifications/preferences"),
        # QA_ENGINEER role only
        "create suite": ("POST", "/api/v1/suites"),
        # project guard only, no role
        "record fix outcome": ("POST", f"/api/v1/projects/{world.project_a}/fix-outcomes"),
        # a triage write on the caller's own inbox
        "triage a failure": ("PUT", f"/api/v1/me/assigned-failures/{uuid.uuid4()}/triage"),
        # the Jira webhook, which reached no guard before N32
        "jira webhook": ("POST", "/api/v1/feedback/jira-webhook"),
    }


_DOORS = sorted(_writes(SimpleNamespace(project_a="x")))


@pytest.mark.parametrize("key", ["stream", "stream_unbound"])
@pytest.mark.parametrize("door", _DOORS)
async def test_a_key_without_a_write_scope_cannot_write(world, door, key):
    from app.core.deps import PROJECT_WRITE_SCOPE_DETAIL

    method, path = _writes(world)[door]

    resp = await world.client.request(method, path, headers=world.headers[key])

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PROJECT_WRITE_SCOPE_DETAIL


@pytest.mark.parametrize("key", ["writer", "writer_unbound", "admin", "legacy", "jwt"])
@pytest.mark.parametrize("door", _DOORS)
async def test_a_write_scope_a_legacy_key_or_a_jwt_passes_authorization(world, door, key):
    method, path = _writes(world)[door]

    resp = await world.client.request(method, path, headers=world.headers[key])

    # Past every dependency: the empty body is what is refused.
    assert resp.status_code == 422, resp.text


async def test_a_stream_key_still_reads(world):
    resp = await world.client.get("/api/v1/saved-views", headers=world.headers["stream"])

    assert resp.status_code == 200, resp.text


# ── project:write is not project:admin ──────────────────────────────────────


async def test_project_write_does_not_reach_a_qa_lead_route(world):
    from app.core.deps import PROJECT_ADMIN_SCOPE_DETAIL

    role_gate = await world.client.post("/api/v1/releases", headers=world.headers["writer"])
    project_role_gate = await world.client.put(
        f"/api/v1/projects/{world.project_a}/fixer/config", headers=world.headers["writer"]
    )

    for resp in (role_gate, project_role_gate):
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == PROJECT_ADMIN_SCOPE_DETAIL


async def test_project_admin_reaches_the_project_role_gate(world):
    resp = await world.client.put(
        f"/api/v1/projects/{world.project_a}/fixer/config", headers=world.headers["admin"]
    )

    assert resp.status_code == 422, resp.text


# ── the routes that take a scoped key's writes ──────────────────────────────


async def test_a_stream_key_still_rotates_and_revokes_itself(world):
    minted = await world.client.post(
        "/api/v1/keys", headers=world.headers["stream"], json={"name": f"n32 rotated {world.tag}"}
    )
    assert minted.status_code == 201, minted.text
    assert minted.json()["scopes"] == ["stream:write"]

    revoked = await world.client.delete(
        f"/api/v1/keys/{world.key_ids['stream']}", headers=world.headers["stream"]
    )
    assert revoked.status_code == 204, revoked.text


async def test_a_stream_key_still_ingests(world):
    resp = await world.client.post("/api/v1/ingest", headers=world.headers["stream"])

    assert resp.status_code == 422, resp.text


def _marked_routes():
    from fastapi.routing import APIRoute

    from app.core.deps import _TAKES_SCOPED_KEY_WRITES_ATTR
    from app.main import app

    return {
        (method, route.path)
        for route in app.routes if isinstance(route, APIRoute)
        if getattr(route.endpoint, _TAKES_SCOPED_KEY_WRITES_ATTR, False)
        for method in route.methods
    }


async def test_only_the_reviewed_routes_take_a_scoped_keys_writes():
    """A new marker lets every scoped key write through a route: review it here."""
    assert _marked_routes() == {("POST", "/api/v1/keys"), ("DELETE", "/api/v1/keys/{key_id}")}


#: The SDK/CLI ingest routes (as in test_scoped_key_limits_postgres.py).
_KEY_INGEST_ROUTES = (
    ("POST", "/api/v1/stream/sessions"),
    ("DELETE", "/api/v1/stream/sessions/{session_id}"),
    ("POST", "/api/v1/stream/events/batch"),
    ("POST", "/api/v1/stream/ingest"),
    ("POST", "/api/v1/ingest"),
    ("POST", "/api/v1/ingest/file"),
    ("POST", "/ws/events/{run_id}"),
)


async def test_no_ingest_route_reaches_the_write_rule_unmarked():
    """Routing an ingest endpoint through get_current_active_user would refuse
    every ["stream:write"] pipeline; this fails first."""
    from fastapi.routing import APIRoute

    from app.main import app

    def reaches(dependant):
        if getattr(dependant.call, "__qualname__", "") == "get_current_active_user":
            return True
        return any(reaches(sub) for sub in dependant.dependencies)

    routes = {
        (method, route.path): route
        for route in app.routes if isinstance(route, APIRoute) for method in route.methods
    }
    assert all(key in routes for key in _KEY_INGEST_ROUTES)
    offenders = [
        key for key in _KEY_INGEST_ROUTES
        if reaches(routes[key].dependant) and key not in _marked_routes()
    ]
    assert offenders == []
    # ...and the walker does see the dependency where it is.
    assert reaches(routes[("POST", "/api/v1/suites")].dependant)


# ── N31: only enforced scope names can be minted ────────────────────────────


async def _key_count(world) -> int:
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        return (await db.execute(
            select(func.count()).select_from(ApiKey).where(ApiKey.user_id == world.owner)
        )).scalar_one()


@pytest.mark.parametrize("scopes", [
    ["report:read"], ["test:write"], ["admin:read"], ["stream:write", "test:read"],
])
async def test_an_unenforced_scope_name_is_refused(world, scopes):
    before = await _key_count(world)

    resp = await world.client.post(
        "/api/v1/keys", headers=world.headers["jwt"], json={"name": "n32 unknown", "scopes": scopes},
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "stream:write, project:write, project:admin" in detail
    assert await _key_count(world) == before


@pytest.mark.parametrize("scopes", [
    ["stream:write"], ["project:write"], ["project:admin"],
    ["stream:write", "project:write"], [],
])
async def test_every_enforced_scope_name_is_mintable(world, scopes):
    resp = await world.client.post(
        "/api/v1/keys", headers=world.headers["jwt"], json={"name": "n32 known", "scopes": scopes},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == scopes


async def test_the_vocabulary_is_the_three_enforced_scopes():
    from app.core.deps import API_KEY_SCOPES

    assert list(API_KEY_SCOPES) == ["stream:write", "project:write", "project:admin"]
