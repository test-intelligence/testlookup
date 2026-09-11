"""Follow-up to re-audit N32: the Jira webhook's credential is Jira's signature.

``POST /api/v1/feedback/jira-webhook`` verified nothing: any signed-in caller
(or API key) could post a forged "resolved" event and close any project's
defect, writing AI training feedback with it. A real Jira delivery carries no
session at all. The route is now public and requires
``X-Hub-Signature: sha256=<HMAC-SHA256 of the raw body>`` with
``JIRA_WEBHOOK_SECRET``, compared in constant time; it refuses every delivery
(403) while the secret is unset or a placeholder.

Real app through ASGI, a real ``defects`` row the forged event would close.
The app's engine is disposed at the start and the end.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PATH = "/api/v1/feedback/jira-webhook"
_SECRET = "n34-jira-secret-" + "7f3a9c1e5b2d8046"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import Defect, User, UserRole

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
    monkeypatch.setattr(settings, "JIRA_WEBHOOK_SECRET", _SECRET)

    tag = uuid.uuid4().hex[:10]
    defect_id, admin = uuid.uuid4(), uuid.uuid4()
    issue_key = f"N34-{tag}"
    async with sessions() as db:
        db.add(User(
            id=admin, email=f"n34-admin-{tag}@example.com", username=f"n34_admin_{tag}",
            full_name="N34 admin", hashed_password="!unusable", role=UserRole.ADMIN.value,
        ))
        db.add(Defect(id=defect_id, jira_ticket_id=issue_key, resolution_status="OPEN"))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    body = json.dumps({
        "issue": {"key": issue_key, "fields": {"status": {"name": "Done"}, "resolution": None}},
    }).encode()
    world = SimpleNamespace(
        client=client, sessions=sessions, settings=settings, defect_id=defect_id, body=body,
        admin_jwt={"Authorization": f"Bearer {create_access_token(str(admin))}"},
        Defect=Defect,
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(Defect).where(Defect.id == defect_id),
            delete(User).where(User.id == admin),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"n34 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _status(world) -> str:
    async with world.sessions() as db:
        return (await db.execute(
            select(world.Defect.resolution_status).where(world.Defect.id == world.defect_id)
        )).scalar_one()


def _post(world, headers, body=None):
    return world.client.post(
        _PATH, content=world.body if body is None else body,
        headers={"Content-Type": "application/json", **headers},
    )


async def test_a_delivery_signed_with_the_secret_is_applied(world):
    resp = await _post(world, {"X-Hub-Signature": _sign(world.body)})

    assert resp.status_code == 200, resp.text
    assert await _status(world) == "RESOLVED"


@pytest.mark.parametrize("case", [
    "no signature, signed-in admin",
    "signed with another secret",
    "signature of a different body",
    "hex without the sha256= prefix",
    "the secret in another header",
])
async def test_a_forged_delivery_is_refused_and_changes_nothing(world, case):
    headers = {
        "no signature, signed-in admin": world.admin_jwt,
        "signed with another secret": {"X-Hub-Signature": _sign(world.body, "some-other-secret")},
        "signature of a different body": {"X-Hub-Signature": _sign(world.body + b" ")},
        "hex without the sha256= prefix": {"X-Hub-Signature": _sign(world.body)[len("sha256="):]},
        "the secret in another header": {"X-Webhook-Secret": _SECRET, "X-Signature": _sign(world.body)},
    }[case]

    resp = await _post(world, headers)

    assert resp.status_code == 401, resp.text
    assert await _status(world) == "OPEN"


@pytest.mark.parametrize("secret", [None, "", "change-me-webhook-secret"])
async def test_an_unset_or_placeholder_secret_refuses_every_delivery(world, monkeypatch, secret):
    monkeypatch.setattr(world.settings, "JIRA_WEBHOOK_SECRET", secret)

    # Signed with exactly what an attacker would try: the empty or published key.
    resp = await _post(world, {"X-Hub-Signature": _sign(world.body, secret or "")})

    assert resp.status_code == 403, resp.text
    assert await _status(world) == "OPEN"


async def test_a_signed_body_that_is_not_an_object_is_a_422(world):
    body = b"[1, 2]"

    resp = await _post(world, {"X-Hub-Signature": _sign(body)}, body=body)

    assert resp.status_code == 422, resp.text


def test_the_signature_is_compared_in_constant_time():
    """Timing cannot be asserted through a request; the comparison can be read."""
    import ast
    import inspect
    import textwrap

    from app.routers import feedback

    tree = ast.parse(textwrap.dedent(inspect.getsource(feedback._verify_jira_signature)))
    digests = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "compare_digest"
    ]
    equalities = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
    ]
    assert len(digests) == 1 and equalities == []


def test_the_route_takes_no_session():
    """The credential is the signature: no user dependency on the route."""
    from fastapi.routing import APIRoute

    from app.main import app

    route = next(
        r for r in app.routes
        if isinstance(r, APIRoute) and r.path == _PATH and "POST" in r.methods
    )

    def names(dependant):
        out = [getattr(dependant.call, "__qualname__", "")]
        for sub in dependant.dependencies:
            out += names(sub)
        return out

    assert not {"get_current_user_or_api_key", "get_current_active_user"} & set(names(route.dependant))
