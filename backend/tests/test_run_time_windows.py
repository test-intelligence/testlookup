"""D-19: charts and windows place a run by WHEN IT EXECUTED, not when it was uploaded.

Before the fix every chart bucketed on ``test_runs.created_at`` (the row's
insert time) and every window filtered on it, so a run executed ten days ago
and uploaded today was drawn on today's bar and counted in a 5-day window. The
run time is ``coalesce(start_time, created_at)`` (``services/run_time.py``):
the execution time when the reporter sent one, else the receive time.

* The runs list's window runs on in-memory SQLite through the REAL statement
  (``list_project_runs`` and its bulk-resolver twin ``run_list_filters``).
* The chart statements are Postgres SQL (``TO_CHAR``/``DATE_TRUNC``); here the
  built text is checked, and ``tests/integration/test_run_time_postgres.py``
  executes them (a run uploaded now with a start time ten days ago lands in the
  bucket of ten days ago and not in today's).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import select  # noqa: E402
from sqlalchemy import types as sqltypes  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.models.postgres import Project, TestRun  # noqa: E402
from app.services import chart_data_service as charts  # noqa: E402
from app.services import chart_rows_service as rows  # noqa: E402
from app.services import coverage_map_service, failure_groups_service, heatmap_service  # noqa: E402
from app.services import metrics_service, runs_service, test_scatter_service  # noqa: E402
from app.services.analytics_scope import AnalyticsScope  # noqa: E402
from app.services.run_time import RUN_TIME_SQL, run_time_column  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:  # noqa: D103 - JSONB as SQLite JSON
    return "JSON"


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect: Any, coltype: Any) -> Any:  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


NOW = datetime.now(timezone.utc)
PROJECT = uuid.uuid4()
BACKDATED = uuid.uuid4()  # executed ten days ago, uploaded now
FRESH = uuid.uuid4()  # executed now, uploaded now
LATE_UPLOAD = uuid.uuid4()  # executed one day ago, uploaded now
NO_START = uuid.uuid4()  # no execution time: falls back to created_at (now)


@pytest.fixture
async def maker() -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        # The tables only: several of their indexes are Postgres-only.
        for table in (Project.__table__, TestRun.__table__):
            await conn.execute(CreateTable(table))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Project(id=PROJECT, name="d19", slug="d19", is_active=True))
        await db.flush()
        for run_id, build, start in (
            (BACKDATED, "10", NOW - timedelta(days=10)),
            (FRESH, "11", NOW - timedelta(minutes=5)),
            (LATE_UPLOAD, "12", NOW - timedelta(days=1, hours=1)),
            (NO_START, "13", None),
        ):
            db.add(TestRun(
                id=run_id, project_id=PROJECT, build_number=build, status="PASSED",
                start_time=start, created_at=NOW,
            ))
        await db.commit()
    yield factory
    await engine.dispose()


async def _window_ids(db: Any, days: int) -> set[uuid.UUID]:
    filters = runs_service.run_list_filters(
        PROJECT, None, None, None, days, None,
    )
    return set((await db.execute(select(TestRun.id).where(*filters))).scalars())


async def test_the_bulk_twin_window_reads_the_execution_time(maker: Any) -> None:
    async with maker() as db:
        assert await _window_ids(db, 30) == {BACKDATED, FRESH, LATE_UPLOAD, NO_START}
        # Uploaded now, executed ten days ago: outside a 5-day window. On
        # created_at it was inside, which is the D-19 defect.
        assert await _window_ids(db, 5) == {FRESH, LATE_UPLOAD, NO_START}
        assert await _window_ids(db, 1) == {FRESH, NO_START}


async def test_the_runs_list_includes_a_back_dated_run_by_its_execution_time(
    maker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The enrichment reads other tables (releases, run sequence, suites) that
    # are not under test; ``order="newest"`` keeps the statement free of the
    # Postgres-only natural build key. The WHERE clause is the list's own.
    async def _empty(_db: Any, _ids: Any) -> dict:
        return {}

    for name in ("fetch_release_map", "fetch_run_seq_map", "fetch_run_suites_map"):
        monkeypatch.setattr(runs_service, name, _empty)

    async def _ids(db: Any, days: int) -> tuple[set[uuid.UUID], int]:
        items, total, _pages = await runs_service.list_project_runs(
            db, PROJECT, 1, 50, days=days, order="newest",
        )
        return {uuid.UUID(str(item["id"])) for item in items}, total

    async with maker() as db:
        wide, wide_total = await _ids(db, 30)
        narrow, narrow_total = await _ids(db, 5)
    assert wide == {BACKDATED, FRESH, LATE_UPLOAD, NO_START} and wide_total == 4
    # Executed ten days ago, uploaded now: listed by its execution day, so a
    # 5-day window leaves it out (on created_at it was in, D-19).
    assert narrow == {FRESH, LATE_UPLOAD, NO_START} and narrow_total == 3


def test_the_run_time_is_one_expression() -> None:
    assert RUN_TIME_SQL == "COALESCE(tr.start_time, tr.created_at)"
    compiled = str(run_time_column().compile(compile_kwargs={"literal_binds": True}))
    assert compiled == "coalesce(test_runs.start_time, test_runs.created_at)"


@pytest.mark.parametrize("dimension", ["day", "week"])
def test_chart_time_buckets_read_the_run_time(dimension: str) -> None:
    sql = charts.DIMENSIONS[dimension].sql
    assert RUN_TIME_SQL in sql
    assert "tr.created_at AT TIME ZONE" not in sql


def _scope() -> AnalyticsScope:
    return AnalyticsScope(
        project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30,
    )


def _no_created_at_window(sql: str) -> None:
    assert f"{RUN_TIME_SQL} >= :period_start" in sql
    assert not re.search(r"tr\.created_at\s*>=", sql), sql


def test_the_chart_statement_windows_and_buckets_on_the_run_time() -> None:
    spec = charts.parse_chart_spec("executions", ["day"], None, scope=_scope())
    sql, _params = charts.build_statement(spec, _scope(), now=NOW)
    _no_created_at_window(sql)
    assert f"DATE_TRUNC('day', {RUN_TIME_SQL} AT TIME ZONE 'UTC')" in sql


def test_the_dashboard_trend_and_headline_read_the_run_time() -> None:
    import inspect

    trend = inspect.getsource(metrics_service)
    assert "DATE_TRUNC('day', {RUN_TIME_SQL} AT TIME ZONE 'UTC')" in trend
    assert "WHERE {RUN_TIME_SQL} >= :period_start" in trend
    period = inspect.getsource(metrics_service._period_stats)
    assert "TestRun.created_at >= start" not in period
    assert "run_time_column()" in period


@pytest.mark.parametrize(
    "module",
    [charts, rows, heatmap_service, coverage_map_service, failure_groups_service, test_scatter_service],
    ids=lambda m: m.__name__.rsplit(".", 1)[-1],
)
def test_no_chart_family_window_reads_created_at(module: Any) -> None:
    import inspect

    source = inspect.getsource(module)
    assert not re.search(r"tr\.created_at\s*>=", source), module.__name__
    assert "RUN_TIME_SQL" in source
