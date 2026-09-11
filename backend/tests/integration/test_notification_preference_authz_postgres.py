"""QA-R3-1 against a real database: a notification preference is a subscription.

A row in ``notification_preferences`` for project P makes the notification
manager send P's "run failed" and transition notifications to the row's
``email_override`` or personal webhook. ``POST /api/v1/notifications/preferences``
accepted any ``project_id`` and the manager sent to every enabled preference of
the project, so a QA_ENGINEER with no membership in P (403 on
``GET /projects/P``), or any key bound to another project, subscribed to P's
failures at an address of its choosing.

Real app, real credentials (signed JWTs, an ``api_keys`` row), no auth
override. Only ``get_db`` and ``AsyncSessionLocal`` point at this test's
engine, and the app's own engine is disposed at the start and end so it
carries no connection from another file's closed event loop (QA-R3-14).

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
        NotificationLog,
        NotificationPreference,
        ProductUsageEvent,
        Project,
        ProjectMember,
        User,
        UserRole,
    )
    from app.services.notification import manager

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
    monkeypatch.setattr(manager, "AsyncSessionLocal", sessions)

    tag = uuid.uuid4().hex[:10]
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    outsider, member_b, ex_member, key_owner, disabled = (uuid.uuid4() for _ in range(5))
    raw_key = f"qai_{secrets.token_urlsafe(32)}"

    async with sessions() as db:
        for pid, letter in ((project_a, "a"), (project_b, "b")):
            db.add(Project(id=pid, name=f"r4n-{letter}-{tag}", slug=f"r4n-{letter}-{tag}", is_active=True))
        for uid, role, name, active in (
            (outsider, UserRole.QA_ENGINEER, "outsider", True),
            (member_b, UserRole.QA_ENGINEER, "member", True),
            (ex_member, UserRole.QA_ENGINEER, "ex", True),
            (key_owner, UserRole.ADMIN, "owner", True),
            # Still a member of B, but the account is disabled: no access at all.
            (disabled, UserRole.QA_ENGINEER, "disabled", False),
        ):
            db.add(User(
                id=uid, email=f"r4n-{name}-{tag}@example.com", username=f"r4n_{name}_{tag}",
                full_name=f"R4 {name}", hashed_password="!unusable", role=role.value,
                is_active=active,
            ))
        await db.flush()
        for uid, pid in (
            (outsider, project_a), (member_b, project_b), (ex_member, project_b), (disabled, project_b),
        ):
            db.add(ProjectMember(user_id=uid, project_id=pid, role=UserRole.QA_ENGINEER.value))
        db.add(ApiKey(
            id=uuid.uuid4(), user_id=key_owner, name="team-a ci",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(), key_hint=raw_key[:8] + "...",
            scopes=[], project_id=project_a,
        ))
        await db.commit()
    for uid in (outsider, member_b, ex_member, key_owner):
        await redis.delete(f"membership:{uid}")

    def jwt(uid):
        return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

    async def leave(uid, pid):
        """Remove a membership the way the app does: row gone, cache dropped."""
        async with sessions.begin() as db:
            await db.execute(delete(ProjectMember).where(
                ProjectMember.user_id == uid, ProjectMember.project_id == pid,
            ))
        await redis.delete(f"membership:{uid}")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, manager=manager, leave=leave,
        project_a=project_a, project_b=project_b, disabled=disabled,
        outsider=outsider, member_b=member_b, ex_member=ex_member, key_owner=key_owner,
        outsider_jwt=jwt(outsider), member_jwt=jwt(member_b), ex_jwt=jwt(ex_member),
        key={"X-API-Key": raw_key},
        models=SimpleNamespace(
            NotificationLog=NotificationLog, NotificationPreference=NotificationPreference,
            ProductUsageEvent=ProductUsageEvent,
        ),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        projects = [project_a, project_b]
        users = [outsider, member_b, ex_member, key_owner, disabled]
        for statement in (
            delete(NotificationLog).where(NotificationLog.user_id.in_(users)),
            delete(NotificationPreference).where(NotificationPreference.user_id.in_(users)),
            delete(ProductUsageEvent).where(ProductUsageEvent.user_id.in_(users)),
            delete(AccessAuditLog).where(AccessAuditLog.actor_user_id.in_(users)),
            delete(ApiKey).where(ApiKey.user_id.in_(users)),
            delete(ProjectMember).where(ProjectMember.user_id.in_(users)),
            delete(User).where(User.id.in_(users)),
            delete(Project).where(Project.id.in_(projects)),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"r4 notification teardown: {type(exc).__name__}: {str(exc)[:160]}")
        for uid in users:
            await redis.delete(f"membership:{uid}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _preferences(world, user_id):
    Pref = world.models.NotificationPreference
    async with world.sessions() as db:
        return (await db.execute(
            select(Pref.project_id, Pref.email_override).where(Pref.user_id == user_id)
        )).all()


def _subscribe(world, headers, project_id, email="attacker@evil.example"):
    return world.client.post("/api/v1/notifications/preferences", headers=headers, json={
        "project_id": None if project_id is None else str(project_id),
        "channel": "email", "email_override": email,
    })


async def _stage_run_failed(world, project_id, scope):
    from app.models.postgres import NotificationEventType

    async with world.sessions() as db:
        await world.manager.stage_scoped_preference_deliveries(
            db, project_id=project_id, run_id=None,
            events=[NotificationEventType.RUN_FAILED],
            build_title_fn=lambda _event: (f"B run failed {world.tag}", "B's failure text"),
            metadata={"pass_rate": 0.0}, delivery_scope=scope,
        )
        await db.commit()


async def _recipients(world, project_id):
    Log = world.models.NotificationLog
    async with world.sessions() as db:
        return set((await db.execute(
            select(Log.user_id).where(Log.project_id == project_id)
        )).scalars().all())


# ── writing a subscription ───────────────────────────────────────────────────


async def test_a_non_member_cannot_subscribe_to_another_projects_notifications(world):
    cannot_read = await world.client.get(f"/api/v1/projects/{world.project_b}", headers=world.outsider_jwt)
    resp = await _subscribe(world, world.outsider_jwt, world.project_b)

    assert cannot_read.status_code == 403, cannot_read.text  # the precondition QA used
    assert resp.status_code == 403, resp.text
    assert await _preferences(world, world.outsider) == []


async def test_a_bound_key_subscribes_only_for_its_own_project(world):
    other = await _subscribe(world, world.key, world.project_b)
    every_project = await _subscribe(world, world.key, None)
    own = await _subscribe(world, world.key, world.project_a, email="ci-owner@example.com")

    assert other.status_code == 403, other.text
    assert every_project.status_code == 403, every_project.text
    assert own.status_code == 201, own.text
    assert await _preferences(world, world.key_owner) == [(world.project_a, "ci-owner@example.com")]


async def test_a_member_subscribes_to_its_own_project(world):
    resp = await _subscribe(world, world.member_jwt, world.project_b, email="member@example.com")

    assert resp.status_code == 201, resp.text
    assert await _preferences(world, world.member_b) == [(world.project_b, "member@example.com")]


async def test_an_ex_member_cannot_rearm_a_stale_subscription(world):
    created = await _subscribe(world, world.ex_jwt, world.project_b, email="ex@example.com")
    assert created.status_code == 201, created.text
    await world.leave(world.ex_member, world.project_b)

    # No project_id in the body: update never rewrites it, so the check must be
    # on the STORED project. Authorizing the body's (absent) project would pass.
    rearmed = await world.client.put(
        f"/api/v1/notifications/preferences/{created.json()['id']}", headers=world.ex_jwt,
        json={"channel": "email", "email_override": "moved@evil.example"},
    )

    assert rearmed.status_code == 403, rearmed.text
    assert await _preferences(world, world.ex_member) == [(world.project_b, "ex@example.com")]


async def test_a_bound_key_lists_only_its_own_projects_preferences(world):
    Pref = world.models.NotificationPreference
    async with world.sessions.begin() as db:
        db.add(Pref(user_id=world.key_owner, project_id=world.project_b, channel="email",
                    enabled=True, events=["run_failed"], slack_webhook_url=None))
        db.add(Pref(user_id=world.key_owner, project_id=world.project_a, channel="email",
                    enabled=True, events=["run_failed"]))

    listed = await world.client.get("/api/v1/notifications/preferences", headers=world.key)

    assert listed.status_code == 200, listed.text
    assert {p["project_id"] for p in listed.json()} == {str(world.project_a)}


# ── sending: only to someone who can still read the project ─────────────────


async def test_a_stale_or_all_projects_preference_is_not_delivered(world):
    """Send time is the second line: rows written before the fix, or by a
    member who has since left, and "all projects" rows, which match every
    project whoever wrote them."""
    assert (await _subscribe(world, world.member_jwt, world.project_b)).status_code == 201
    assert (await _subscribe(world, world.ex_jwt, world.project_b)).status_code == 201
    # "All projects" is still allowed to write: it means every project I can see.
    assert (await _subscribe(world, world.outsider_jwt, None)).status_code == 201
    await world.leave(world.ex_member, world.project_b)
    Pref = world.models.NotificationPreference
    async with world.sessions.begin() as db:
        # A row written before the fix, straight into the table.
        db.add(Pref(user_id=world.outsider, project_id=world.project_b, channel="email",
                    enabled=True, events=["run_failed"], email_override="attacker@evil.example"))
        # A member whose account was disabled after subscribing.
        db.add(Pref(user_id=world.disabled, project_id=world.project_b, channel="email",
                    enabled=True, events=["run_failed"]))

    await _stage_run_failed(world, world.project_b, f"r4-stage-{world.tag}")

    assert await _recipients(world, world.project_b) == {world.member_b}


async def test_an_instance_admins_all_projects_preference_is_still_delivered(world):
    """ADMIN reads every project in the app, so it is notified of every project."""
    Pref = world.models.NotificationPreference
    async with world.sessions.begin() as db:
        db.add(Pref(user_id=world.key_owner, project_id=None, channel="email",
                    enabled=True, events=["run_failed"]))

    await _stage_run_failed(world, world.project_b, f"r4-admin-{world.tag}")

    assert await _recipients(world, world.project_b) == {world.key_owner}


async def test_the_relay_does_not_deliver_to_a_recipient_who_left(world, monkeypatch):
    """Staged while both were members; one left before the provider call."""
    from unittest.mock import AsyncMock

    assert (await _subscribe(world, world.member_jwt, world.project_b)).status_code == 201
    assert (await _subscribe(world, world.ex_jwt, world.project_b)).status_code == 201
    await _stage_run_failed(world, world.project_b, f"r4-relay-{world.tag}")
    assert await _recipients(world, world.project_b) == {world.member_b, world.ex_member}
    await world.leave(world.ex_member, world.project_b)

    delivered: list = []

    async def _dispatch(pref, *_args, **_kwargs):
        delivered.append(pref.user_id)
        return "sent", None

    monkeypatch.setattr(world.manager, "_dispatch_to_channel", _dispatch)
    monkeypatch.setattr(world.manager.email_service, "_get_smtp_cfg", AsyncMock(return_value={}))

    await world.manager.relay_pending_notification_deliveries()

    assert world.ex_member not in delivered
    assert world.member_b in delivered
    Log = world.models.NotificationLog
    async with world.sessions() as db:
        rows = dict((await db.execute(
            select(Log.user_id, Log.status).where(Log.project_id == world.project_b)
        )).all())
        ex_error = (await db.execute(
            select(Log.error_detail).where(Log.user_id == world.ex_member)
        )).scalar_one()
    assert rows[world.member_b] == "sent"
    assert rows[world.ex_member] != "sent"
    assert "no longer access" in ex_error


async def test_the_relay_rechecks_an_all_projects_preference_too(world, monkeypatch):
    """An "all projects" row is staged under the run's project; its owner can
    leave that project before the send like anyone else (QA round 4, MB)."""
    from unittest.mock import AsyncMock

    assert (await _subscribe(world, world.ex_jwt, None, email="ex-all@example.com")).status_code == 201
    await _stage_run_failed(world, world.project_b, f"r4-relay-all-{world.tag}")
    assert world.ex_member in await _recipients(world, world.project_b)
    await world.leave(world.ex_member, world.project_b)

    delivered: list = []

    async def _dispatch(pref, *_args, **_kwargs):
        delivered.append(pref.user_id)
        return "sent", None

    monkeypatch.setattr(world.manager, "_dispatch_to_channel", _dispatch)
    monkeypatch.setattr(world.manager.email_service, "_get_smtp_cfg", AsyncMock(return_value={}))

    await world.manager.relay_pending_notification_deliveries()

    assert world.ex_member not in delivered
    Log = world.models.NotificationLog
    async with world.sessions() as db:
        status_ = (await db.execute(
            select(Log.status).where(Log.user_id == world.ex_member, Log.project_id == world.project_b)
        )).scalar_one()
    assert status_ != "sent"


# ── QA-R3-2: onboarding usage events are filed under a project too ──────────


async def _usage_events(world, project_id):
    Event = world.models.ProductUsageEvent
    async with world.sessions() as db:
        return (await db.execute(
            select(Event.user_id).where(Event.project_id == project_id)
        )).scalars().all()


def _track(world, headers, project_id):
    return world.client.post("/api/v1/onboarding/track", headers=headers, json={
        "event_name": f"r4.probe.{world.tag}", "project_id": project_id,
    })


async def test_a_usage_event_is_filed_only_under_a_project_the_caller_can_access(world):
    outsider = await _track(world, world.outsider_jwt, str(world.project_b))
    other_project_key = await _track(world, world.key, str(world.project_b))
    member = await _track(world, world.member_jwt, str(world.project_b))
    own_project_key = await _track(world, world.key, str(world.project_a))

    assert outsider.status_code == 403, outsider.text
    assert other_project_key.status_code == 403, other_project_key.text
    assert member.status_code == 200, member.text
    assert own_project_key.status_code == 200, own_project_key.text
    assert await _usage_events(world, world.project_b) == [world.member_b]
    assert await _usage_events(world, world.project_a) == [world.key_owner]


async def test_a_usage_event_without_a_usable_project_is_still_tracked(world):
    """Fire-and-forget as before: no project, or an unparseable one, files the
    event under no project rather than failing."""
    none = await _track(world, world.outsider_jwt, None)
    garbage = await _track(world, world.outsider_jwt, "not-a-uuid")

    assert none.status_code == 200, none.text
    assert garbage.status_code == 200, garbage.text
    Event = world.models.ProductUsageEvent
    async with world.sessions() as db:
        filed = (await db.execute(
            select(Event.project_id).where(Event.user_id == world.outsider)
        )).scalars().all()
    assert filed == [None, None]


async def test_counting_helper_sees_rows(world):
    """Guards the guard: the recipient query above reads the table it claims to."""
    Log = world.models.NotificationLog
    assert (await _subscribe(world, world.member_jwt, world.project_b)).status_code == 201
    await _stage_run_failed(world, world.project_b, f"r4-count-{world.tag}")
    async with world.sessions() as db:
        count = (await db.execute(
            select(func.count()).select_from(Log).where(Log.project_id == world.project_b)
        )).scalar_one()
    assert count == 1
