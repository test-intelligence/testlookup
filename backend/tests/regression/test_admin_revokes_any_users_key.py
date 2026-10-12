"""An ADMIN revokes any user's API key; everyone else only their own.

Open-source docs pass, 2026-10-11. The Streaming API keys page lists every
key bound to the project for an admin and offers Revoke on each one.
``require_api_key_owner`` let the admin through, and then the handler looked
the key up with ``user_id == current_user.id`` and answered 404 for every key
the admin did not own: a leaked CI key minted by someone else could not be
killed from the page meant for it.

Pinned here, against a real (in-memory SQLite) database:

* an ADMIN revokes another user's key: the row goes inactive, and an
  ``API_KEY_REVOKED`` identity event names the admin as actor and the owner
  as the user;
* a QA engineer revokes their own key (and the same event is written);
* a QA engineer cannot revoke someone else's key: the guard refuses with 403,
  and the handler itself does not find it either (404), so neither layer
  alone is load-bearing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import (
    ApiKey,
    Base,
    IdentityEvent,
    IdentityEventType,
    Project,
    ProjectActivityEvent,
    User,
    UserRole,
)
from app.routers import api_keys as router

pytestmark = pytest.mark.regression

_TABLES = [
    Project.__table__,
    User.__table__,
    ApiKey.__table__,
    IdentityEvent.__table__,
    ProjectActivityEvent.__table__,
]


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect, coltype):  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _user(name: str, role: UserRole) -> User:
    return User(
        id=uuid.uuid4(), email=f"{name}@example.com", username=name,
        full_name=name.title(), hashed_password="x", role=role.value,
    )


@pytest.fixture
async def world(session_factory, monkeypatch):
    project = Project(id=uuid.uuid4(), name="Checkout", slug="checkout")
    admin = _user("ada", UserRole.ADMIN)
    owner = _user("owen", UserRole.QA_ENGINEER)
    other = _user("olga", UserRole.QA_ENGINEER)
    key = ApiKey(
        id=uuid.uuid4(), user_id=owner.id, project_id=project.id, name="ci-stream",
        key_hash="h-" + uuid.uuid4().hex, key_hint="qai_abcd", scopes=[], is_active=True,
    )
    async with session_factory() as s:
        s.add_all([project, admin, owner, other])
        await s.flush()
        s.add(key)
        await s.commit()
    # The live-event credential cache is Redis; nothing here needs it. The
    # minted-key cascade is PostgreSQL SQL (a recursive CTE driving an
    # UPDATE ... RETURNING through table aliases, which SQLite cannot run);
    # it has its own Postgres test (test_key_revoke_cascade_postgres.py).
    monkeypatch.setattr(router, "forget_streaming_key_hash", AsyncMock())
    monkeypatch.setattr(router, "_revoke_subtree", AsyncMock(return_value=[]))
    return SimpleNamespace(project=project, admin=admin, owner=owner, other=other, key=key)


async def _revoke(session_factory, key_id, caller):
    async with session_factory() as db:
        return await router.revoke_api_key(key_id=key_id, db=db, current_user=caller, _=caller)


async def _guard(session_factory, key_id, caller):
    from app.core.deps import require_api_key_owner

    check = require_api_key_owner()
    request = SimpleNamespace(path_params={"key_id": str(key_id)})
    async with session_factory() as db:
        return await check(request=request, db=db, current_user=caller)


async def _key(session_factory, key_id) -> ApiKey:
    async with session_factory() as db:
        return (await db.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one()


async def _identity_events(session_factory) -> list[IdentityEvent]:
    async with session_factory() as db:
        return list((await db.execute(select(IdentityEvent))).scalars().all())


@pytest.mark.asyncio
async def test_an_admin_revokes_another_users_key(session_factory, world):
    # The guard lets the admin through ...
    assert await _guard(session_factory, world.key.id, world.admin) is world.admin
    # ... and the handler now finds the key instead of answering 404.
    await _revoke(session_factory, world.key.id, world.admin)

    assert (await _key(session_factory, world.key.id)).is_active is False
    events = await _identity_events(session_factory)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == IdentityEventType.API_KEY_REVOKED.value
    assert event.actor_id == world.admin.id
    assert event.actor_name == "ada"
    assert event.user_id == world.owner.id
    assert event.detail["owner_username"] == "owen"
    assert event.detail["revoked_by_owner"] is False
    assert event.detail["key_id"] == str(world.key.id)
    assert event.detail["project_id"] == str(world.project.id)


@pytest.mark.asyncio
async def test_an_owner_revokes_their_own_key(session_factory, world):
    assert await _guard(session_factory, world.key.id, world.owner) is world.owner
    await _revoke(session_factory, world.key.id, world.owner)

    assert (await _key(session_factory, world.key.id)).is_active is False
    (event,) = await _identity_events(session_factory)
    assert event.actor_id == world.owner.id
    assert event.user_id == world.owner.id
    assert event.detail["revoked_by_owner"] is True


@pytest.mark.asyncio
async def test_a_non_admin_cannot_revoke_someone_elses_key(session_factory, world):
    with pytest.raises(HTTPException) as guard:
        await _guard(session_factory, world.key.id, world.other)
    assert guard.value.status_code == 403

    # Defence in depth: the handler does not find another user's key for a
    # non-admin either, so dropping the guard would still not revoke it.
    with pytest.raises(HTTPException) as handler:
        await _revoke(session_factory, world.key.id, world.other)
    assert handler.value.status_code == 404

    assert (await _key(session_factory, world.key.id)).is_active is True
    assert await _identity_events(session_factory) == []


def test_the_identity_event_type_fits_its_column():
    assert len(IdentityEventType.API_KEY_REVOKED.value) <= 40
