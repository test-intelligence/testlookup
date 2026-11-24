"""The suite catalogue on real PostgreSQL: each test under its own suite; authored cases apart.

D-60 / S-E-6 (product findings 2026-11-21): ONE file upload carrying three
suites -- checkout (10 tests, 2 failing), cart (8, 3 failing), search (7) --
has checkout as its dominant ``primary_suite_name``. The catalogue filed all
25 tests (and all 5 failures) under checkout. Each suite now counts its own
tests only, by the shared effective suite /coverage groups by. A live-stream
row with no suite of its own still falls to the run label.

D-59: one authored case in checkout leaves its pass rate, test count and last
run as they were and sets ``authored_count`` to 1. A suite of authored cases
only has no pass rate and no last run.

Calls ``list_test_suites`` directly on a throwaway project, deleted after.
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

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SUITES = {"checkout": (10, 2), "cart": (8, 3), "search": (7, 0)}  # tests, failing


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest_asyncio.fixture
async def world():
    from app.db import postgres as app_postgres
    from app.models.postgres import LaunchStatus, Project, TestCase, TestRun

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:10]
    project = uuid.uuid4()
    upload, live = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        async with sessions.begin() as db:
            db.add(Project(id=project, name=f"d60-{tag}", slug=f"d60-{tag}", is_active=True))
            await db.flush()
            total = sum(n for n, _ in SUITES.values())
            failing = sum(f for _, f in SUITES.values())
            db.add(TestRun(
                id=upload, project_id=project, build_number=f"d60-upload-{tag}", jenkins_job="d60",
                status=LaunchStatus.FAILED, ingestion_source="unknown", trigger_source="push",
                total_tests=total, passed_tests=total - failing, failed_tests=failing,
                primary_suite_name="checkout", suite_names=sorted(SUITES),
                created_at=now - timedelta(hours=2),
            ))
            # An older live-stream session whose events carry no suite: its
            # one test belongs to the session's label.
            db.add(TestRun(
                id=live, project_id=project, build_number=f"d60-live-{tag}", jenkins_job="d60",
                status=LaunchStatus.PASSED, ingestion_source="unknown", trigger_source="live_stream",
                total_tests=1, passed_tests=1, primary_suite_name="streamed",
                created_at=now - timedelta(days=1),
            ))
            await db.flush()
            for suite, (count, fails) in SUITES.items():
                for i in range(count):
                    db.add(TestCase(
                        id=uuid.uuid4(), test_run_id=upload, test_fingerprint=f"fp-{suite}-{i}-{tag}",
                        test_name=f"test_{suite}_{i}", status="FAILED" if i < fails else "PASSED",
                        suite_name=suite,
                    ))
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=live, test_fingerprint=f"fp-streamed-{tag}",
                test_name="test_streamed", status="PASSED", suite_name=None,
            ))
        yield SimpleNamespace(sessions=sessions, project=project, upload=upload, live=live, tag=tag)
    finally:
        async with sessions.begin() as db:
            await db.execute(text("DELETE FROM managed_test_cases WHERE project_id = :p"), {"p": project})
            await db.execute(text("DELETE FROM canonical_test_cases WHERE project_id = :p"), {"p": project})
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project})
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _catalogue(world) -> dict:
    from app.routers.test_management_exports import list_test_suites

    async with world.sessions() as db:
        rows = await list_test_suites(
            project_id=world.project, db=db,
            current_user=SimpleNamespace(id=uuid.uuid4(), role="ADMIN"),
        )
    return {row["suite_name"]: row for row in rows}


async def test_a_multi_suite_upload_files_each_test_under_its_own_suite(world):
    rows = await _catalogue(world)
    assert {name: rows[name]["test_count"] for name in SUITES} == {"checkout": 10, "cart": 8, "search": 7}
    # S-E-6: checkout's failures are checkout's two, not the run's five.
    assert {name: rows[name]["failed_count"] for name in SUITES} == {"checkout": 2, "cart": 3, "search": 0}
    assert rows["checkout"]["passed_count"] == 8
    assert rows["checkout"]["pass_rate"] == 80.0
    assert rows["cart"]["pass_rate"] == 62.5
    for name in SUITES:
        assert rows[name]["last_run_id"] == str(world.upload), name
        assert rows[name]["authored_count"] == 0
    # A live-stream row with no suite of its own falls to the session's label.
    assert rows["streamed"]["test_count"] == 1
    assert rows["streamed"]["last_run_id"] == str(world.live)


async def test_an_authored_case_leaves_the_run_statistics_unchanged(world):
    from app.models.postgres import ManagedTestCase

    before = await _catalogue(world)
    async with world.sessions.begin() as db:
        db.add(ManagedTestCase(project_id=world.project, title="Pay by card", suite_name="checkout", status="active"))
        db.add(ManagedTestCase(project_id=world.project, title="Refund", suite_name="payments", status="draft"))
    after = await _catalogue(world)

    for key in ("test_count", "passed_count", "failed_count", "pass_rate", "last_run_at", "last_run_id"):
        assert after["checkout"][key] == before["checkout"][key], key
    assert after["checkout"]["authored_count"] == 1
    payments = after["payments"]
    assert (payments["authored_count"], payments["test_count"]) == (1, 0)
    assert payments["pass_rate"] is None
    assert payments["last_run_at"] is None and payments["last_run_id"] is None
