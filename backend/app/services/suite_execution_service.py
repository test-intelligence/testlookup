"""EXEC-02: how long each suite of a run, and the run itself, actually took.

``test_runs.duration_ms`` is the SUM of the run's test durations ("Test time (summed)"); it says nothing about how long
the run took on the clock, because tests can run in parallel. This module writes the missing figures:

* one ``suite_executions`` row per run per effective suite: its wall clock, summed test time, outcome counts and where
  the wall clock came from;
* the run's own ``exec_started_at`` / ``exec_finished_at`` / ``wall_clock_ms`` / ``timing_source``.

Where a wall clock comes from, first available wins:

1. ``report``      -- the report stated it (suite or run timing in the :class:`ReportTimings` side channel);
2. ``derived``     -- TestLookup computed it: the earliest case start to the latest case finish (for the run, the union
   of its suites' spans, and only when every suite has one);
3. ``derived_sum`` -- no wall clock is known; only the summed time is stored. The wall stays NULL: a missing wall clock
   is "not reported", never the sum passed off as one (parallel tests make the sum larger than the wall).

Rules this module keeps:

* Suites are grouped by the EFFECTIVE suite, ``analytics_scope.effective_suite_sql`` (a live-stream run's label wins,
  otherwise the case's own suite), in one grouped query; ``suite_key`` is ``run_compare_service.normalize_suite_name``
  of it, so two spellings of one suite are one row.
* Writes are idempotent on ``(test_run_id, suite_key)``; a suite the run no longer has loses its row.
* It flushes and never commits (transaction ratchet): the caller owns the transaction.
* ``test_runs.duration_ms`` is never written here; it keeps its summed meaning.
* The run row is locked (``FOR UPDATE``) before anything is written, so a finalize and the backfill serialise on the
  run instead of deadlocking on each other's suite rows.

Nothing here is called from ingestion or finalize directly; the pipeline wires :func:`upsert_for_run` into its own
step, and the one-shot Celery task ``backfill_suite_executions`` drives :func:`backfill_chunk`.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence, cast

import structlog
from sqlalchemy import Table, bindparam, delete, func, literal, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import SuiteExecution, TestRun
from app.services.analytics_scope import effective_suite_sql
from app.services.report_timings import ReportTimings, SuiteTiming, non_negative_ms, to_utc
from app.services.run_compare_service import normalize_suite_name

logger = structlog.get_logger("services.suite_execution")

#: ``suite_executions.source`` / ``test_runs.timing_source`` vocabulary (see the module docstring).
SOURCE_REPORT = "report"
SOURCE_DERIVED = "derived"
SOURCE_DERIVED_SUM = "derived_sum"
SOURCES: tuple[str, ...] = (SOURCE_REPORT, SOURCE_DERIVED, SOURCE_DERIVED_SUM)

#: A stated wall clock longer than this is not believed (a unit mix-up or a corrupt report), it is "not reported".
MAX_WALL_MS = 7 * 24 * 3600 * 1000
#: ``suite_executions.parallelism`` is rounded to 2 places and capped here.
MAX_PARALLELISM = 9999.99
#: Rows per INSERT (asyncpg allows 32767 bind parameters; a row binds 24).
_INSERT_BATCH = 500

#: Backfill: runs per chunk, the Redis cursor key and the per-pass lock.
BACKFILL_CHUNK_SIZE = 500
BACKFILL_CURSOR_KEY = "suite_exec.backfill_cursor"
BACKFILL_LOCK_KEY = "suite_exec.backfill_lock"
#: The backfill stops a pass once its own resident memory grew by this much (it then resumes on the next tick).
BACKFILL_RSS_GROWTH_CAP_BYTES = 256 * 1024 * 1024
#: The backfill never waits long for a run row a live writer holds; the chunk is retried on the next tick.
BACKFILL_LOCK_TIMEOUT = "5s"


# One grouped read over the run's cases. The suite is the EFFECTIVE suite (the analytics rule, not a copy of it);
# the CASE is in the select list, so the WHERE stays a single indexed predicate (ix_test_cases_run_suite).
_GROUPED_SQL = text(
    "SELECT tc.test_run_id AS run_id, "
    f"{effective_suite_sql('tr', 'tc')} AS suite, "
    "COUNT(*) AS test_count, "
    "COUNT(*) FILTER (WHERE tc.started_at IS NOT NULL AND tc.finished_at IS NOT NULL) AS timed_count, "
    "SUM(tc.duration_ms) AS summed_ms, "
    "MIN(tc.started_at) AS started_at, "
    "MAX(tc.finished_at) AS finished_at, "
    "COUNT(*) FILTER (WHERE tc.status = 'PASSED') AS passed, "
    "COUNT(*) FILTER (WHERE tc.status = 'FAILED') AS failed, "
    "COUNT(*) FILTER (WHERE tc.status = 'BROKEN') AS broken, "
    "COUNT(*) FILTER (WHERE tc.status = 'SKIPPED') AS skipped "
    "FROM test_cases tc "
    "JOIN test_runs tr ON tr.id = tc.test_run_id "
    "WHERE tc.test_run_id IN :run_ids "
    "GROUP BY tc.test_run_id, 2"
).bindparams(bindparam("run_ids", expanding=True))


# ── Planning (pure: no I/O) ──────────────────────────────────────────────────


@dataclass
class SuiteAggregate:
    """The cases of one effective suite of one run (several raw spellings of a suite merge here)."""

    suite_name: str
    test_count: int = 0
    timed_count: int = 0
    summed_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    passed: int = 0
    failed: int = 0
    broken: int = 0
    skipped: int = 0
    unknown: int = 0
    # How many cases carried each raw spelling: the most common one names the row.
    spellings: dict[str, int] = field(default_factory=dict)

    def add(self, row: Any) -> None:
        count = int(_get(row, "test_count") or 0)
        passed = int(_get(row, "passed") or 0)
        failed = int(_get(row, "failed") or 0)
        broken = int(_get(row, "broken") or 0)
        skipped = int(_get(row, "skipped") or 0)
        self.test_count += count
        self.timed_count += int(_get(row, "timed_count") or 0)
        self.passed += passed
        self.failed += failed
        self.broken += broken
        self.skipped += skipped
        # Anything outside the four statuses is unknown, so the outcome counts always add up to test_count.
        self.unknown += max(0, count - passed - failed - broken - skipped)
        summed = _get(row, "summed_ms")
        if summed is not None:
            self.summed_ms = (self.summed_ms or 0) + int(summed)
        self.started_at = _min_dt(self.started_at, to_utc(_get(row, "started_at")))
        self.finished_at = _max_dt(self.finished_at, to_utc(_get(row, "finished_at")))
        raw = _get(row, "suite")
        if raw:
            self.spellings[str(raw)] = self.spellings.get(str(raw), 0) + count
            self.suite_name = sorted(self.spellings.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


@dataclass
class SuitePlan:
    suite_name: str
    suite_key: str
    source: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    wall_ms: Optional[int]
    summed_ms: Optional[int]
    setup_ms: Optional[int]
    teardown_ms: Optional[int]
    test_count: int
    timed_count: int
    passed: int
    failed: int
    broken: int
    skipped: int
    unknown: int
    parallelism: Optional[float]


@dataclass
class RunPlan:
    run_id: uuid.UUID
    timing_source: str
    exec_started_at: Optional[datetime]
    exec_finished_at: Optional[datetime]
    wall_clock_ms: Optional[int]
    summed_ms: Optional[int]
    suites: list[SuitePlan]

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "timing_source": self.timing_source,
            "wall_clock_ms": self.wall_clock_ms,
            "summed_ms": self.summed_ms,
            "exec_started_at": self.exec_started_at.isoformat() if self.exec_started_at else None,
            "exec_finished_at": self.exec_finished_at.isoformat() if self.exec_finished_at else None,
            "parallelism": parallelism(self.summed_ms, self.wall_clock_ms),
            "suites": len(self.suites),
        }


def _get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(key)
    return getattr(row, key, None)


def _min_dt(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _max_dt(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _span_ms(started: Optional[datetime], finished: Optional[datetime]) -> Optional[int]:
    """Milliseconds from ``started`` to ``finished``; None when either is missing or the span runs backwards."""
    if started is None or finished is None or finished < started:
        return None
    return int(round((finished - started).total_seconds() * 1000))


def _bounded_wall(ms: Optional[int]) -> Optional[int]:
    return ms if ms is not None and 0 <= ms <= MAX_WALL_MS else None


def parallelism(summed_ms: Optional[int], wall_ms: Optional[int]) -> Optional[float]:
    """Summed test time over wall clock, 2 places. None when the wall is unknown or zero (never a made-up 1.0)."""
    if summed_ms is None or not wall_ms or wall_ms <= 0:
        return None
    return min(round(summed_ms / wall_ms, 2), MAX_PARALLELISM)


def _stated_timing(
    started: Any, finished: Any, wall: Any,
) -> Optional[tuple[Optional[datetime], Optional[datetime], int]]:
    """A report's ``(started, finished, wall_ms)`` when it states a wall clock, else None.

    A stated ``wall_ms`` wins over the span of the stated timestamps; with only one timestamp and a wall the other
    end follows from it. A start alone states no wall clock.
    """
    started_at = to_utc(started)
    finished_at = to_utc(finished)
    if started_at is not None and finished_at is not None and finished_at < started_at:
        started_at = finished_at = None  # incoherent: believe neither
    wall_ms = _bounded_wall(non_negative_ms(wall))
    if wall_ms is None:
        wall_ms = _bounded_wall(_span_ms(started_at, finished_at))
    if wall_ms is None:
        return None
    if started_at is None and finished_at is not None:
        started_at = finished_at - timedelta(milliseconds=wall_ms)
    elif finished_at is None and started_at is not None:
        finished_at = started_at + timedelta(milliseconds=wall_ms)
    return started_at, finished_at, wall_ms


def _report_suites_by_key(report_timings: Optional[ReportTimings]) -> dict[str, dict[str, Any]]:
    """The report's suite timings keyed like the rows. Two entries for one key (an archive that ran the suite in two
    files) combine only when both carry a span: earliest start to latest finish; otherwise the key states nothing."""
    out: dict[str, dict[str, Any]] = {}
    for suite in (report_timings or {}).get("suites") or []:
        key = normalize_suite_name(suite.get("name"))
        if not key:
            continue
        entry = _suite_entry(suite)
        if key not in out:
            out[key] = entry
            continue
        prev = out[key]
        both_spanned = all(e["started_at"] and e["finished_at"] for e in (prev, entry))
        out[key] = {
            "started_at": _min_dt(prev["started_at"], entry["started_at"]) if both_spanned else None,
            "finished_at": _max_dt(prev["finished_at"], entry["finished_at"]) if both_spanned else None,
            "wall_ms": None,
            "setup_ms": _sum_optional(prev["setup_ms"], entry["setup_ms"]),
            "teardown_ms": _sum_optional(prev["teardown_ms"], entry["teardown_ms"]),
        }
    return out


def _suite_entry(suite: SuiteTiming) -> dict[str, Any]:
    return {
        "started_at": to_utc(suite.get("started_at")),
        "finished_at": to_utc(suite.get("finished_at")),
        "wall_ms": non_negative_ms(suite.get("wall_ms")),
        "setup_ms": non_negative_ms(suite.get("setup_ms")),
        "teardown_ms": non_negative_ms(suite.get("teardown_ms")),
    }


def _sum_optional(a: Optional[int], b: Optional[int]) -> Optional[int]:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def aggregate_rows(rows: Iterable[Any]) -> tuple[dict[str, SuiteAggregate], Optional[SuiteAggregate]]:
    """Fold the grouped rows of ONE run into suites keyed by ``suite_key``, plus the cases with no suite at all."""
    suites: dict[str, SuiteAggregate] = {}
    unsuited: Optional[SuiteAggregate] = None
    for row in rows:
        key = normalize_suite_name(_get(row, "suite"))
        if not key:
            if unsuited is None:
                unsuited = SuiteAggregate(suite_name="")
            unsuited.add(row)
            continue
        agg = suites.get(key)
        if agg is None:
            agg = suites[key] = SuiteAggregate(suite_name=str(_get(row, "suite")).strip())
        agg.add(row)
    return suites, unsuited


def plan_run(
    run_id: uuid.UUID,
    rows: Iterable[Any],
    report_timings: Optional[ReportTimings] = None,
) -> RunPlan:
    """What to write for one run, from its grouped case rows and the report's timing side channel."""
    suites, unsuited = aggregate_rows(rows)
    report_suites = _report_suites_by_key(report_timings)
    unmatched = sorted(set(report_suites) - set(suites))
    if unmatched:
        # The case-derived row stands; a report suite no case belongs to never becomes a second row.
        logger.debug("suite_execution_report_suite_unmatched", run_id=str(run_id), suites=unmatched[:20])

    plans: list[SuitePlan] = []
    for key in sorted(suites):
        agg = suites[key]
        rep = report_suites.get(key)
        stated = _stated_timing(rep["started_at"], rep["finished_at"], rep["wall_ms"]) if rep else None
        derived_wall = _bounded_wall(_span_ms(agg.started_at, agg.finished_at))
        started: Optional[datetime]
        finished: Optional[datetime]
        wall: Optional[int]
        if stated is not None:
            source = SOURCE_REPORT
            started, finished, wall = stated
        elif derived_wall is not None:
            source = SOURCE_DERIVED
            started, finished, wall = agg.started_at, agg.finished_at, derived_wall
        else:
            source = SOURCE_DERIVED_SUM
            started = finished = wall = None
        plans.append(
            SuitePlan(
                suite_name=agg.suite_name[:500],
                suite_key=key[:500],
                source=source,
                started_at=started,
                finished_at=finished,
                wall_ms=wall,
                summed_ms=agg.summed_ms,
                # Suite-level fixtures (before-all / after-all) as the report states them; per-test setup and
                # teardown stay on the test rows.
                setup_ms=rep["setup_ms"] if rep else None,
                teardown_ms=rep["teardown_ms"] if rep else None,
                test_count=agg.test_count,
                timed_count=agg.timed_count,
                passed=agg.passed,
                failed=agg.failed,
                broken=agg.broken,
                skipped=agg.skipped,
                unknown=agg.unknown,
                parallelism=parallelism(agg.summed_ms, wall),
            )
        )

    run_summed = _sum_optional(
        _sum_all(p.summed_ms for p in plans),
        unsuited.summed_ms if unsuited else None,
    )
    timings = report_timings or {}
    stated_run = _stated_timing(
        timings.get("run_started_at"), timings.get("run_finished_at"), timings.get("run_wall_ms"),
    )
    run_started: Optional[datetime] = None
    run_finished: Optional[datetime] = None
    run_wall: Optional[int] = None
    if stated_run is not None:
        run_source = SOURCE_REPORT
        run_started, run_finished, run_wall = stated_run
    else:
        run_source = SOURCE_DERIVED_SUM
        spans: list[tuple[Optional[datetime], Optional[datetime]]] = [(p.started_at, p.finished_at) for p in plans]
        if unsuited is not None and unsuited.test_count:
            spans.append((unsuited.started_at, unsuited.finished_at))
        # The union of the suites' spans, only when EVERY suite has one: a run wall clock from some of its suites
        # would understate it.
        if spans and all(s is not None and f is not None for s, f in spans):
            run_started = min(s for s, _ in spans if s is not None)
            run_finished = max(f for _, f in spans if f is not None)
            run_wall = _bounded_wall(_span_ms(run_started, run_finished))
            if run_wall is not None:
                run_source = SOURCE_DERIVED
            else:
                run_started = run_finished = None
    return RunPlan(
        run_id=run_id,
        timing_source=run_source,
        exec_started_at=run_started,
        exec_finished_at=run_finished,
        wall_clock_ms=run_wall,
        summed_ms=run_summed,
        suites=plans,
    )


def _sum_all(values: Iterable[Optional[int]]) -> Optional[int]:
    total: Optional[int] = None
    for value in values:
        if value is not None:
            total = (total or 0) + value
    return total


# ── Writing ──────────────────────────────────────────────────────────────────

_UPDATE_COLUMNS = (
    "suite_name", "started_at", "finished_at", "wall_ms", "summed_ms", "setup_ms", "teardown_ms",
    "test_count", "timed_count", "passed", "failed", "broken", "skipped", "unknown", "parallelism",
    "source", "run_created_at", "branch",
)


async def _write_plans(db: AsyncSession, runs: dict[uuid.UUID, Any], plans: Sequence[RunPlan]) -> None:
    if not plans:
        return
    run_ids = [plan.run_id for plan in plans]
    keep = [(plan.run_id, suite.suite_key) for plan in plans for suite in plan.suites]
    stale = delete(SuiteExecution).where(SuiteExecution.test_run_id.in_(run_ids))
    if keep:
        stale = stale.where(tuple_(SuiteExecution.test_run_id, SuiteExecution.suite_key).not_in(keep))
    await db.execute(stale)

    rows: list[dict[str, Any]] = []
    for plan in plans:
        run = runs[plan.run_id]
        for suite in plan.suites:
            rows.append({
                "id": uuid.uuid4(),
                "project_id": _get(run, "project_id"),
                "test_run_id": plan.run_id,
                "suite_key": suite.suite_key,
                "suite_name": suite.suite_name,
                "started_at": suite.started_at,
                "finished_at": suite.finished_at,
                "wall_ms": suite.wall_ms,
                "summed_ms": suite.summed_ms,
                "setup_ms": suite.setup_ms,
                "teardown_ms": suite.teardown_ms,
                "test_count": suite.test_count,
                "timed_count": suite.timed_count,
                "passed": suite.passed,
                "failed": suite.failed,
                "broken": suite.broken,
                "skipped": suite.skipped,
                "unknown": suite.unknown,
                "parallelism": suite.parallelism,
                "source": suite.source,
                "run_created_at": _get(run, "created_at"),
                "branch": _get(run, "branch"),
            })
    for start in range(0, len(rows), _INSERT_BATCH):
        insert_stmt = pg_insert(SuiteExecution).values(rows[start:start + _INSERT_BATCH])
        upsert = insert_stmt.on_conflict_do_update(
            constraint="uq_suite_executions_run_suite",
            set_={
                **{column: insert_stmt.excluded[column] for column in _UPDATE_COLUMNS},
                "updated_at": func.now(),
            },
        )
        await db.execute(upsert)

    table = cast(Table, TestRun.__table__)
    run_update = (
        update(table)
        .where(table.c.id == bindparam("b_run_id"))
        .values(
            exec_started_at=bindparam("b_started"),
            exec_finished_at=bindparam("b_finished"),
            wall_clock_ms=bindparam("b_wall"),
            timing_source=bindparam("b_source"),
            # A timing write is not an edit of the run: keep its updated_at (and never touch duration_ms).
            updated_at=table.c.updated_at,
        )
    )
    await db.execute(
        run_update,
        [
            {
                "b_run_id": plan.run_id,
                "b_started": plan.exec_started_at,
                "b_finished": plan.exec_finished_at,
                "b_wall": plan.wall_clock_ms,
                "b_source": plan.timing_source,
            }
            for plan in plans
        ],
    )


async def _lock_runs(db: AsyncSession, run_ids: Sequence[uuid.UUID], *, only_untimed: bool) -> dict[uuid.UUID, Any]:
    stmt = (
        select(TestRun.id, TestRun.project_id, TestRun.created_at, TestRun.branch, TestRun.timing_source)
        .where(TestRun.id.in_(list(run_ids)))
        .order_by(TestRun.id)
        .with_for_update(of=TestRun)
    )
    if only_untimed:
        # Re-evaluated after the lock is granted: a run a finalize timed meanwhile drops out here.
        stmt = stmt.where(TestRun.timing_source.is_(None))
    return {row.id: row for row in (await db.execute(stmt)).all()}


async def _grouped_rows(db: AsyncSession, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[Any]]:
    by_run: dict[uuid.UUID, list[Any]] = {run_id: [] for run_id in run_ids}
    if not run_ids:
        return by_run
    result = await db.execute(_GROUPED_SQL, {"run_ids": list(run_ids)})
    for row in result.all():
        by_run.setdefault(_get(row, "run_id"), []).append(row)
    return by_run


async def upsert_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    report_timings: Optional[ReportTimings] = None,
) -> Optional[dict[str, Any]]:
    """Write the run's suite execution rows and its run-level timing. Flushes; the caller commits.

    Returns a summary (wall, summed, parallelism, source, suite count), or None when the run does not exist.
    """
    runs = await _lock_runs(db, [run_id], only_untimed=False)
    if run_id not in runs:
        return None
    rows = (await _grouped_rows(db, [run_id])).get(run_id, [])
    plan = plan_run(run_id, rows, report_timings)
    await _write_plans(db, runs, [plan])
    await db.flush()
    return plan.summary()


# ── Backfill ─────────────────────────────────────────────────────────────────


@dataclass
class BackfillChunk:
    scanned: int
    written: int
    skipped: int
    last_created_at: Optional[datetime]
    last_id: Optional[uuid.UUID]
    done: bool
    #: Projects whose runs this chunk timed: their analytics epochs are bumped after the commit.
    project_ids: list[uuid.UUID] = field(default_factory=list)


async def backfill_chunk(
    db: AsyncSession,
    after: Optional[tuple[datetime, uuid.UUID]],
    limit: int = BACKFILL_CHUNK_SIZE,
) -> BackfillChunk:
    """Time the next ``limit`` runs in ``(created_at, id)`` order after ``after``. Flushes; the caller commits.

    A run that already has a ``timing_source`` (a finalize timed it, or an earlier pass did) is skipped, so a pass
    never overwrites report timing with derived figures, and re-running from any cursor is harmless.
    """
    await db.execute(text(f"SET LOCAL lock_timeout = '{BACKFILL_LOCK_TIMEOUT}'"))
    page = select(TestRun.id, TestRun.created_at).order_by(TestRun.created_at, TestRun.id).limit(limit)
    if after is not None:
        page = page.where(tuple_(TestRun.created_at, TestRun.id) > tuple_(literal(after[0]), literal(after[1])))
    scanned = (await db.execute(page)).all()
    if not scanned:
        return BackfillChunk(0, 0, 0, after[0] if after else None, after[1] if after else None, True)
    runs = await _lock_runs(db, [row.id for row in scanned], only_untimed=True)
    grouped = await _grouped_rows(db, list(runs))
    plans = [plan_run(run_id, grouped.get(run_id, [])) for run_id in runs]
    await _write_plans(db, runs, plans)
    await db.flush()
    last = scanned[-1]
    return BackfillChunk(
        scanned=len(scanned),
        written=len(plans),
        skipped=len(scanned) - len(plans),
        last_created_at=last.created_at,
        last_id=last.id,
        done=len(scanned) < limit,
        project_ids=sorted({_get(run, "project_id") for run in runs.values()}, key=str),
    )


def cursor_from_state(state: Optional[dict[str, Any]]) -> Optional[tuple[datetime, uuid.UUID]]:
    """The keyset position stored in the Redis cursor, or None to start from the oldest run."""
    if not state or not state.get("created_at") or not state.get("id"):
        return None
    try:
        created_at = datetime.fromisoformat(str(state["created_at"]))
        return (created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)), uuid.UUID(
            str(state["id"])
        )
    except (TypeError, ValueError):
        logger.warning("suite_execution_backfill_cursor_unreadable", state=str(state)[:200])
        return None


def advance_state(state: Optional[dict[str, Any]], chunk: BackfillChunk) -> dict[str, Any]:
    """The cursor state after ``chunk``: position, running totals, and ``done`` once a short chunk was read."""
    state = dict(state or {})
    if chunk.last_created_at is not None and chunk.last_id is not None:
        state["created_at"] = chunk.last_created_at.isoformat()
        state["id"] = str(chunk.last_id)
    state["scanned"] = int(state.get("scanned") or 0) + chunk.scanned
    state["written"] = int(state.get("written") or 0) + chunk.written
    state["skipped"] = int(state.get("skipped") or 0) + chunk.skipped
    state["done"] = bool(chunk.done)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    return state


async def load_backfill_state(redis: Any) -> dict[str, Any]:
    raw = await redis.get(BACKFILL_CURSOR_KEY)
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        value = json.loads(raw)
    except ValueError:
        logger.warning("suite_execution_backfill_state_unreadable")
        return {}
    return value if isinstance(value, dict) else {}


async def save_backfill_state(redis: Any, state: dict[str, Any]) -> None:
    await redis.set(BACKFILL_CURSOR_KEY, json.dumps(state, sort_keys=True))


def current_rss_bytes() -> Optional[int]:
    """This process's resident memory (Linux ``/proc``), or None where it cannot be read."""
    sysconf = getattr(os, "sysconf", None)  # POSIX only
    if sysconf is None:
        return None
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * int(sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        return None
