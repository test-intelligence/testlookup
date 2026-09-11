"""Re-audit N35 on real PostgreSQL + Redis: revoking a key revokes what it minted.

A leaked CI key could mint itself a replacement (``POST /api/v1/keys`` with the
key), and revoking the leaked key left the replacement working. Migration 0168
records ``api_keys.minted_by_key_id`` and ``DELETE /api/v1/keys/{id}`` cascades.

The tree, minted through the REAL route where a route can mint it::

    parent (JWT-minted root)
    |-- root   (minted by parent's key)          <- revoked by the test
    |   |-- child (minted by root's key)
    |   |   |-- grandchild (minted by child's key)
    |   |   `-- foreign   (another owner; planted: no route mints it)
    |   `-- dormant (already inactive, planted)
    |       `-- orphan (active, planted under the inactive one)
    `-- sibling (minted by parent's key)          <- left alone

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, database at head.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _raw() -> str:
    return f"qai_{secrets.token_urlsafe(32)}"


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    import app.db.postgres as app_postgres
    from app.core.security import create_access_token
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import ApiKey, Project, ProjectActivityEvent, User, UserRole
    from app.services.live_event_authz import STREAMING_KEY_PREFIX

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
    project = uuid.uuid4()
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    async with sessions() as db:
        db.add(Project(id=project, name=f"n35-{tag}", slug=f"n35-{tag}", is_active=True))
        for uid, name in ((owner, "owner"), (other_owner, "other")):
            db.add(User(
                id=uid, email=f"n35-{name}-{tag}@example.com", username=f"n35_{name}_{tag}",
                full_name=f"N35 {name}", hashed_password="!unusable", role=UserRole.ADMIN.value,
            ))
        await db.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, redis=redis, project=project,
        owner=owner, other_owner=other_owner, prefix=STREAMING_KEY_PREFIX,
        jwt={"Authorization": f"Bearer {create_access_token(str(owner))}"},
        ApiKey=ApiKey, ProjectActivityEvent=ProjectActivityEvent, planted_cache=[],
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for cache_key in world.planted_cache:
            await redis.delete(cache_key)
        for statement in (
            delete(ProjectActivityEvent).where(ProjectActivityEvent.project_id == project),
            delete(ApiKey).where(ApiKey.user_id.in_([owner, other_owner])),
            delete(User).where(User.id.in_([owner, other_owner])),
            delete(Project).where(Project.id == project),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"n35 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _mint(world, headers: dict, name: str) -> tuple[uuid.UUID, str]:
    resp = await world.client.post(
        "/api/v1/keys", headers=headers, json={"name": name, "project_id": str(world.project)}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return uuid.UUID(body["id"]), body["raw_key"]


async def _plant(world, *, owner, minted_by, name, active=True) -> tuple[uuid.UUID, str]:
    raw = _raw()
    key_id = uuid.uuid4()
    async with world.sessions.begin() as db:
        db.add(world.ApiKey(
            id=key_id, user_id=owner, name=name, key_hash=_digest(raw), key_hint=raw[:8] + "...",
            scopes=[], project_id=world.project, minted_by_key_id=minted_by, is_active=active,
        ))
    return key_id, raw


async def _tree(world) -> dict[str, tuple[uuid.UUID, str]]:
    keys = {}
    keys["parent"] = await _mint(world, world.jwt, "parent")
    keys["root"] = await _mint(world, {"X-API-Key": keys["parent"][1]}, "root")
    keys["sibling"] = await _mint(world, {"X-API-Key": keys["parent"][1]}, "sibling")
    keys["child"] = await _mint(world, {"X-API-Key": keys["root"][1]}, "child")
    keys["grandchild"] = await _mint(world, {"X-API-Key": keys["child"][1]}, "grandchild")
    keys["foreign"] = await _plant(
        world, owner=world.other_owner, minted_by=keys["child"][0], name="foreign"
    )
    keys["dormant"] = await _plant(
        world, owner=world.owner, minted_by=keys["root"][0], name="dormant", active=False
    )
    keys["orphan"] = await _plant(
        world, owner=world.owner, minted_by=keys["dormant"][0], name="orphan"
    )
    for _key_id, raw in keys.values():
        cache_key = f"{world.prefix}{_digest(raw)}"
        await world.redis.set(cache_key, str(world.project), ex=120)
        world.planted_cache.append(cache_key)
    return keys


async def _active(world, keys) -> dict[str, bool]:
    ids = {key_id: name for name, (key_id, _raw_key) in keys.items()}
    async with world.sessions() as db:
        rows = (await db.execute(
            select(world.ApiKey.id, world.ApiKey.is_active).where(world.ApiKey.id.in_(ids))
        )).all()
    return {ids[row.id]: bool(row.is_active) for row in rows}


async def _revoke(world, keys, name="root"):
    resp = await world.client.delete(f"/api/v1/keys/{keys[name][0]}", headers=world.jwt)
    assert resp.status_code == 204, resp.text


async def test_a_key_minted_by_a_key_records_its_parent(world):
    parent_id, parent_raw = await _mint(world, world.jwt, "parent")
    child_id, _ = await _mint(world, {"X-API-Key": parent_raw}, "child")
    async with world.sessions() as db:
        minted = dict((await db.execute(
            select(world.ApiKey.id, world.ApiKey.minted_by_key_id)
            .where(world.ApiKey.id.in_([parent_id, child_id]))
        )).all())
    assert minted == {parent_id: None, child_id: parent_id}


async def test_revoking_a_key_revokes_its_whole_subtree_and_nothing_else(world):
    keys = await _tree(world)
    await _revoke(world, keys)
    assert await _active(world, keys) == {
        "parent": True, "sibling": True,
        "root": False, "child": False, "grandchild": False,
        "foreign": False, "dormant": False, "orphan": False,
    }


async def test_every_revoked_key_leaves_the_streaming_cache(world):
    keys = await _tree(world)
    await _revoke(world, keys)
    cached = {
        name: bool(await world.redis.exists(f"{world.prefix}{_digest(raw)}"))
        for name, (_id, raw) in keys.items()
    }
    assert cached == {
        "parent": True, "sibling": True,
        # the dormant key's entry is left for its TTL: it was already inactive
        "dormant": True,
        "root": False, "child": False, "grandchild": False, "foreign": False, "orphan": False,
    }


async def test_the_cache_is_dropped_only_after_the_revocation_commits(world, monkeypatch):
    """Dropped before the commit, a request in between re-cached the key from
    a row that still said active."""
    keys = await _tree(world)
    hashes = {_digest(raw): name for name, (_id, raw) in keys.items()}
    # Every call is kept: a key forgotten before the commit AND again after
    # would otherwise hide the early call behind the later one.
    seen: list[tuple[str, bool]] = []
    import app.routers.api_keys as api_keys_router
    real_forget = api_keys_router.forget_streaming_key_hash

    async def _forget(key_hash):
        name = hashes[key_hash]
        async with world.sessions() as db:
            active = bool((await db.execute(
                select(world.ApiKey.is_active).where(world.ApiKey.id == keys[name][0])
            )).scalar_one())
        seen.append((name, active))
        await real_forget(key_hash)

    monkeypatch.setattr(api_keys_router, "forget_streaming_key_hash", _forget)
    await _revoke(world, keys)
    assert sorted(name for name, _ in seen) == sorted(
        ["root", "child", "grandchild", "foreign", "orphan"]
    )
    assert not any(active for _, active in seen), f"forgotten while still active: {seen}"


async def test_one_revoked_activity_row_per_key_naming_the_cascade(world):
    keys = await _tree(world)
    await _revoke(world, keys)
    Event = world.ProjectActivityEvent
    async with world.sessions() as db:
        rows = (await db.execute(
            select(Event.entity_id, Event.context)
            .where(Event.project_id == world.project, Event.event_type == "api_key.revoked")
        )).all()
    by_id = {str(keys[name][0]): name for name in keys}
    revoked = sorted(by_id[str(row.entity_id)] for row in rows)
    assert revoked == sorted(["root", "child", "grandchild", "foreign", "orphan"])
    for row in rows:
        name = by_id[str(row.entity_id)]
        if name == "root":
            assert "cascade_from" not in (row.context or {})
            assert row.context["revoked_descendants"] == 4
        else:
            assert row.context["cascade_from"] == str(keys["root"][0]), (name, row.context)


async def test_a_revoked_descendant_can_no_longer_authenticate(world):
    keys = await _tree(world)
    await _revoke(world, keys)
    for name in ("grandchild", "foreign", "orphan"):
        resp = await world.client.get("/api/v1/keys", headers={"X-API-Key": keys[name][1]})
        assert resp.status_code == 401, (name, resp.status_code, resp.text)
    resp = await world.client.get("/api/v1/keys", headers={"X-API-Key": keys["sibling"][1]})
    assert resp.status_code == 200, resp.text
