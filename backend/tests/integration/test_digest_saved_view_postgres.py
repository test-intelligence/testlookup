"""W09 NTF-04 against a real database: a saved report view filters the digest.

Seeded project P (every row with its OWN time -- ``created_at`` is never left
to ``now()``):

====================  ===========  ===================  ========  =======
run                   age          suite (row / label)  release   fails
====================  ===========  ===================  ========  =======
``run_checkout``      1 day        checkout / checkout  R1        fp-a
``run_payments``      1 day        payments / payments  R1        fp-b
``run_old``           5 days       checkout / checkout  R1        fp-c
``run_live``          2 days       (none) / checkout    R2        fp-d
====================  ===========  ===================  ========  =======

``run_live`` is a ``live_stream`` run whose rows carry no suite: its EFFECTIVE
suite is its label, so a ``checkout`` view counts it. A view
``{suites: ['checkout'], window: 3}`` therefore sees run_checkout and run_live
(2 runs, new failures fp-a and fp-d); the unfiltered weekly digest of the same
data sees all four. A release view keeps only runs whose
``primary_release_id`` is in it, and drops another project's release. Deleting
the view nulls ``saved_view_id`` (``ON DELETE SET NULL``) while the name
snapshot (0221) stays, and the next scheduled send says the view was deleted.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and ``REDIS_URL``, migrated to head.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, update
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
        SavedView,
        TestCase,
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
    now = datetime.now(timezone.utc)
    runs = {name: uuid.uuid4() for name in ("checkout", "payments", "old", "live")}
    seed = (
        # name, age, run label, trigger, release, row suite, fingerprint
        ("checkout", timedelta(days=1), "checkout", "push", R1, "checkout", "fp-a"),
        ("payments", timedelta(days=1), "payments", "push", R1, "payments", "fp-b"),
        ("old", timedelta(days=5), "checkout", "push", R1, "checkout", "fp-c"),
        ("live", timedelta(days=2), "checkout", "live_stream", R2, None, "fp-d"),
    )

    async with sessions() as db:
        for pid, letter in ((P, "p"), (Q, "q")):
            db.add(Project(id=pid, name=f"w09bv-{letter}-{tag}", slug=f"w09bv-{letter}-{tag}", is_active=True))
        db.add(User(
            id=member, email=f"w09bv-member-{tag}@example.com", username=f"w09bv_member_{tag}",
            full_name="W09B view owner", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value,
        ))
        await db.flush()
        db.add(ProjectMember(user_id=member, project_id=P, role=UserRole.QA_ENGINEER.value))
        db.add(Release(id=R1, project_id=P, name=f"2026.10-{tag}", version="2026.10"))
        db.add(Release(id=R2, project_id=P, name=f"2026.11-{tag}", version="2026.11"))
        db.add(Release(id=RQ, project_id=Q, name=f"other-{tag}", version="9.0"))
        await db.flush()
        for name, age, label, trigger, release_id, row_suite, fingerprint in seed:
            at = now - age
            db.add(TestRun(
                id=runs[name], project_id=P, build_number=f"w09bv-{tag}-{name}", jenkins_job="w09bv",
                trigger_source=trigger, status=LaunchStatus.FAILED, ingestion_source="unknown",
                total_tests=1, failed_tests=1, pass_rate=0.0, primary_release_id=release_id,
                primary_suite_name=label, created_at=at,
            ))
            await db.flush()
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=runs[name], test_name=f"test_{fingerprint}", suite_name=row_suite,
                status="FAILED", test_fingerprint=f"{fingerprint}-{tag}", created_at=at,
            ))
        await db.commit()
    await redis.delete(f"membership:{member}")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    world = SimpleNamespace(
        client=client, sessions=sessions, tag=tag, P=P, Q=Q, R1=R1, R2=R2, RQ=RQ, member=member, now=now,
        jwt={"Authorization": f"Bearer {create_access_token(str(member))}"},
        models=SimpleNamespace(
            DigestSubscription=DigestSubscription, NotificationLog=NotificationLog, SavedView=SavedView,
        ),
    )
    try:
        yield world
    finally:
        await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            delete(NotificationLog).where(NotificationLog.user_id == member),
            delete(DigestSubscription).where(DigestSubscription.user_id == member),
            delete(SavedView).where(SavedView.user_id == member),
            delete(TestRun).where(TestRun.id.in_(list(runs.values()))),
            delete(Release).where(Release.id.in_([R1, R2, RQ])),
            delete(ProjectMember).where(ProjectMember.user_id == member),
            delete(User).where(User.id == member),
            delete(Project).where(Project.id.in_([P, Q])),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"w09 G-B saved-view teardown: {type(exc).__name__}: {str(exc)[:160]}")
        await redis.delete(f"membership:{member}")
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _save_view(world, filters: dict, name="Checkout regressions") -> str:
    response = await world.client.post("/api/v1/saved-views", headers=world.jwt, json={
        "project_id": str(world.P), "name": name, "page": "trends", "is_shared": False,
        "filters": {"kind": "report_view", "page": "trends", **filters},
    })
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _digest(world, view_id: str | None):
    """``resolve_digest_scope`` + ``generate_digest`` exactly as the weekly send runs them."""
    from app.services.digest_content_service import generate_digest
    from app.services.digest_scope_service import resolve_digest_scope

    async with world.sessions() as db:
        scope = await resolve_digest_scope(
            db, project_id=world.P, owner_id=world.member,
            saved_view_id=uuid.UUID(view_id) if view_id else None, saved_view_name=None,
        )
        return await generate_digest(db, world.P, "weekly", since=world.now - timedelta(days=7), scope=scope)


async def test_a_suite_and_window_view_counts_only_its_runs_and_failures(world):
    view_id = await _save_view(world, {"suites": ["checkout"], "window": 3})

    filtered = await _digest(world, view_id)
    assert filtered["total_runs"] == 2, "checkout in the last 3 days: the push run and the live-stream run"
    assert filtered["delta"]["new_failures"] == 2
    assert sorted(filtered["delta"]["new_failures_top"]) == ["test_fp-a", "test_fp-d"]
    assert filtered["scope"]["suites"] == ["checkout"] and filtered["scope"]["window_days"] == 3

    whole = await _digest(world, None)
    assert (whole["total_runs"], whole["delta"]["new_failures"]) == (4, 4)
    assert "scope" not in whole

    # The preview route applies the same view (no watermark: the last 3 days).
    preview = await world.client.get(
        "/api/v1/digests/preview", headers=world.jwt,
        params={"project_id": str(world.P), "period": "weekly", "saved_view_id": view_id},
    )
    assert preview.status_code == 200, preview.text
    assert (preview.json()["total_runs"], preview.json()["scope"]["view_name"]) == (2, "Checkout regressions")


async def test_a_release_view_keeps_its_releases_runs_and_drops_a_foreign_one(world):
    view_id = await _save_view(world, {"release_ids": [str(world.R2), str(world.RQ)], "window": 7})
    filtered = await _digest(world, view_id)
    assert filtered["total_runs"] == 1
    assert filtered["delta"]["new_failures_top"] == ["test_fp-d"]
    assert filtered["scope"]["release_names"] == [f"2026.11-{world.tag}"]
    assert filtered["scope"]["dropped_release_count"] == 1


async def test_deleting_the_view_leaves_a_digest_that_says_so(world, monkeypatch):
    from app.services.notification import email_service
    from app.worker import tasks

    view_id = await _save_view(world, {"suites": ["checkout"], "window": 3})
    created = await world.client.post("/api/v1/digests/subscriptions", headers=world.jwt, json={
        "project_id": str(world.P), "name": f"weekly {world.tag}", "schedule": "WEEKLY",
        "channel": "email", "saved_view_id": view_id,
    })
    assert created.status_code == 201, created.text
    assert created.json()["saved_view_name"] == "Checkout regressions"
    sub_id = uuid.UUID(created.json()["id"])

    deleted = await world.client.delete(f"/api/v1/saved-views/{view_id}", headers=world.jwt)
    assert deleted.status_code in (200, 204), deleted.text

    Sub = world.models.DigestSubscription
    async with world.sessions() as db:
        row = (await db.execute(select(Sub.saved_view_id, Sub.saved_view_name).where(Sub.id == sub_id))).one()
        assert tuple(row) == (None, "Checkout regressions")
        await db.execute(update(Sub).where(Sub.id == sub_id).values(next_delivery_at=world.now - timedelta(hours=1)))
        await db.commit()

    listed = await world.client.get(f"/api/v1/digests/subscriptions/{sub_id}", headers=world.jwt)
    assert listed.json()["scope_problem"] == "saved_view_deleted"

    sent: list[dict] = []

    async def _send(**kwargs):
        sent.append(kwargs)

    captured: list = []
    monkeypatch.setattr(email_service, "get_smtp_config", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(email_service, "send_html_email_with_attachments", _send)
    monkeypatch.setattr(tasks, "_run_async", captured.append)
    tasks.dispatch_scheduled_digests.run()
    [coroutine] = captured
    await coroutine

    Log = world.models.NotificationLog
    async with world.sessions() as db:
        logs = (await db.execute(
            select(Log.status, Log.delivery_metadata, Log.delivery_key).where(
                Log.user_id == world.member, Log.event_type == "digest_delivery",
            )
        )).all()
    assert [tuple(log) for log in logs] == [(
        "sent",
        {"scope_source": "saved_view_deleted", "saved_view_id": None, "saved_view_name": "Checkout regressions"},
        None,
    )]
    [email] = [mail for mail in sent if mail["to_email"] == f"w09bv-member-{world.tag}@example.com"]
    assert "View deleted: Checkout regressions — this digest covers the whole project." in email["html_body"]
