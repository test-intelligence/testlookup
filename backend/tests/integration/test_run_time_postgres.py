"""D-19 on real PostgreSQL: a run is charted and windowed by when it EXECUTED.

A run uploaded now whose ``start_time`` is ten days ago:

* chart-data ``group_by=day`` counts it in the bucket of ten days ago and not
  in today's (the bucket expression is Postgres SQL, so only here does it run);
* the dashboard trend (``metrics_service.get_trend_data``) does the same;
* the runs list's window includes it at 30 days and leaves it out at 5;
* a run with no ``start_time`` falls back to ``created_at``.

And the planner can use ``ix_test_runs_project_run_time`` (migration 0259) for
the window: the index exists with the expression the queries spell.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
Seeds a throwaway project and deletes it (runs cascade).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import chart_data_service as charts
from app.services import metrics_service
from app.services.analytics_scope import AnalyticsScope
from app.services.runs_service import list_project_runs

pytest.importorskip("asyncpg")

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)
TEN_DAYS_AGO = NOW - timedelta(days=10)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def project(engine):
    pid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO projects (id, name, slug, is_active) VALUES (:id, 'd19-run-time', :s, true)"),
            {"id": pid, "s": f"d19-{pid.hex}"},
        )
    yield pid
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM test_runs WHERE project_id = :id"), {"id": pid})
        await conn.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})


async def _run(engine, project_id, build: str, *, start_time, total: int) -> uuid.UUID:
    rid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO test_runs (id, project_id, build_number, status, ingestion_source, "
            "start_time, total_tests, passed_tests, failed_tests, skipped_tests, broken_tests, "
            "created_at, updated_at) "
            "VALUES (:id, :p, :b, 'PASSED', 'file', :st, :n, :n, 0, 0, 0, :now, :now)"
        ), {"id": rid, "p": project_id, "b": build, "st": start_time, "n": total, "now": NOW})
    return rid


def _scope(project_id: uuid.UUID, days: int = 30) -> AnalyticsScope:
    return AnalyticsScope(
        project_id=project_id, allowed_project_ids=None, release_ids=(), suite_names=(), days=days,
    )


async def test_a_back_dated_run_is_charted_on_its_execution_day(engine, project):
    await _run(engine, project, "backfill", start_time=TEN_DAYS_AGO, total=7)
    await _run(engine, project, "no-start", start_time=None, total=2)  # created_at = now
    scope = _scope(project)
    spec = charts.parse_chart_spec("executions", ["day"], None, scope=scope)
    async with AsyncSession(engine) as db:
        payload = await charts.build_chart_data(db, scope, spec, now=NOW)
    points = {point["x"]: point["y"] for point in payload["series"][0]["points"]}
    executed_day = TEN_DAYS_AGO.date().isoformat()
    today = NOW.date().isoformat()
    assert points[executed_day] == 7
    # Today holds only the run with no execution time (created_at fallback);
    # on created_at the back-dated run's 7 were here too.
    assert points[today] == 2


async def test_the_dashboard_trend_buckets_on_the_execution_day(engine, project):
    await _run(engine, project, "backfill", start_time=TEN_DAYS_AGO, total=7)
    async with AsyncSession(engine) as db:
        trend = await metrics_service.get_trend_data(db, str(project), days=30)
        short = await metrics_service.get_trend_data(db, str(project), days=5)
    by_day = {row["date"]: row["total"] for row in trend}
    assert by_day == {TEN_DAYS_AGO.date().isoformat(): 7}
    assert short == []


async def test_the_runs_list_window_reads_the_execution_time(engine, project):
    await _run(engine, project, "backfill", start_time=TEN_DAYS_AGO, total=1)
    await _run(engine, project, "fresh", start_time=NOW - timedelta(minutes=1), total=1)
    async with AsyncSession(engine) as db:
        wide, wide_total, _ = await list_project_runs(db, str(project), 1, 50, days=30)
        narrow, narrow_total, _ = await list_project_runs(db, str(project), 1, 50, days=5)
    assert {item["build_number"] for item in wide} == {"backfill", "fresh"} and wide_total == 2
    assert {item["build_number"] for item in narrow} == {"fresh"} and narrow_total == 1


async def test_the_run_time_index_exists_with_the_query_expression(engine):
    async with engine.connect() as conn:
        definition = (await conn.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_test_runs_project_run_time'"
        ))).scalar()
    assert definition is not None, "migration 0259 has not run"
    assert "COALESCE(start_time, created_at)" in definition
