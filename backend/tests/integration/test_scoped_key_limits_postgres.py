"""QA-R3-11 against a real database: a scoped API key is limited to its scopes.

``api_keys.scopes`` was read only by the streaming ingest dependency. A key
bound to project A with ``scopes=["stream:write"]`` (the scope the API Keys
page mints for CI) could, through N20's ``allow_project_key=True`` routes,
delete A's runs and reset A, and ``POST /api/v1/keys {"name": "rotated"}``
minted it an unrestricted (``scopes: []``), never-expiring replacement that
survived revoking the original.

Now a scoped key needs ``project:admin`` on those routes, an empty (legacy)
scope list stays full access, and a key that mints a key grants no scope it
does not hold and no expiry later than its own.

Real app, real ``api_keys`` rows authenticated by ``_validate_api_key``, no
auth override. ``get_db`` and ``AsyncSessionLocal`` point at this test's
engine; the app's own engine is disposed at the start and end (QA-R3-14).
Doubles: the MongoDB citation count (0) and ``delete_run_everywhere.delay``,
which binds its arguments to the task's real signature.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
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


class _Mongo:
    """The citation check counts decision reports; there are none."""

    class _Collection:
        async def count_documents(self, _query):
            return 0

    def __getitem__(self, _name):
        return self._Collection()


#: name -> (scopes, days until expiry or None). Every key is bound to project A.
_KEYS = {
    "stream": (["stream:write"], 30),
    "project_admin": (["stream:write", "project:admin"], 10),
    "legacy": ([], None),
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
        DeletionJob,
        LaunchStatus,
        Project,
        ProjectActivityEvent,
        TestRun,
        User,
        UserRole,
    )
    from app.worker.tasks import delete_run_everywhere

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(
        _env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=4, max_overflow=0
    )
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
    monkeypatch.setattr("app.db.mongo.get_mongo_db", lambda: _Mongo())

    queued: list[tuple] = []
    task = delete_run_everywhere._get_current_object()
    signature = inspect.signature(task.run)

    def _delay(*args, **kwargs):
        signature.bind(*args, **kwargs)  # the argument check a real broker hop runs
        queued.append(args)

    monkeypatch.setattr(task, "delay", _delay)

    tag = uuid.uuid4().hex[:10]
    project_a = uuid.uuid4()
    project_a_name = f"r4s-a-{tag}"
    owner = uuid.uuid4()
    run_1, run_2 = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    raw = {name: f"qai_{secrets.token_urlsafe(32)}" for name in _KEYS}
    expiry = {
        name: (None if days is None else now + timedelta(days=days))
        for name, (_scopes, days) in _KEYS.items()
    }

    async with sessions() as db:
        db.add(Project(id=project_a, name=project_a_name, slug=project_a_name, is_active=True))
        db.add(User(
            id=owner, email=f"r4s-owner-{tag}@example.com", username=f"r4s_owner_{tag}",
            full_name="R4 key owner", hashed_password="!unusable", role=UserRole.ADMIN.value,
        ))
        await db.flush()
        for rid in (run_1, run_2):
            db.add(TestRun(
                id=rid, project_id=project_a, build_number=f"r4s-{rid.hex[:8]}",
                jenkins_job="r4s", status=LaunchStatus.FAILED, ingestion_source="unknown",
                total_tests=1, failed_tests=1,
            ))
        for name, (scopes, _days) in _KEYS.items():
            db.add(ApiKey(
                id=uuid.uuid4(), user_id=owner, name=f"team-a {name}",
                key_hash=hashlib.sha256(raw[name].encode()).hexdigest(),
                key_hint=raw[name][:8] + "...", scopes=scopes, project_id=project_a,
                expires_at=expiry[name],
            ))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, queued=queued,
        project_a=project_a, project_a_name=project_a_name, owner=owner,
        run_1=run_1, run_2=run_2, expiry=expiry,
        headers={name: {"X-API-Key": value} for name, value in raw.items()},
        admin_jwt={"Authorization": f"Bearer {create_access_token(str(owner))}"},
        models=SimpleNamespace(ApiKey=ApiKey, TestRun=TestRun),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(ApiKey).where(ApiKey.user_id == owner),
            delete(DeletionJob).where(DeletionJob.project_id == project_a),
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project_a),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id == owner),
            delete(TestRun).where(TestRun.project_id == project_a),
            delete(User).where(User.id == owner),
            delete(Project).where(Project.id == project_a),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"r4 scopes teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _runs_left(world) -> int:
    TestRun = world.models.TestRun
    async with world.sessions() as db:
        return (await db.execute(
            select(func.count()).select_from(TestRun).where(TestRun.project_id == world.project_a)
        )).scalar_one()


async def _key_count(world) -> int:
    ApiKey = world.models.ApiKey
    async with world.sessions() as db:
        return (await db.execute(
            select(func.count()).select_from(ApiKey).where(ApiKey.user_id == world.owner)
        )).scalar_one()


def _delete_run(world, headers, run_id):
    return world.client.request(
        "DELETE", f"/api/v1/runs/{run_id}", headers=headers,
        json={"confirm": True, "reason": "r4 scope probe"},
    )


def _mint(world, key, body):
    headers = world.headers[key] if isinstance(key, str) else key
    return world.client.post("/api/v1/keys", headers=headers, json=body)


def _expires(resp) -> datetime | None:
    value = resp.json()["expires_at"]
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── a stream-scoped key does not administer its project ─────────────────────


async def test_a_stream_scoped_key_cannot_delete_its_projects_run(world):
    resp = await _delete_run(world, world.headers["stream"], world.run_1)

    assert resp.status_code == 403, resp.text
    assert "project:admin" in resp.json()["detail"]
    assert world.queued == []
    assert await _runs_left(world) == 2


async def test_a_stream_scoped_key_cannot_reset_its_project(world):
    resp = await world.client.post(
        f"/api/v1/projects/{world.project_a}/reset", headers=world.headers["stream"],
        json={"mode": "runs", "confirmation_name": world.project_a_name},
    )

    assert resp.status_code == 403, resp.text
    assert await _runs_left(world) == 2


# ── ...and a key that holds project:admin, or a legacy key, still does ──────


async def test_a_project_admin_scoped_key_still_administers_its_project(world):
    deleted = await _delete_run(world, world.headers["project_admin"], world.run_1)
    # A wrong confirmation name: past the authorization, refused by the typed
    # confirmation, so nothing is wiped.
    reset = await world.client.post(
        f"/api/v1/projects/{world.project_a}/reset", headers=world.headers["project_admin"],
        json={"mode": "runs", "confirmation_name": "not the project name"},
    )

    assert deleted.status_code == 202, deleted.text
    assert [args[0] for args in world.queued] == [str(world.run_1)]
    assert reset.status_code != 403 and reset.status_code < 500, reset.text


async def test_a_legacy_unscoped_key_is_unchanged(world):
    deleted = await _delete_run(world, world.headers["legacy"], world.run_2)
    minted = await _mint(world, "legacy", {"name": "legacy rotated"})

    assert deleted.status_code == 202, deleted.text
    assert minted.status_code == 201, minted.text
    assert minted.json()["scopes"] == []
    assert minted.json()["expires_at"] is None
    assert minted.json()["project_id"] == str(world.project_a)


# ── a key mints no more than it holds ────────────────────────────────────────


@pytest.mark.parametrize("scopes", [[], ["stream:write", "project:admin"], ["project:admin"]])
async def test_a_stream_scoped_key_cannot_mint_a_wider_key(world, scopes):
    resp = await _mint(world, "stream", {"name": "escape", "scopes": scopes})

    assert resp.status_code == 403, resp.text
    assert "raw_key" not in resp.text
    assert await _key_count(world) == len(_KEYS)


async def test_rotating_a_scoped_key_keeps_its_scopes_and_end_date(world):
    """``testlookup keys create`` sends only a name. The rotated key is as
    narrow as the original and ends when it does, so revoking the original
    and waiting out its expiry both still bound what leaked."""
    rotated = await _mint(world, "stream", {"name": "rotate-cli"})

    assert rotated.status_code == 201, rotated.text
    body = rotated.json()
    assert body["scopes"] == ["stream:write"]
    assert body["project_id"] == str(world.project_a)
    assert _expires(rotated) == world.expiry["stream"]

    child = {"X-API-Key": body["raw_key"]}
    child_delete = await _delete_run(world, child, world.run_1)
    child_escape = await _mint(world, child, {"name": "escape", "scopes": []})
    assert child_delete.status_code == 403, child_delete.text
    assert child_escape.status_code == 403, child_escape.text
    assert await _runs_left(world) == 2


async def test_a_minted_key_may_not_outlive_its_minter(world):
    too_long = await _mint(world, "stream", {"name": "long", "expires_days": 365})
    shorter = await _mint(world, "stream", {"name": "short", "expires_days": 7})

    assert too_long.status_code == 403, too_long.text
    assert shorter.status_code == 201, shorter.text
    assert _expires(shorter) < world.expiry["stream"]
    assert await _key_count(world) == len(_KEYS) + 1


async def test_a_subset_of_the_callers_scopes_is_mintable(world):
    resp = await _mint(world, "project_admin", {"name": "stream only", "scopes": ["stream:write"]})

    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == ["stream:write"]
    assert _expires(resp) == world.expiry["project_admin"]


async def test_a_signed_in_admin_mints_project_admin_keys_as_before(world):
    """``project:admin`` is mintable, and a JWT caller has no scope or expiry cap."""
    resp = await _mint(world, world.admin_jwt, {
        "name": "ci admin", "project_id": str(world.project_a),
        "scopes": ["stream:write", "project:admin"],
    })

    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == ["stream:write", "project:admin"]
    assert resp.json()["expires_at"] is None
