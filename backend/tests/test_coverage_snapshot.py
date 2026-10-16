"""W08 TRND-07 -- ``coverage_snapshot_service``, everything a database cannot
answer: the window, the days a beat stores, the column mapping, the upsert's
shape and the statements' fragments. The values on seeded data, the
idempotency and the backfill are ``tests/integration/test_coverage_snapshot_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import coverage_snapshot_service as svc
from app.services.analytics_scope import AnalyticsScope, effective_suite_sql

PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")
D = date(2026, 9, 20)


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30)
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


class _Recorder:
    """A session that records every statement and answers from a queue."""

    def __init__(self, answers=()):
        self.calls: list = []
        self.answers = list(answers)
        self.flushed = 0

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return self.answers.pop(0) if self.answers else _Result([])

    async def flush(self):
        self.flushed += 1


class _Result:
    def __init__(self, rows, scalar=None, one=None):
        self._rows, self._scalar, self._one = rows, scalar, one

    def fetchall(self):
        return self._rows

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def one(self):
        return self._one


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# ── the window and the days ────────────────────────────────────────────────


def test_a_snapshot_describes_the_30_utc_days_ending_its_day():
    start, end = svc.snapshot_window(D)
    assert start == datetime(2026, 8, 22, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 21, tzinfo=timezone.utc)
    assert (end - start).days == svc.SNAPSHOT_WINDOW_DAYS == 30


def test_a_beat_stores_every_missing_day_and_yesterday_never_today():
    today = date(2026, 9, 21)
    assert svc.days_to_snapshot([], today) == [date(2026, 9, 20)]
    missing = [date(2026, 9, 1), date(2026, 9, 18)]
    assert svc.days_to_snapshot(missing, today) == [date(2026, 9, 1), date(2026, 9, 18), date(2026, 9, 20)]
    # Yesterday missing as well is stored once.
    assert svc.days_to_snapshot([date(2026, 9, 20)], today) == [date(2026, 9, 20)]
    assert today not in svc.days_to_snapshot(missing, today)


@pytest.mark.asyncio
async def test_missing_days_are_the_last_30_without_a_row():
    today = date(2026, 9, 21)
    stored = [SimpleNamespace(snapshot_day=date(2026, 9, 20)), SimpleNamespace(snapshot_day=date(2026, 8, 22))]
    db = _Recorder([_Result(stored)])
    missing = await svc.missing_days(db, PROJECT, today=today)
    assert len(missing) == 28
    assert missing[0] == date(2026, 8, 23) and missing[-1] == date(2026, 9, 19)
    (statement, params) = db.calls[0]
    assert params == {"snap_project": PROJECT, "snap_first": date(2026, 8, 22), "snap_last": date(2026, 9, 20)}
    assert svc.BACKFILL_DAYS == 30


def test_exercised_pct_is_null_over_an_empty_catalogue():
    assert svc.exercised_pct(0, 5) is None
    assert svc.exercised_pct(200, 50) == 25.0
    assert svc.exercised_pct(3, 1) == 33.3
    # Tests ran that the catalogue does not hold yet: stated, not clamped.
    assert svc.exercised_pct(10, 12) == 120.0


def test_a_history_point_speaks_the_clear_names():
    row = SimpleNamespace(snapshot_day=D, total_tests=40, automated_count=30, total_suites=4)
    assert svc.history_point(row) == {
        "day": "2026-09-20", "catalogue_tests": 40, "exercised_tests": 30,
        "exercised_pct": 75.0, "suites": 4,
    }
    empty = SimpleNamespace(snapshot_day=D, total_tests=0, automated_count=0, total_suites=0)
    assert svc.history_point(empty)["exercised_pct"] is None


def test_the_history_window_ends_yesterday():
    assert svc.history_window_start(90, date(2026, 9, 21)) == date(2026, 6, 23)
    assert svc.HISTORY_MAX_DAYS == 365


# ── the statements ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_upsert_is_on_the_project_day_index_and_rewrites_every_value():
    db = _Recorder()
    values = {"catalogue_tests": 4, "exercised_tests": 3, "suites": 2, "suite_coverage": [{"suite_name": "a"}]}
    await svc.stage_snapshot(db, PROJECT, D, values)
    (statement, _), = db.calls
    sql = _sql(statement)
    assert sql.startswith("INSERT INTO coverage_snapshots")
    assert "ON CONFLICT (project_id, snapshot_day) DO UPDATE SET" in sql
    for column in ("snapshot_date", "total_tests", "automated_count", "total_suites", "suite_coverage"):
        assert f"{column} = " in sql
    assert "DO NOTHING" not in sql
    assert db.flushed == 1
    bound = statement.compile(dialect=postgresql.dialect()).params
    assert (bound["total_tests"], bound["automated_count"], bound["total_suites"]) == (4, 3, 2)
    assert bound["snapshot_day"] == D


@pytest.mark.asyncio
async def test_a_snapshot_is_three_bounded_statements_on_the_shared_fragments():
    exercised = SimpleNamespace(exercised_tests=7, suites=2)
    db = _Recorder([_Result([], scalar=9), _Result([], one=exercised), _Result([])])
    values = await svc.snapshot_values(db, PROJECT, D)
    assert values == {"catalogue_tests": 9, "exercised_tests": 7, "suites": 2, "suite_coverage": []}
    assert len(db.calls) == 3
    catalogue, counts, suites = (str(statement) for statement, _ in db.calls)
    assert "c.created_at < :snap_end" in catalogue
    assert "c.status <> 'deleted' OR c.deleted_observed_at >= :snap_end" in catalogue
    for sql in (counts, suites):
        assert effective_suite_sql() in sql
        assert "tr.created_at >= :snap_start" in sql and "tr.created_at < :snap_end" in sql
        assert "tr.project_id IN (SELECT id FROM projects WHERE is_active)" in sql
    assert "LIMIT 50" in suites and "'Unknown Suite'" in suites
    start, end = svc.snapshot_window(D)
    assert db.calls[1][1]["snap_start"] == start and db.calls[1][1]["snap_end"] == end


@pytest.mark.asyncio
async def test_untagged_tests_read_the_callout_s_window_through_the_shared_label_helper():
    db = _Recorder([_Result([])])
    body = await svc.untagged_tests(db, _scope(days=14), page=2, size=50)
    assert body == {"items": [], "total": 0, "page": 2, "size": 50, "window_days": 14}
    (statement, params), = db.calls
    sql = str(statement)
    assert f"({effective_suite_sql()} IS NULL OR LOWER(TRIM(coalesce(" in sql
    assert [params[f"suite_name_{i}"] for i in range(3)] == list(svc.UNTAGGED_LABELS)
    assert "tc.created_at >= :period_start" in sql  # coverage_stats' clock
    assert (params["ut_limit"], params["ut_offset"]) == (50, 50)
    assert params["ut_project"] == PROJECT
    assert "ORDER BY executions DESC, test_name, fp" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("page, size", [(0, 50), (1, 0), (1, 201)])
async def test_untagged_tests_refuse_a_page_outside_the_bounds(page, size):
    with pytest.raises(ValueError):
        await svc.untagged_tests(_Recorder(), _scope(), page=page, size=size)


@pytest.mark.asyncio
async def test_an_untagged_row_without_a_catalogue_entry_has_a_null_id():
    row = SimpleNamespace(
        total=3, fp="fp-1", executions=4, last_executed_at=datetime(2026, 9, 20, 6, tzinfo=timezone.utc),
        test_name="t", class_name=None, canonical_id=None, catalogue_suite_id=None, catalogue_suite=None,
    )
    body = await svc.untagged_tests(_Recorder([_Result([row])]), _scope(), page=1, size=50)
    assert body["total"] == 3
    assert body["items"] == [{
        "canonical_id": None, "test_fingerprint": "fp-1", "test_name": "t", "class_name": None,
        "catalogue_suite_id": None, "catalogue_suite": None, "executions": 4,
        "last_executed_at": "2026-09-20T06:00:00+00:00",
    }]


@pytest.mark.asyncio
async def test_a_page_past_the_end_still_says_the_total():
    """The total rides on a row of its own (LEFT JOIN of the page)."""
    row = SimpleNamespace(
        total=3, fp=None, executions=None, last_executed_at=None, test_name=None, class_name=None,
        canonical_id=None, catalogue_suite_id=None, catalogue_suite=None,
    )
    body = await svc.untagged_tests(_Recorder([_Result([row])]), _scope(), page=9, size=50)
    assert (body["total"], body["items"]) == (3, [])
