"""EXEC-02: suite execution records -- which wall clock wins, and what is never invented.

``test_runs.duration_ms`` is the summed test time. These pin how ``suite_execution_service`` turns a run's grouped
case rows plus the report's timing side channel into per-suite wall clocks and the run's wall clock:

* report timing beats case timestamps, which beat "summed only";
* a wall clock that is not known stays NULL (``derived_sum``), it is never the summed time passed off as one;
* parallel suites give a run wall clock below the summed time (parallelism > 1);
* the effective-suite rule is the analytics one (a live-stream run's label), not a copy;
* the writer flushes, never commits, locks the run first, and never writes ``duration_ms``.

SQL behaviour on a real database (upsert idempotency, the backfill cursor) is in
``tests/integration/test_suite_executions_postgres.py``.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import suite_execution_service as ses
from app.services.analytics_scope import effective_suite_sql

T0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _row(suite, *, count=1, timed=0, summed=None, started=None, finished=None,
         passed=None, failed=0, broken=0, skipped=0):
    return {
        "run_id": None,
        "suite": suite,
        "test_count": count,
        "timed_count": timed,
        "summed_ms": summed,
        "started_at": started,
        "finished_at": finished,
        "passed": count - failed - broken - skipped if passed is None else passed,
        "failed": failed,
        "broken": broken,
        "skipped": skipped,
    }


RUN = uuid.uuid4()


# ── Source precedence ────────────────────────────────────────────────────────


def test_case_timestamps_derive_the_suite_wall_clock():
    plan = ses.plan_run(RUN, [_row("Checkout", count=3, timed=3, summed=4200, started=_at(0), finished=_at(5))])
    (suite,) = plan.suites
    assert suite.source == ses.SOURCE_DERIVED
    assert (suite.started_at, suite.finished_at, suite.wall_ms) == (_at(0), _at(5), 5000)
    assert suite.summed_ms == 4200
    assert suite.parallelism == 0.84
    # The run's wall clock is the union of its suites' spans.
    assert plan.timing_source == ses.SOURCE_DERIVED
    assert (plan.exec_started_at, plan.exec_finished_at, plan.wall_clock_ms) == (_at(0), _at(5), 5000)


def test_report_suite_timing_beats_case_timestamps():
    timings = {
        "suites": [{"name": "Checkout", "started_at": _at(-1), "finished_at": _at(7), "wall_ms": 8000,
                    "setup_ms": 300, "teardown_ms": 200}],
    }
    plan = ses.plan_run(
        RUN, [_row("Checkout", count=3, timed=3, summed=4200, started=_at(0), finished=_at(5))], timings,
    )
    (suite,) = plan.suites
    assert suite.source == ses.SOURCE_REPORT
    assert (suite.started_at, suite.finished_at, suite.wall_ms) == (_at(-1), _at(7), 8000)
    # Suite-level fixtures come from the report only.
    assert (suite.setup_ms, suite.teardown_ms) == (300, 200)
    assert plan.timing_source == ses.SOURCE_DERIVED
    assert plan.wall_clock_ms == 8000


def test_report_run_timing_beats_the_union_of_suites():
    timings = {"run_started_at": _at(-2), "run_finished_at": _at(20), "run_wall_ms": None, "suites": []}
    plan = ses.plan_run(RUN, [_row("A", timed=1, summed=1000, started=_at(0), finished=_at(1))], timings)
    assert plan.timing_source == ses.SOURCE_REPORT
    assert (plan.exec_started_at, plan.exec_finished_at, plan.wall_clock_ms) == (_at(-2), _at(20), 22000)


def test_no_timing_anywhere_is_summed_only_and_the_wall_stays_null():
    """A JUnit file with no ``timestamp``: Test time is known, Wall clock is not reported."""
    plan = ses.plan_run(RUN, [_row("AuthSuite", count=6, summed=12456)])
    (suite,) = plan.suites
    assert suite.source == ses.SOURCE_DERIVED_SUM
    assert suite.wall_ms is None and suite.parallelism is None
    assert suite.started_at is None and suite.finished_at is None
    assert suite.summed_ms == 12456
    assert plan.timing_source == ses.SOURCE_DERIVED_SUM
    assert plan.wall_clock_ms is None and plan.exec_started_at is None
    assert plan.summed_ms == 12456
    assert plan.summary()["parallelism"] is None


def test_a_stated_suite_time_without_a_clock_times_the_suite_but_not_the_run():
    """``<testsuite time="12.456">`` without ``timestamp``: the suite has a wall clock, the run's cannot be placed."""
    plan = ses.plan_run(
        RUN, [_row("AuthSuite", count=6, summed=12456)], {"suites": [{"name": "AuthSuite", "wall_ms": 12456}]},
    )
    (suite,) = plan.suites
    assert (suite.source, suite.wall_ms, suite.parallelism) == (ses.SOURCE_REPORT, 12456, 1.0)
    assert plan.timing_source == ses.SOURCE_DERIVED_SUM
    assert plan.wall_clock_ms is None


def test_a_junit_timestamp_plus_time_times_suite_and_run():
    """The same file with ``timestamp`` added: Wall clock 12.5 s, Test time 12.5 s, Parallelism 1.0x."""
    plan = ses.plan_run(
        RUN,
        [_row("AuthSuite", count=6, summed=12456)],
        {"suites": [{"name": "AuthSuite", "started_at": T0, "wall_ms": 12456}]},
    )
    (suite,) = plan.suites
    assert suite.finished_at == T0 + timedelta(milliseconds=12456)
    assert (plan.timing_source, plan.wall_clock_ms) == (ses.SOURCE_DERIVED, 12456)
    assert ses.parallelism(plan.summed_ms, plan.wall_clock_ms) == 1.0


def test_parallel_suites_give_a_wall_clock_below_the_summed_time():
    """Two suites on two workers, overlapping: 9.0 s on the clock, 17.4 s of test time."""
    rows = [
        _row("checkout", count=10, timed=10, summed=9000, started=_at(0), finished=_at(9)),
        _row("search", count=8, timed=8, summed=8400, started=_at(0.2), finished=_at(8.6)),
    ]
    plan = ses.plan_run(RUN, rows)
    assert plan.wall_clock_ms == 9000
    assert plan.summed_ms == 17400
    assert plan.wall_clock_ms < plan.summed_ms
    assert ses.parallelism(plan.summed_ms, plan.wall_clock_ms) == 1.93


def test_parallel_tests_inside_one_suite_give_parallelism_above_one():
    plan = ses.plan_run(RUN, [_row("api", count=4, timed=4, summed=8000, started=_at(0), finished=_at(2))])
    assert plan.suites[0].parallelism == 4.0


def test_the_run_wall_clock_needs_every_suite_spanned():
    """A run wall clock from some of its suites would understate it: NULL instead."""
    rows = [
        _row("timed", timed=1, summed=1000, started=_at(0), finished=_at(1)),
        _row("untimed", summed=5000),
    ]
    plan = ses.plan_run(RUN, rows)
    assert [s.source for s in plan.suites] == [ses.SOURCE_DERIVED, ses.SOURCE_DERIVED_SUM]
    assert plan.timing_source == ses.SOURCE_DERIVED_SUM
    assert plan.wall_clock_ms is None


def test_cases_without_a_suite_get_no_row_but_count_toward_the_run():
    rows = [
        _row("A", timed=1, summed=1000, started=_at(0), finished=_at(1)),
        _row(None, count=2, summed=700),
    ]
    plan = ses.plan_run(RUN, rows)
    assert [s.suite_key for s in plan.suites] == ["a"]
    assert plan.summed_ms == 1700
    # The unsuited cases have no span, so the run wall clock is unknown.
    assert plan.wall_clock_ms is None


def test_a_backwards_span_is_not_believed():
    plan = ses.plan_run(RUN, [_row("A", timed=1, summed=10, started=_at(5), finished=_at(1))])
    assert plan.suites[0].source == ses.SOURCE_DERIVED_SUM
    assert plan.suites[0].wall_ms is None
    timings = {"suites": [{"name": "A", "started_at": _at(5), "finished_at": _at(1)}]}
    assert ses.plan_run(RUN, [_row("A", summed=10)], timings).suites[0].source == ses.SOURCE_DERIVED_SUM


def test_an_absurd_stated_wall_clock_is_not_reported():
    timings = {"run_wall_ms": ses.MAX_WALL_MS + 1, "suites": [{"name": "A", "wall_ms": ses.MAX_WALL_MS + 1}]}
    plan = ses.plan_run(RUN, [_row("A", summed=10)], timings)
    assert plan.suites[0].wall_ms is None and plan.wall_clock_ms is None


def test_a_zero_wall_clock_has_no_parallelism():
    assert ses.parallelism(100, 0) is None
    assert ses.parallelism(None, 100) is None
    assert ses.parallelism(10**9, 1) == ses.MAX_PARALLELISM


# ── One row per suite ────────────────────────────────────────────────────────


def test_two_spellings_of_one_suite_are_one_row():
    rows = [
        _row("Checkout", count=3, summed=300, failed=1),
        _row("checkout ", count=1, summed=100, skipped=1),
    ]
    plan = ses.plan_run(RUN, rows)
    (suite,) = plan.suites
    assert suite.suite_key == "checkout"
    assert suite.suite_name == "Checkout"  # the most common spelling names the row
    assert (suite.test_count, suite.summed_ms) == (4, 400)
    assert (suite.passed, suite.failed, suite.skipped) == (2, 1, 1)


def test_a_report_suite_no_case_belongs_to_adds_no_row():
    timings = {"suites": [{"name": "Elsewhere", "started_at": _at(0), "finished_at": _at(1)}]}
    plan = ses.plan_run(RUN, [_row("Mine", summed=5)], timings)
    assert [s.suite_key for s in plan.suites] == ["mine"]
    assert plan.suites[0].source == ses.SOURCE_DERIVED_SUM


def test_report_suites_are_matched_case_insensitively():
    timings = {"suites": [{"name": " CHECKOUT", "wall_ms": 900}]}
    plan = ses.plan_run(RUN, [_row("Checkout", summed=800)], timings)
    assert (plan.suites[0].source, plan.suites[0].wall_ms) == (ses.SOURCE_REPORT, 900)


def test_outcome_counts_add_up_to_the_test_count():
    row = _row("A", count=6, passed=2, failed=1, broken=1, skipped=1)  # one UNKNOWN
    (suite,) = ses.plan_run(RUN, [row]).suites
    assert suite.unknown == 1
    assert suite.passed + suite.failed + suite.broken + suite.skipped + suite.unknown == suite.test_count


def test_the_grouping_is_the_analytics_effective_suite_rule():
    """Not a sixth copy of the rule: the module renders ``analytics_scope.effective_suite_sql`` itself, and the
    suite is a select-list expression, so the WHERE is one indexed predicate (never an OR across tables)."""
    sql = str(ses._GROUPED_SQL)
    assert effective_suite_sql("tr", "tc") in sql
    where = sql.split("WHERE", 1)[1]
    assert " OR " not in where.upper()
    assert "tc.test_run_id IN" in where


# ── The writer ───────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


def _session(run_rows, grouped_rows):
    statements: list = []
    results = iter([_Result(run_rows), _Result(grouped_rows)])

    async def execute(stmt, params=None):
        statements.append((stmt, params))
        try:
            return next(results)
        except StopIteration:
            return _Result([])

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db, statements


def _sql(stmt) -> str:
    try:
        return str(stmt.compile(dialect=postgresql.dialect()))
    except Exception:  # noqa: BLE001 -- a TextClause compiles without a dialect too
        return str(stmt)


async def test_upsert_locks_the_run_flushes_and_never_commits():
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id, project_id=uuid.uuid4(), created_at=T0, branch="main", timing_source=None,
    )
    grouped = [dict(_row("Checkout", count=2, timed=2, summed=1500, started=_at(0), finished=_at(1)), run_id=run_id)]
    db, statements = _session([run], grouped)

    summary = await ses.upsert_for_run(db, run_id, None)

    assert summary["timing_source"] == ses.SOURCE_DERIVED
    assert summary["wall_clock_ms"] == 1000 and summary["parallelism"] == 1.5
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
    sqls = [_sql(stmt) for stmt, _ in statements]
    assert "FOR UPDATE OF test_runs" in sqls[0], "the run row is locked before anything is written"
    assert sqls[2].startswith("DELETE FROM suite_executions")
    assert "ON CONFLICT ON CONSTRAINT uq_suite_executions_run_suite DO UPDATE" in sqls[3]
    run_update = sqls[4]
    assert run_update.startswith("UPDATE test_runs SET")
    assert "wall_clock_ms" in run_update and "timing_source" in run_update
    # duration_ms keeps its summed meaning: the timing write never touches it, nor updated_at.
    assert "duration_ms" not in run_update
    assert "updated_at=test_runs.updated_at" in run_update.replace(" ", "")
    (params,) = [p for stmt, p in statements[4:5]]
    assert params[0]["b_wall"] == 1000 and params[0]["b_source"] == ses.SOURCE_DERIVED


async def test_upsert_of_a_missing_run_writes_nothing():
    db, statements = _session([], [])
    assert await ses.upsert_for_run(db, uuid.uuid4(), None) is None
    assert len(statements) == 1
    db.flush.assert_not_awaited()


async def test_a_run_with_no_cases_is_summed_only_with_no_rows():
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, project_id=uuid.uuid4(), created_at=T0, branch=None, timing_source=None)
    db, statements = _session([run], [])
    summary = await ses.upsert_for_run(db, run_id, None)
    assert summary["timing_source"] == ses.SOURCE_DERIVED_SUM
    assert summary["suites"] == 0 and summary["summed_ms"] is None
    sqls = [_sql(stmt) for stmt, _ in statements]
    assert not any("INSERT INTO suite_executions" in s for s in sqls)
    assert any(s.startswith("DELETE FROM suite_executions") for s in sqls), "stale rows of the run still go"


# ── The API ──────────────────────────────────────────────────────────────────


def test_the_runs_list_and_detail_carry_the_timing_fields():
    """``/runs`` and ``/runs/{id}`` serialise every ``TestRun`` column, so the four columns ride along; the
    documented run schema names them too."""
    from app.models.postgres import TestRun
    from app.models.schemas import TestRunSummary
    from app.services.runs_service import enrich_runs_with_release

    run = TestRun(
        id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="b-1", duration_ms=17400,
        wall_clock_ms=9000, exec_started_at=T0, exec_finished_at=_at(9), timing_source="derived",
    )
    (item,) = enrich_runs_with_release([run], {})
    assert (item["wall_clock_ms"], item["timing_source"], item["duration_ms"]) == (9000, "derived", 17400)
    assert (item["exec_started_at"], item["exec_finished_at"]) == (T0.isoformat(), _at(9).isoformat())
    fields = {"wall_clock_ms", "exec_started_at", "exec_finished_at", "timing_source"}
    assert fields <= set(TestRunSummary.model_fields)
    untimed = TestRunSummary.model_validate({
        "id": uuid.uuid4(), "project_id": uuid.uuid4(), "build_number": "b-2", "status": "PASSED",
        "total_tests": 0, "passed_tests": 0, "failed_tests": 0, "skipped_tests": 0, "broken_tests": 0,
        "created_at": T0,
    })
    assert untimed.wall_clock_ms is None and untimed.timing_source is None


# ── Backfill cursor ──────────────────────────────────────────────────────────


def test_the_cursor_round_trips_and_accumulates():
    run_id = uuid.uuid4()
    chunk = ses.BackfillChunk(scanned=500, written=480, skipped=20, last_created_at=T0, last_id=run_id, done=False)
    state = ses.advance_state({}, chunk)
    assert ses.cursor_from_state(state) == (T0, run_id)
    assert (state["scanned"], state["written"], state["skipped"], state["done"]) == (500, 480, 20, False)
    short = ses.BackfillChunk(scanned=3, written=3, skipped=0, last_created_at=_at(1), last_id=run_id, done=True)
    state = ses.advance_state(state, short)
    assert (state["scanned"], state["written"], state["done"]) == (503, 483, True)


def test_an_empty_chunk_keeps_the_position():
    run_id = uuid.uuid4()
    state = ses.advance_state({}, ses.BackfillChunk(1, 1, 0, T0, run_id, False))
    state = ses.advance_state(state, ses.BackfillChunk(0, 0, 0, None, None, True))
    assert ses.cursor_from_state(state) == (T0, run_id)
    assert state["done"] is True


@pytest.mark.parametrize("state", [None, {}, {"created_at": "not a date", "id": "x"}, {"created_at": T0.isoformat()}])
def test_an_unusable_cursor_starts_from_the_oldest_run(state):
    assert ses.cursor_from_state(state) is None


async def test_backfill_state_lives_in_redis_under_its_key():
    store: dict = {}
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: store.get(key)),
        set=AsyncMock(side_effect=lambda key, value: store.__setitem__(key, value)),
    )
    assert await ses.load_backfill_state(redis) == {}
    await ses.save_backfill_state(redis, {"done": True, "scanned": 4})
    assert ses.BACKFILL_CURSOR_KEY == "suite_exec.backfill_cursor"
    assert set(store) == {"suite_exec.backfill_cursor"}
    assert await ses.load_backfill_state(redis) == {"done": True, "scanned": 4}
    store[ses.BACKFILL_CURSOR_KEY] = b"{not json"
    assert await ses.load_backfill_state(redis) == {}


async def test_backfill_chunk_skips_runs_that_are_already_timed():
    first, second = uuid.uuid4(), uuid.uuid4()
    page = [SimpleNamespace(id=first, created_at=T0), SimpleNamespace(id=second, created_at=_at(1))]
    untimed = [SimpleNamespace(id=second, project_id=uuid.uuid4(), created_at=_at(1), branch=None, timing_source=None)]
    statements: list = []
    results = iter([_Result([]), _Result(page), _Result(untimed), _Result([])])

    async def execute(stmt, params=None):
        statements.append((stmt, params))
        return next(results, _Result([]))

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    chunk = await ses.backfill_chunk(db, (T0 - timedelta(days=1), uuid.UUID(int=0)), limit=2)

    assert (chunk.scanned, chunk.written, chunk.skipped, chunk.done) == (2, 1, 1, False)
    assert (chunk.last_created_at, chunk.last_id) == (_at(1), second)
    assert chunk.project_ids == [untimed[0].project_id], "only the projects of runs it timed"
    sqls = [_sql(stmt) for stmt, _ in statements]
    assert "lock_timeout" in sqls[0]
    assert "(test_runs.created_at, test_runs.id) >" in sqls[1]
    assert "ORDER BY test_runs.created_at, test_runs.id" in sqls[1]
    assert "timing_source IS NULL" in sqls[2] and "FOR UPDATE" in sqls[2]
    db.commit.assert_not_awaited()


# ── The Celery task ──────────────────────────────────────────────────────────


@pytest.fixture
def task_env(monkeypatch):
    """The task with Redis, the lock and the DB session faked; ``chunks`` scripts what each chunk returns."""
    import app.db.postgres as pg
    import app.db.redis_client as redis_client
    from app.services import cache_service
    from app.worker import tasks

    journal: list = []

    async def _bump(project_ids):
        journal.append(("bump", sorted(str(p) for p in project_ids)))

    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _bump)
    store: dict = {}
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: store.get(key)),
        set=AsyncMock(side_effect=lambda key, value: store.__setitem__(key, value)),
    )
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    lock = {"held": False}
    monkeypatch.setattr(tasks, "_is_duplicate", AsyncMock(side_effect=lambda *a, **k: lock["held"]))
    monkeypatch.setattr(tasks, "_release_duplicate_lock", AsyncMock())
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    session = MagicMock()
    session.commit = AsyncMock(side_effect=lambda: journal.append(("commit",)))
    session.rollback = AsyncMock()

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(pg, "AsyncSessionLocal", _Factory(), raising=False)
    script: list = []
    seen_cursors: list = []

    async def fake_chunk(db, after, limit):
        seen_cursors.append(after)
        return script.pop(0)

    monkeypatch.setattr(ses, "backfill_chunk", fake_chunk)
    return SimpleNamespace(
        store=store, lock=lock, session=session, script=script, cursors=seen_cursors, tasks=tasks, journal=journal,
    )


def _run_task(env, **kwargs):
    return env.tasks.backfill_suite_executions.apply(kwargs=kwargs).get()


def test_task_walks_chunks_until_a_short_one_and_marks_done(task_env):
    a, b = uuid.uuid4(), uuid.uuid4()
    project = uuid.uuid4()
    task_env.script.extend([
        ses.BackfillChunk(2, 2, 0, T0, a, False, [project]),
        ses.BackfillChunk(1, 0, 1, _at(1), b, True, []),
    ])
    result = _run_task(task_env, chunk_size=2, max_chunks=10)
    assert result["status"] == "done" and result["chunks"] == 2
    assert (result["scanned"], result["written"], result["skipped"]) == (3, 2, 1)
    # The second chunk resumed from the first one's last run; each chunk committed on its own, and the
    # projects it timed had their analytics epoch bumped AFTER that commit.
    assert task_env.cursors == [None, (T0, a)]
    assert task_env.journal == [("commit",), ("bump", [str(project)]), ("commit",), ("bump", [])]
    assert '"done": true' in task_env.store[ses.BACKFILL_CURSOR_KEY]


def test_task_does_nothing_once_done(task_env):
    task_env.store[ses.BACKFILL_CURSOR_KEY] = '{"done": true, "scanned": 7, "written": 7, "skipped": 0}'
    result = _run_task(task_env)
    assert result == {"status": "done", "chunks": 0, "scanned": 7, "written": 7, "skipped": 0}
    assert task_env.cursors == []


def test_task_stops_at_max_chunks_and_resumes_next_tick(task_env):
    a, b = uuid.uuid4(), uuid.uuid4()
    task_env.script.extend([ses.BackfillChunk(2, 2, 0, T0, a, False)])
    assert _run_task(task_env, chunk_size=2, max_chunks=1)["status"] == "max_chunks"
    task_env.script.extend([ses.BackfillChunk(1, 1, 0, _at(1), b, True)])
    assert _run_task(task_env, chunk_size=2, max_chunks=1)["status"] == "done"
    assert task_env.cursors == [None, (T0, a)]


def test_task_skips_while_another_pass_holds_the_lock(task_env):
    task_env.lock["held"] = True
    assert _run_task(task_env) == {"status": "locked"}
    assert task_env.cursors == []


def test_a_failed_chunk_rolls_back_and_keeps_the_cursor(task_env, monkeypatch):
    async def boom(db, after, limit):
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(ses, "backfill_chunk", boom)
    with pytest.raises(RuntimeError):
        _run_task(task_env)
    task_env.session.rollback.assert_awaited_once()
    assert ses.BACKFILL_CURSOR_KEY not in task_env.store


def test_the_backfill_is_scheduled_on_the_default_queue():
    from app.worker.celery_app import celery_app
    from app.worker.tasks import backfill_suite_executions

    entries = [e for e in celery_app.conf.beat_schedule.values() if e["task"] == backfill_suite_executions.name]
    assert len(entries) == 1
    assert backfill_suite_executions.queue == "default"
