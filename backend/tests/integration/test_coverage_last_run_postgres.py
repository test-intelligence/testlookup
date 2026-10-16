"""W08 TRND-07 -- ``coverage_stats``' per-suite last run against real Postgres.

Each suite row of ``GET /api/v1/analytics/coverage`` carries ``last_run_at``
(the newest run's ``created_at``, ISO UTC) and ``last_run_id`` (that run's id,
from the MAX of "<UTC timestamp>|<id>": no sort, no third statement). Proved:
the newer of two runs wins; a live-stream run labels by its session suite (the
effective suite); a tie at the microsecond takes the larger id; the release
filter scopes it; and ``coverage_stats`` still issues EXACTLY two statements.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (a throwaway
database).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.models.postgres import (
        LaunchStatus,
        Project,
        ProjectMember,
        Release,
        TestCase,
        TestRun,
        User,
        UserRole,
    )
    from app.routers import analytics

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(analytics.router)
    app.dependency_overrides[get_db] = _get_db
    patch = pytest.MonkeyPatch()
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    pid, member = uuid.uuid4(), uuid.uuid4()
    runs: dict[str, uuid.UUID] = {}
    tie_ids = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
    try:
        async with sessions() as db:
            db.add(Project(id=pid, name=f"lastrun-{tag}", slug=f"lastrun-{tag}", is_active=True))
            db.add(User(id=member, email=f"lastrun-{tag}@example.com", username=f"lastrun_{tag}",
                        full_name="Last run", hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            db.add(ProjectMember(project_id=pid, user_id=member, role=UserRole.QA_ENGINEER.value))
            release = Release(id=uuid.uuid4(), project_id=pid, name=f"lastrun-r-{tag}", version="1", status="active")
            db.add(release)
            await db.flush()

            plan = [
                # key, when, trigger, primary suite, row suite, release
                ("old", NOW - timedelta(days=3), None, None, "checkout", release.id),
                ("new", NOW - timedelta(days=1), None, None, "checkout", None),
                ("live", NOW - timedelta(days=2), "live_stream", "Payments Live", "com.acme.LiveTest", None),
            ]
            for key, when, trigger, primary, suite, rel in plan:
                run_id = uuid.uuid4()
                runs[key] = run_id
                db.add(TestRun(id=run_id, project_id=pid, build_number=f"lr-{key}-{tag}", jenkins_job="lr",
                               status=LaunchStatus.PASSED, ingestion_source="sdk", trigger_source=trigger,
                               primary_suite_name=primary, primary_release_id=rel, total_tests=1,
                               passed_tests=1, created_at=when))
                await db.flush()
                db.add(TestCase(id=uuid.uuid4(), test_run_id=run_id, test_fingerprint=f"{key}-{tag}",
                                test_name=key, suite_name=suite, status="PASSED", created_at=when))
            # Two runs of "tie" at the same microsecond.
            tie_at = NOW - timedelta(hours=5, microseconds=123456)
            for index, run_id in enumerate(tie_ids):
                db.add(TestRun(id=run_id, project_id=pid, build_number=f"lr-tie{index}-{tag}", jenkins_job="lr",
                               status=LaunchStatus.PASSED, ingestion_source="sdk", total_tests=1, passed_tests=1,
                               created_at=tie_at))
                await db.flush()
                db.add(TestCase(id=uuid.uuid4(), test_run_id=run_id, test_fingerprint=f"tie{index}-{tag}",
                                test_name=f"tie{index}", suite_name="tie", status="PASSED", created_at=tie_at))
            await db.commit()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, engine=engine, pid=pid, runs=runs, release=release.id,
                tie_ids=tie_ids, tie_at=tie_at,
                member={"Authorization": f"Bearer {create_access_token(str(member))}"},
            )
        finally:
            await client.aclose()
    finally:
        for statement in (
            "DELETE FROM projects WHERE id = :pid",
            "DELETE FROM access_audit_logs WHERE actor_user_id = :uid",
            "DELETE FROM users WHERE id = :uid",
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(text(statement), {"pid": pid, "uid": member})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"last-run teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _stats(world, **kwargs) -> tuple[dict, int]:
    """``coverage_stats`` for the project, and how many statements it ran."""
    from app.services import analytics_service

    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(world.engine.sync_engine, "before_cursor_execute", count)
    try:
        async with world.sessions() as db:
            payload = await analytics_service.coverage_stats(db, str(world.pid), 30, **kwargs)
    finally:
        event.remove(world.engine.sync_engine, "before_cursor_execute", count)
    return payload, len(statements)


def _suite(payload: dict, name: str) -> dict:
    return next(row for row in payload["suites"] if row["suite_name"] == name)


async def test_the_newer_run_is_the_suite_s_last_run_in_two_statements(world):
    payload, statements = await _stats(world)
    assert statements == 2
    checkout = _suite(payload, "checkout")
    assert checkout["last_run_id"] == str(world.runs["new"])
    assert datetime.fromisoformat(checkout["last_run_at"]) == NOW - timedelta(days=1)
    assert checkout["last_run_at"].endswith("+00:00")


async def test_a_live_stream_run_is_the_last_run_of_its_session_suite(world):
    payload, _ = await _stats(world)
    live = _suite(payload, "Payments Live")
    assert live["last_run_id"] == str(world.runs["live"])
    assert not [row for row in payload["suites"] if row["suite_name"] == "com.acme.LiveTest"]


async def test_a_tie_at_the_microsecond_takes_the_larger_id(world):
    payload, _ = await _stats(world)
    tie = _suite(payload, "tie")
    assert tie["last_run_id"] == str(max(world.tie_ids, key=str))
    assert datetime.fromisoformat(tie["last_run_at"]) == world.tie_at


async def test_the_release_filter_scopes_the_last_run_too(world):
    payload, statements = await _stats(world, release_id=str(world.release))
    assert statements == 2
    checkout = _suite(payload, "checkout")
    assert checkout["last_run_id"] == str(world.runs["old"])


async def test_the_route_serves_both_keys_beside_the_existing_ones(world):
    resp = await world.client.get(
        "/api/v1/analytics/coverage", params={"project_id": str(world.pid), "days": 30},
        headers=world.member,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    row = next(r for r in body["suites"] if r["suite_name"] == "checkout")
    assert {"last_run_at", "last_run_id", "unique_tests", "pass_rate", "project_count"} <= set(row)
    assert row["last_run_id"] == str(world.runs["new"])
    assert set(body["summary"]) == {
        "unique_tests", "suite_count", "total_executions", "avg_pass_rate", "days_with_runs",
    }
