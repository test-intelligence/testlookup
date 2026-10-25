"""W13 TCL-07 on real PostgreSQL: a suite is listed on the TM Suites tab before its first run.

Through the real app over HTTP: a QA lead creates ``Checkout API`` with an
explicit owner (``POST /api/v1/suites``); ``GET /api/v1/test-management/suites``
then lists it as an ``empty: true`` row carrying its ``suite_id`` and that
owner, beside the ingested ``Smoke`` bucket (which carries no ``empty`` key).
The project's DEFAULT suite is never added, another project's hand-made suite
never appears, a release filter adds nothing, and once a run reports
``checkout api`` the suite is an aggregated row like any other.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/v1/test-management/suites"


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


@pytest_asyncio.fixture
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
        Release,
        TestCase,
        TestRun,
        TestSuite,
        User,
        UserRole,
    )

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    redis = _redis_client()
    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    lead, other_lead = uuid.uuid4(), uuid.uuid4()
    release = uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        async with sessions.begin() as db:
            db.add_all([
                Project(id=project_a, name=f"w13-tcl07-a-{tag}", slug=f"w13-tcl07-a-{tag}", is_active=True),
                Project(id=project_b, name=f"w13-tcl07-b-{tag}", slug=f"w13-tcl07-b-{tag}", is_active=True),
            ])
            for uid, label in ((lead, "lead"), (other_lead, "other")):
                db.add(User(
                    id=uid, email=f"w13-tcl07-{label}-{tag}@example.invalid", username=f"w13_tcl07_{label}_{tag}",
                    full_name=f"TCL-07 {label}", hashed_password="!unusable", role=UserRole.QA_LEAD.value,
                    is_active=True,
                ))
            await db.flush()
            db.add(ProjectMember(project_id=project_a, user_id=lead, role=UserRole.QA_LEAD.value))
            db.add(ProjectMember(project_id=project_b, user_id=other_lead, role=UserRole.QA_LEAD.value))
            db.add(Release(id=release, project_id=project_a, name=f"w13-r1-{tag}", version="1.0.0", status="active"))
            # The project's catch-all default suite, and an ingested ``Smoke``.
            db.add(TestSuite(project_id=project_a, name=f"Default Suite ({tag})", is_default=True))
            db.add(TestSuite(project_id=project_a, name="Smoke"))
            run_id = uuid.uuid4()
            db.add(TestRun(
                id=run_id, project_id=project_a, build_number=f"w13-{tag}", jenkins_job="w13",
                status=LaunchStatus.PASSED, ingestion_source="unknown", total_tests=1, passed_tests=1,
                created_at=now - timedelta(hours=2),
            ))
            await db.flush()
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=run_id, test_fingerprint=f"fp-smoke-{tag}", test_name="test_smoke",
                status="PASSED", suite_name="Smoke",
            ))

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, tag=tag, project_a=project_a, project_b=project_b,
                lead=lead, release=release, now=now, headers=_jwt(lead), other_headers=_jwt(other_lead),
                TestRun=TestRun, TestCase=TestCase, LaunchStatus=LaunchStatus,
            )
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        patch.undo()
        async with sessions.begin() as db:
            await db.execute(text("DELETE FROM canonical_test_cases WHERE project_id IN (:a, :b)"),
                             {"a": project_a, "b": project_b})
            await db.execute(text("DELETE FROM projects WHERE id IN (:a, :b)"), {"a": project_a, "b": project_b})
            await db.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": lead, "b": other_lead})
        await engine.dispose()
        await redis.aclose()
        await app_postgres.dispose_engine_for_loop()


async def _suites(world, **params):
    resp = await world.client.get(URL, params={"project_id": str(world.project_a), **params}, headers=world.headers)
    assert resp.status_code == 200, resp.text
    return {row["suite_name"]: row for row in resp.json()}


async def test_a_hand_made_suite_is_listed_empty_with_its_owner_until_a_run_reports_it(world):
    created = await world.client.post("/api/v1/suites", json={
        "project_id": str(world.project_a), "name": "Checkout API", "owner_user_id": str(world.lead),
    }, headers=world.headers)
    assert created.status_code == 201, created.text
    suite_id = created.json()["id"]
    other = await world.client.post("/api/v1/suites", json={
        "project_id": str(world.project_b), "name": "Billing",
    }, headers=world.other_headers)
    assert other.status_code == 201, other.text

    rows = await _suites(world)
    assert set(rows) == {"Smoke", "Checkout API"}  # no default suite, nothing of project B
    assert "empty" not in rows["Smoke"] and "suite_id" not in rows["Smoke"]
    added = rows["Checkout API"]
    assert added["empty"] is True and added["suite_id"] == suite_id
    assert (added["test_count"], added["pass_rate"], added["last_run_id"]) == (0, None, None)
    assert added["owner_user_id"] == str(world.lead) and added["owner_is_fallback"] is False

    # A release-scoped catalogue holds what ran in that release: nothing is added.
    scoped = await _suites(world, release_id=str(world.release))
    assert "Checkout API" not in scoped

    # A run reports the suite (another case): it is an aggregated row from now on.
    async with world.sessions.begin() as db:
        run_id = uuid.uuid4()
        db.add(world.TestRun(
            id=run_id, project_id=world.project_a, build_number=f"w13-2-{world.tag}", jenkins_job="w13",
            status=world.LaunchStatus.PASSED, ingestion_source="unknown", total_tests=1, passed_tests=1,
            created_at=world.now - timedelta(minutes=5),
        ))
        await db.flush()
        db.add(world.TestCase(
            id=uuid.uuid4(), test_run_id=run_id, test_fingerprint=f"fp-checkout-{world.tag}",
            test_name="test_checkout", status="PASSED", suite_name="checkout api",
        ))
    rows = await _suites(world)
    assert set(rows) == {"Smoke", "checkout api"}
    assert not any("empty" in row for row in rows.values())
    assert rows["checkout api"]["test_count"] == 1
