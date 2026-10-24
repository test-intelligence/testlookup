"""W11 GATE-08b against a real database: digests carry the release-level verdict.

* The weekly digest of a project whose ACTIVE release is evaluated NO_GO (a
  current release-level ``release_gate_decisions`` row with two reasons) reads
  ``Release 2026.11 — No-Go (2 blockers)`` -- in the text, in the HTML and in
  the preview route's JSON (``GET /api/v1/digests/preview``, a member's JWT).
* A phase verdict and a superseded verdict of the same release are NOT the
  release's current verdict (the statement's ON clause).
* A PER_RELEASE subscription for ANOTHER release of the project, through the
  real event-driven loop of ``dispatch_ai_summary_email``: its staged delivery's
  metadata names THAT release (never evaluated: ``verdict`` None).

Only ``get_db`` and ``AsyncSessionLocal`` point at this test's engine; the
task's coroutine is awaited on this test's loop.

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
        ReleaseGateDecision,
        ReleasePhase,
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
    P = uuid.uuid4()
    R_ACTIVE, R_OTHER = uuid.uuid4(), uuid.uuid4()
    PHASE = uuid.uuid4()
    member = uuid.uuid4()
    run_other = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with sessions() as db:
        db.add(Project(id=P, name=f"w11c-{tag}", slug=f"w11c-{tag}", is_active=True))
        db.add(User(
            id=member, email=f"w11c-member-{tag}@example.com", username=f"w11c_member_{tag}",
            full_name="W11C member", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value,
        ))
        await db.flush()
        db.add(ProjectMember(user_id=member, project_id=P, role=UserRole.QA_ENGINEER.value))
        db.add(Release(id=R_ACTIVE, project_id=P, name=f"2026.11-{tag}", version="2026.11",
                       status="in_progress", is_active=True))
        db.add(Release(id=R_OTHER, project_id=P, name=f"2026.10-{tag}", version="2026.10", status="planning"))
        await db.flush()
        db.add(ReleasePhase(id=PHASE, release_id=R_ACTIVE, name="Regression", phase_type="qa_testing",
                            status="pending", order_index=0))
        await db.flush()
        # Neither of these is the release's current verdict. Written FIRST, so a
        # statement that lost its ON predicates would meet them before the
        # current row.
        db.add(ReleaseGateDecision(
            release_id=R_ACTIVE, verdict="GO", is_current=False, blocking_reasons=[],
            created_at=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc),
        ))
        db.add(ReleaseGateDecision(
            release_id=R_ACTIVE, phase_id=PHASE, verdict="GO", is_current=True, blocking_reasons=[],
        ))
        await db.flush()
        # The CURRENT release-level verdict: NO_GO with two reasons.
        db.add(ReleaseGateDecision(
            release_id=R_ACTIVE, verdict="NO_GO", is_current=True,
            blocking_reasons=["Open critical defect", "Pass rate 81% below 90%"],
            created_at=datetime(2026, 10, 21, 14, 5, tzinfo=timezone.utc),
        ))
        db.add(TestRun(
            id=run_other, project_id=P, build_number=f"w11c-{tag}", jenkins_job="w11c",
            status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=4, failed_tests=2,
            pass_rate=50.0, primary_release_id=R_OTHER, primary_suite_name="checkout",
            created_at=now - timedelta(minutes=5),
        ))
        await db.flush()
        db.add(ReleaseTestRunLink(release_id=R_OTHER, test_run_id=run_other, is_primary=True))
        await db.commit()
    await redis.delete(f"membership:{member}")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, P=P, R_ACTIVE=R_ACTIVE, R_OTHER=R_OTHER,
        member=member, run_other=run_other,
        jwt={"Authorization": f"Bearer {create_access_token(str(member))}"},
        models=SimpleNamespace(NotificationLog=NotificationLog),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(NotificationLog).where(NotificationLog.user_id == member),
            delete(NotificationLog).where(NotificationLog.run_id == run_other),
            delete(DigestSubscription).where(DigestSubscription.user_id == member),
            delete(ReleaseTestRunLink).where(ReleaseTestRunLink.test_run_id == run_other),
            delete(TestRun).where(TestRun.id == run_other),
            delete(ReleaseGateDecision).where(ReleaseGateDecision.release_id.in_([R_ACTIVE, R_OTHER])),
            delete(ReleasePhase).where(ReleasePhase.id == PHASE),
            delete(Release).where(Release.id.in_([R_ACTIVE, R_OTHER])),
            delete(ProjectMember).where(ProjectMember.user_id == member),
            delete(User).where(User.id == member),
            delete(Project).where(Project.id == P),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"w11 G-C release-verdict teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.delete(f"membership:{member}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def test_the_weekly_digest_names_the_active_releases_current_verdict(world):
    from app.services import digest_content_service as dcs

    async with world.sessions() as db:
        digest = await dcs.generate_digest(db, world.P, "weekly", with_release_verdict=True)
        plain = await dcs.generate_digest(db, world.P, "weekly")

    rv = digest["release_verdict"]
    assert (rv["release_id"], rv["source"], rv["verdict"], rv["blocker_count"]) == (
        str(world.R_ACTIVE), "active", "NO_GO", 2,
    )
    assert rv["decided_at"] == "2026-10-21T14:05:00+00:00"
    line = f"Release 2026.11-{world.tag} — No-Go (2 blockers) · decided 2026-10-21 14:05 UTC"
    assert line in dcs.render_digest_text(digest)
    assert line in dcs.render_digest_html(digest)
    assert "release_verdict" not in plain


async def test_the_preview_route_returns_the_verdict(world):
    resp = await world.client.get(
        "/api/v1/digests/preview", headers=world.jwt, params={"project_id": str(world.P), "period": "weekly"},
    )
    assert resp.status_code == 200, resp.text
    rv = resp.json()["release_verdict"]
    assert (rv["release_name"], rv["verdict"], rv["blocker_count"], rv["source"]) == (
        f"2026.11-{world.tag}", "NO_GO", 2, "active",
    )


async def test_a_per_release_delivery_names_its_own_release(world, monkeypatch):
    from app.services.notification import manager
    from app.services.notification.manager import _EXPLICIT_ROUTE_METADATA_KEY
    from app.worker import tasks

    created = await world.client.post("/api/v1/digests/subscriptions", headers=world.jwt, json={
        "project_id": str(world.P), "name": f"per release {world.tag}", "schedule": "PER_RELEASE",
        "scope_type": "release", "scope_value": str(world.R_OTHER), "channel": "email",
    })
    assert created.status_code == 201, created.text
    sub_id = created.json()["id"]

    captured: list = []
    monkeypatch.setattr(tasks, "_run_async", captured.append)
    monkeypatch.setattr(manager, "dispatch_ai_summary_notifications", AsyncMock())
    monkeypatch.setattr("app.services.auto_tagging_service.auto_tag_after_analysis", AsyncMock())
    tasks.dispatch_ai_summary_email.run(
        str(world.run_other), str(world.P), "42", None, None, "deterministic summary", None, False,
    )
    [coroutine] = captured
    await coroutine

    Log = world.models.NotificationLog
    async with world.sessions() as db:
        rows = (await db.execute(
            select(Log.body, Log.delivery_metadata).where(
                Log.run_id == world.run_other, Log.user_id == world.member,
            )
        )).all()
    mine = [
        (body, metadata) for body, metadata in rows
        if (metadata or {}).get(_EXPLICIT_ROUTE_METADATA_KEY, {}).get("digest_subscription_id") == sub_id
    ]
    assert len(mine) == 1
    body, metadata = mine[0]
    rv = metadata["release_verdict"]
    assert (rv["release_id"], rv["release_name"], rv["source"], rv["verdict"]) == (
        str(world.R_OTHER), f"2026.10-{world.tag}", "subscription", None,
    )
    assert body == "deterministic summary"
