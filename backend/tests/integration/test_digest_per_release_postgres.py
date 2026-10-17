"""W09 NTF-03 against a real database: a Per Release digest names a release and delivers.

Through the real app (signed JWTs, no auth override): ``POST
/api/v1/digests/subscriptions`` binds a ``PER_RELEASE`` digest to a release
of ITS project only, and answers the release's name as ``scope_label``. Then
the real event-driven loop of ``dispatch_ai_summary_email`` stages the digest
for a run whose primary release is R1, for a run linked to R1 only by a
SECONDARY ``release_test_run_links`` row, and not for a run of R2.

Only ``get_db`` and ``AsyncSessionLocal`` point at this test's engine. The
task's coroutine is awaited on this test's loop (``_run_async`` captures it),
so the asyncpg connections never cross an event loop.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


@pytest.fixture
async def world(monkeypatch):
    redis_asyncio = pytest.importorskip("redis.asyncio")
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        DigestSubscription,
        LaunchStatus,
        NotificationLog,
        Project,
        ProjectMember,
        Release,
        ReleaseTestRunLink,
        TestRun,
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
    P, Q = uuid.uuid4(), uuid.uuid4()
    R1, R2, RQ = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    member = uuid.uuid4()
    run_primary, run_linked, run_r2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with sessions() as db:
        for pid, letter in ((P, "p"), (Q, "q")):
            db.add(Project(id=pid, name=f"w09b-{letter}-{tag}", slug=f"w09b-{letter}-{tag}", is_active=True))
        db.add(User(
            id=member, email=f"w09b-member-{tag}@example.com", username=f"w09b_member_{tag}",
            full_name="W09B member", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value,
        ))
        await db.flush()
        db.add(ProjectMember(user_id=member, project_id=P, role=UserRole.QA_ENGINEER.value))
        db.add(Release(id=R1, project_id=P, name=f"2026.10-{tag}", version="2026.10", status="in_progress"))
        db.add(Release(id=R2, project_id=P, name=f"2026.11-{tag}", version="2026.11", status="planning"))
        db.add(Release(id=RQ, project_id=Q, name=f"other-{tag}", version="9.0", status="planning"))
        await db.flush()
        for run_id, primary in ((run_primary, R1), (run_linked, R2), (run_r2, R2)):
            db.add(TestRun(
                id=run_id, project_id=P, build_number=f"w09b-{tag}-{run_id.hex[:6]}", jenkins_job="w09b",
                status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=4, failed_tests=2,
                pass_rate=50.0, primary_release_id=primary, primary_suite_name="checkout",
                created_at=now - timedelta(minutes=5),
            ))
        await db.flush()
        db.add(ReleaseTestRunLink(release_id=R1, test_run_id=run_primary, is_primary=True))
        db.add(ReleaseTestRunLink(release_id=R2, test_run_id=run_linked, is_primary=True))
        # The cherry-pick: verified against R1 too, by a SECONDARY link only.
        db.add(ReleaseTestRunLink(release_id=R1, test_run_id=run_linked, is_primary=False))
        db.add(ReleaseTestRunLink(release_id=R2, test_run_id=run_r2, is_primary=True))
        await db.commit()
    await redis.delete(f"membership:{member}")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, P=P, Q=Q, R1=R1, R2=R2, RQ=RQ, member=member,
        jwt={"Authorization": f"Bearer {create_access_token(str(member))}"},
        run_primary=run_primary, run_linked=run_linked, run_r2=run_r2,
        models=SimpleNamespace(NotificationLog=NotificationLog, DigestSubscription=DigestSubscription),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        runs = [run_primary, run_linked, run_r2]
        for statement in (
            delete(NotificationLog).where(NotificationLog.user_id == member),
            delete(NotificationLog).where(NotificationLog.run_id.in_(runs)),
            delete(DigestSubscription).where(DigestSubscription.user_id == member),
            delete(ReleaseTestRunLink).where(ReleaseTestRunLink.test_run_id.in_(runs)),
            delete(TestRun).where(TestRun.id.in_(runs)),
            delete(Release).where(Release.id.in_([R1, R2, RQ])),
            delete(ProjectMember).where(ProjectMember.user_id == member),
            delete(User).where(User.id == member),
            delete(Project).where(Project.id.in_([P, Q])),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"w09 G-B per-release teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.delete(f"membership:{member}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


def _subscribe(world, release_id):
    return world.client.post("/api/v1/digests/subscriptions", headers=world.jwt, json={
        "project_id": str(world.P), "name": f"per release {world.tag}", "schedule": "PER_RELEASE",
        "scope_type": "release", "scope_value": str(release_id), "channel": "email",
    })


async def _run_the_loop(world, monkeypatch, run_id):
    """The REAL event-driven digest loop of ``dispatch_ai_summary_email`` for one run."""
    from app.services.notification import manager
    from app.worker import tasks

    captured: list = []
    monkeypatch.setattr(tasks, "_run_async", captured.append)
    monkeypatch.setattr(manager, "dispatch_ai_summary_notifications", AsyncMock())
    monkeypatch.setattr("app.services.auto_tagging_service.auto_tag_after_analysis", AsyncMock())
    tasks.dispatch_ai_summary_email.run(
        str(run_id), str(world.P), "42", None, None, "deterministic summary", None, False,
    )
    [coroutine] = captured
    await coroutine


async def _digest_rows(world, run_id, sub_id):
    from app.services.notification.manager import _EXPLICIT_ROUTE_METADATA_KEY

    Log = world.models.NotificationLog
    async with world.sessions() as db:
        rows = (await db.execute(
            select(Log.event_type, Log.status, Log.delivery_metadata).where(
                Log.run_id == run_id, Log.user_id == world.member,
            )
        )).all()
    return [
        (event, status)
        for event, status, metadata in rows
        if (metadata or {}).get(_EXPLICIT_ROUTE_METADATA_KEY, {}).get("digest_subscription_id") == str(sub_id)
    ]


async def test_a_per_release_digest_binds_a_release_of_its_project_only(world):
    foreign = await _subscribe(world, world.RQ)
    assert (foreign.status_code, foreign.json()["detail"]) == (422, "Choose a release of this project.")

    created = await _subscribe(world, world.R1)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["scope_type"], body["scope_value"]) == ("release", str(world.R1))
    assert (body["scope_label"], body["scope_problem"]) == (f"2026.10-{world.tag}", None)

    listed = await world.client.get("/api/v1/digests/subscriptions", headers=world.jwt)
    assert listed.status_code == 200
    [row] = [item for item in listed.json() if item["id"] == body["id"]]
    assert row["scope_label"] == f"2026.10-{world.tag}"


async def test_the_digest_follows_primary_and_secondary_links_and_nothing_else(world, monkeypatch):
    created = await _subscribe(world, world.R1)
    assert created.status_code == 201, created.text
    sub_id = created.json()["id"]

    await _run_the_loop(world, monkeypatch, world.run_primary)
    await _run_the_loop(world, monkeypatch, world.run_linked)
    await _run_the_loop(world, monkeypatch, world.run_r2)

    assert await _digest_rows(world, world.run_primary, sub_id) == [("ai_analysis_complete", "pending")]
    assert await _digest_rows(world, world.run_linked, sub_id) == [("ai_analysis_complete", "pending")], (
        "a run attached to the release by a secondary link did not deliver its Per Release digest"
    )
    assert await _digest_rows(world, world.run_r2, sub_id) == []
