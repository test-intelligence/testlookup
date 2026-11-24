"""D-37 and D-38 against real Postgres: the Inbox numbers agree.

D-38: the per-test "xN" chip counted the viewer's own pending rows, so one
failing test read "x9" for the lead (team view) and "x1" for the engineer who
was handed one of the nine. It now counts every failure of that test in the
window (project, live project, release, suite), whoever holds it and whatever
its triage state, so every viewer reads the same N.

D-37: ``/me/assigned-failures/count`` asked with the list's exact arguments
answers the list's ``unresolved_total``.

Hand-built rows in one project P (members: lead L, engineer B):

    test       rows  created        assigned  triage
    t_repeat   1     1 day ago      B         PENDING_REVIEW
    t_repeat   7     1 day ago      L         PENDING_REVIEW
    t_repeat   1     1 day ago      L         DEFECT_CREATED   (off the inbox, still a failure)
    t_repeat   1     20 days ago    L         PENDING_REVIEW   (outside days=7)

So with ``days=7`` the test failed 9 times; B's inbox holds 1 row, the lead's
team view 8.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head. Uses ``REDIS_URL``
when set, else ``fakeredis``. Every row carries a unique tag and is removed.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import (
        LaunchStatus,
        Project,
        ProjectMember,
        TestCase,
        TestRun,
        User,
        UserRole,
    )

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis_client()

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    project = uuid.uuid4()
    lead, engineer = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    recent, old = now - timedelta(days=1), now - timedelta(days=20)
    # (created_at, assignee, triage_status)
    spec = (
        [(recent, engineer, "PENDING_REVIEW")]
        + [(recent, lead, "PENDING_REVIEW")] * 7
        + [(recent, lead, "DEFECT_CREATED"), (old, lead, "PENDING_REVIEW")]
    )
    try:
        async with sessions() as db:
            db.add(Project(
                id=project, name=f"d38-{tag}", slug=f"d38-{tag}",
                is_active=True, description=f"throwaway D-37/D-38 {tag}",
            ))
            for uid, label, role in (
                (lead, "lead", UserRole.QA_LEAD.value),
                (engineer, "engineer", UserRole.QA_ENGINEER.value),
            ):
                db.add(User(
                    id=uid, email=f"d38-{label}-{tag}@example.com",
                    username=f"d38_{label}_{tag}", full_name=f"D38 {label}",
                    hashed_password="!unusable", role=role,
                ))
            await db.flush()
            for uid, role in ((lead, UserRole.QA_LEAD.value), (engineer, UserRole.QA_ENGINEER.value)):
                db.add(ProjectMember(project_id=project, user_id=uid, role=role))
            run_ids = [uuid.uuid4() for _ in spec]
            for index, (run_id, (when, _who, _triage)) in enumerate(zip(run_ids, spec)):
                db.add(TestRun(
                    id=run_id, project_id=project, build_number=f"d38-{index}-{tag}",
                    jenkins_job="d38", status=LaunchStatus.FAILED, ingestion_source="unknown",
                    trigger_source="api", primary_suite_name="Checkout",
                    total_tests=1, created_at=when + timedelta(minutes=index),
                ))
            await db.flush()
            for index, (run_id, (when, who, triage)) in enumerate(zip(run_ids, spec)):
                db.add(TestCase(
                    test_run_id=run_id, test_name="t_repeat", suite_name="Checkout",
                    test_fingerprint=f"d38-{tag}", class_name="CheckoutTests",
                    status="FAILED", assigned_to_user_id=who, triage_status=triage,
                    created_at=when + timedelta(minutes=index),
                ))
            await db.commit()

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, project=str(project), lead=_jwt(lead), engineer=_jwt(engineer),
            )
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            text("DELETE FROM projects WHERE id = :p"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id IN (:u1, :u2)"),
            delete(User).where(User.email.like(f"d38-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, {"p": project, "u1": lead, "u2": engineer})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"d38 teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _list(world, headers, scope):
    params = {"project_id": world.project, "days": 7, "scope": scope, "size": 100}
    resp = await world.client.get("/api/v1/me/assigned-failures", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return params, resp.json()


async def test_every_viewer_reads_the_same_failure_count(world):
    _, mine = await _list(world, world.engineer, "mine")
    _, team = await _list(world, world.lead, "team")
    assert len(mine["items"]) == 1
    assert len(team["items"]) == 8
    assert {i["failure_count"] for i in mine["items"]} == {9}
    assert {i["failure_count"] for i in team["items"]} == {9}


@pytest.mark.parametrize("who,scope", [("engineer", "mine"), ("lead", "mine"), ("lead", "team")])
async def test_the_count_endpoint_answers_the_lists_total(world, who, scope):
    headers = getattr(world, who)
    params, body = await _list(world, headers, scope)
    resp = await world.client.get(
        "/api/v1/me/assigned-failures/count",
        params={k: v for k, v in params.items() if k != "size"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == body["unresolved_total"]
