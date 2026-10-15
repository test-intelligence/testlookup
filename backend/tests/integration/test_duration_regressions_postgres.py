"""EXEC-05 against PostgreSQL: the finalize step, the read, the baseline feed
and the nightly sweep, on the real schema (migrations 0203 + 0204).

What the unit tests cannot show without a database: the JSONB marker the sweep
reads (``duration_findings ->> 'baseline_fed_through'``), the run-row and
cursor-row locks, the window query over ``suite_executions``, the staged
notification's ``ON CONFLICT`` insert, and that the sweep joins the run for its
project (``test_cases`` has no project_id).

Fixture: 24 earlier runs of a project (``checkout::test_pay`` at 90/110 ms,
suite Checkout at 1000 ms wall, the last two at 1800 ms), a baseline of 20
samples for test_pay, the sweep's cursor past them, and the run under test
(test_pay 400 ms, Checkout 1800 ms). A QA lead member subscribed to
"became slower" on Slack.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head;
skips otherwise. Everything runs in one transaction that is rolled back.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def conn():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    async with engine.connect() as connection:
        outer = await connection.begin()
        try:
            yield connection
        finally:
            await outer.rollback()
    await engine.dispose()


def _session(connection) -> AsyncSession:
    # A commit inside (the nightly sweep owns one) releases a SAVEPOINT; the
    # outer transaction is still rolled back at the end.
    return AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)


async def _fixture(db: AsyncSession) -> dict:
    from app.models.postgres import (
        AppSetting,
        NotificationChannel,
        NotificationPreference,
        PerfBaseline,
        Project,
        ProjectMember,
        SuiteExecution,
        TestCase,
        TestRun,
        User,
    )
    from app.services import perf_regression_service as perf

    token = uuid.uuid4().hex[:10]
    project = Project(id=uuid.uuid4(), name=f"duration {token}", slug=f"duration-{token}")
    user = User(id=uuid.uuid4(), email=f"lead-{token}@example.com", username=f"lead-{token}",
                hashed_password="x", role="QA_LEAD", is_active=True)
    db.add_all([project, user])
    await db.flush()
    db.add(ProjectMember(user_id=user.id, project_id=project.id, role="QA_LEAD"))
    db.add(NotificationPreference(
        user_id=user.id, project_id=project.id, channel=NotificationChannel.SLACK.value, enabled=True,
        events=["test.newly_slow", "suite.newly_slow"],
        slack_webhook_url="https://hooks.fake.test/services/T000/B000/XXXX",
    ))

    start = datetime.now(timezone.utc) - timedelta(days=30)
    fp = f"fp-pay-{token}"
    walls = [1000] * 22 + [1800, 1800]
    last_earlier = start
    for index, wall in enumerate(walls):
        at = start + timedelta(hours=index)
        run = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"b{index}-{token}",
                      branch="main", status="PASSED", created_at=at)
        db.add(run)
        await db.flush()
        db.add(TestCase(test_run_id=run.id, test_name="checkout::test_pay", suite_name="Checkout",
                        test_fingerprint=fp, status="PASSED", duration_ms=90 if index % 2 else 110,
                        created_at=at))
        db.add(SuiteExecution(project_id=project.id, test_run_id=run.id, suite_name="Checkout",
                              suite_key="checkout", wall_ms=wall, summed_ms=wall, source="derived",
                              run_created_at=at, branch="main"))
        last_earlier = at

    baseline = PerfBaseline(project_id=project.id, test_fingerprint=fp, test_name="checkout::test_pay",
                            suite_name="Checkout")
    for i in range(20):
        perf._welford_update(baseline, 90 if i % 2 else 110)
    db.add(baseline)

    # The sweep already passed every earlier row.
    setting = (await db.execute(
        select(AppSetting).where(AppSetting.key == perf._REFRESH_CURSOR_KEY)
    )).scalar_one_or_none()
    if setting is None:
        setting = AppSetting(key=perf._REFRESH_CURSOR_KEY)
        db.add(setting)
    setting.value = {"last_created_at": last_earlier.isoformat()}

    now = datetime.now(timezone.utc)
    current = TestRun(id=uuid.uuid4(), project_id=project.id, build_number=f"b-now-{token}",
                      branch="main", status="PASSED", created_at=now)
    db.add(current)
    await db.flush()
    db.add(TestCase(test_run_id=current.id, test_name="checkout::test_pay", suite_name="Checkout",
                    test_fingerprint=fp, status="PASSED", duration_ms=400, created_at=now))
    db.add(SuiteExecution(project_id=project.id, test_run_id=current.id, suite_name="Checkout",
                          suite_key="checkout", wall_ms=1800, summed_ms=1800, source="derived",
                          run_created_at=now, branch="main"))
    await db.flush()
    return {"project": project, "user": user, "run": current, "fp": fp, "baseline": baseline, "now": now}


async def test_evaluation_stores_findings_feeds_once_notifies_and_the_sweep_skips_the_run(conn):
    from app.models.postgres import AppSetting, NotificationLog, PerfBaseline, TestRun
    from app.services import duration_regression_service as svc
    from app.services import perf_regression_service as perf

    with patch.object(perf, "_feature_enabled", AsyncMock(return_value=True)):
        async with _session(conn) as db:
            data = await _fixture(db)
            out = await svc.evaluate_duration_regressions(db, data["run"].id)
            await db.commit()

        assert out["measured"] is True
        [spike] = out["spikes"]
        assert spike["test_fingerprint"] == data["fp"] and spike["sigma"] > 25 and spike["new"] is True
        [suite] = out["suites"]
        assert (suite["suite_key"], suite["ratio"], suite["metric"], suite["new"]) == ("checkout", 1.8, "wall", True)
        assert suite["baseline_runs"] == 20

        async with _session(conn) as db:
            stored = (await db.execute(
                select(TestRun.duration_findings).where(TestRun.id == data["run"].id)
            )).scalar_one()
            assert stored["baseline_fed_through"] == data["now"].isoformat()
            assert stored["notified"] == {"tests": [data["fp"]], "suites": ["checkout"]}
            samples = (await db.execute(
                select(PerfBaseline.sample_count).where(PerfBaseline.test_fingerprint == data["fp"])
            )).scalar_one()
            assert samples == 21, "fed once on ingest"
            [log] = (await db.execute(
                select(NotificationLog).where(NotificationLog.run_id == data["run"].id)
            )).scalars().all()
            assert log.user_id == data["user"].id
            assert log.event_type == "test.newly_slow" and log.status == "pending" and log.channel == "slack"
            assert "Suite Checkout: 1.8× slower" in log.body

            # The read serves what was stored, without the bookkeeping.
            read = await svc.get_duration_regressions(db, data["run"].id)
            assert read["stored"] is True and "baseline_fed_through" not in read
            summary = await svc.load_policy_summary(db, data["run"].id)
            assert summary["spike_count"] == 1 and summary["suite_regression_count"] == 1

        # Re-evaluation (a re-finalize): no second feed, no second message.
        async with _session(conn) as db:
            again = await svc.evaluate_duration_regressions(db, data["run"].id)
            await db.commit()
        assert again["spikes"] == out["spikes"]

        # The nightly sweep sees the run's row (it is past the cursor) and skips it.
        with patch.object(perf, "AsyncSessionLocal", lambda: _session(conn)):
            swept = await perf.refresh_baselines()

        async with _session(conn) as db:
            samples = (await db.execute(
                select(PerfBaseline.sample_count).where(PerfBaseline.test_fingerprint == data["fp"])
            )).scalar_one()
            assert samples == 21, "the sweep must not feed a row fed on ingest"
            logs = (await db.execute(
                select(NotificationLog.id).where(NotificationLog.run_id == data["run"].id)
            )).all()
            assert len(logs) == 1
            cursor = (await db.execute(
                select(AppSetting.value).where(AppSetting.key == perf._REFRESH_CURSOR_KEY)
            )).scalar_one()
            assert datetime.fromisoformat(cursor["last_created_at"]) >= data["now"], "the watermark moved past it"
        assert swept["skipped"] == 0


async def test_without_the_flag_the_run_page_read_still_judges_suites_and_writes_nothing(conn):
    from app.models.postgres import PerfBaseline, TestRun
    from app.services import duration_regression_service as svc
    from app.services import perf_regression_service as perf

    with patch.object(perf, "_feature_enabled", AsyncMock(return_value=False)):
        async with _session(conn) as db:
            data = await _fixture(db)
            read = await svc.get_duration_regressions(db, data["run"].id)
            assert read["stored"] is False
            assert read["test_check"]["measured"] is False
            assert "perf_regression_detection" in read["test_check"]["reason"]
            assert [s["suite_key"] for s in read["suites"]] == ["checkout"]
            assert (await db.execute(
                select(TestRun.duration_findings).where(TestRun.id == data["run"].id)
            )).scalar_one() is None
            assert (await db.execute(
                select(PerfBaseline.sample_count).where(PerfBaseline.test_fingerprint == data["fp"])
            )).scalar_one() == 20
