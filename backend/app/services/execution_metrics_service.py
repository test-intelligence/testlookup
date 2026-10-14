"""EXEC-03: execution metrics -- how long runs, suites and tests take, read three ways.

* :func:`run_timing` -- ONE run: its wall clock / summed time / parallelism (EXEC-02's ``test_runs`` columns), phase
  totals, its ``suite_executions`` rows, the slowest tests (``ix_test_cases_run_duration``) with their
  ``perf_baselines``, worker lanes, a bounded timeline for the waterfall, and its EXEC-05 duration findings.
* :func:`suite_history` -- ONE suite of one project: its wall clock and summed time per run over a window
  (``suite_executions`` on ``(project_id, suite_key, run_created_at)``), plus window percentiles.
* :func:`project_execution_metrics` -- ONE project over a window: execution time per UTC day, p50/p95 per suite
  against the previous window, the biggest suite and test regressions, the slowest tests, and a duration histogram.

The test-history additions (timing per point, window percentiles, the baseline) live in
``test_case_history_service``.

Rules every statement here keeps:

* **Scope first.** The analytics reads take a resolved :class:`AnalyticsScope` (project pinned by the route policy,
  every release authorised) and every statement carries the tenant fragment (``analytics_service._tenant_filter``:
  the project pin plus the ``is_active`` guard) on ``test_runs``; ``suite_executions`` reads also pin
  ``se.project_id``. A ``test_fingerprint`` is never read without the project (fingerprints are not salted).
* **One suite rule.** Suites of test rows are the EFFECTIVE suite (``analytics_scope.effective_suite_sql`` /
  ``suite_filter_sql``); suites of ``suite_executions`` rows are their ``suite_key``, which EXEC-02 already wrote
  from that rule. No suite-match clause is spelled here (``tests/test_analytics_scope_ratchet.py``).
* **No OR across tables, no null-tolerant binds.** Every optional filter is a conditional fragment.
* **Bounded.** Every list has a LIMIT and every window a cap; the per-test regression read is bounded by
  ``perf_baselines`` candidates instead of percentiles over every test of the project.
* **Null is "not measured".** A missing wall clock, timestamp, phase or baseline is ``None`` with a reason on the
  block, never 0 (and 0 is never shown as "not measured").

Pure read: nothing here writes, flushes or commits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

import structlog
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError
from app.models.postgres import PerfBaseline, SuiteExecution, TestRun, TestStatus
from app.services import duration_regression_service as regressions
from app.services import perf_regression_service as perf
from app.services.analytics_scope import (
    AnalyticsScope,
    effective_suite_sql,
    release_filter_sql,
    scoped_text,
    suite_filter_sql,
    suite_keys,
)
# The tenant rule, spelled once for every raw-SQL analytics query (the pin, the membership set, the fail-closed
# empty set and the ``is_active`` guard).
from app.services.analytics_service import _tenant_filter as tenant_filter_sql
from app.services.chart_data_service import window_start
from app.services.suite_execution_service import parallelism as parallelism_of

logger = structlog.get_logger("services.execution_metrics")

# ── Limits ───────────────────────────────────────────────────────────────────

#: /runs/{id}/timing
DEFAULT_SLOWEST = 20
MAX_SLOWEST = 100
DEFAULT_TIMELINE_LIMIT = 2000
MAX_TIMELINE_LIMIT = 2000
MAX_RUN_SUITES = 500
MAX_LANES = 64

#: /analytics/execution/suite-history
SUITE_HISTORY_DEFAULT_DAYS = 90
SUITE_HISTORY_DEFAULT_LIMIT = 100
SUITE_HISTORY_MAX_LIMIT = 500

#: /analytics/execution-metrics
METRICS_DEFAULT_DAYS = 30
METRICS_DEFAULT_TOP = 20
METRICS_MAX_TOP = 100
METRICS_DEFAULT_SUITE_LIMIT = 50
METRICS_MAX_SUITE_LIMIT = 200
#: Suite x branch histories judged for regressions, newest first.
REGRESSION_PAIR_CAP = 200
#: Rows of one suite x branch history read for the rule: the judged window and one step back.
REGRESSION_HISTORY_DEPTH = 1 + regressions.SUITE_RECENT_RUNS + regressions.SUITE_BASELINE_RUNS
#: Baselined tests whose window p50 is read (the most slowed by their last observation).
TEST_REGRESSION_CANDIDATES = 100
TEST_MIN_SAMPLES = perf._MIN_SAMPLES_FOR_SPIKE
TEST_RATIO = regressions.SUITE_RATIO

MAX_BRANCH_LENGTH = 255

#: TRND-09 histogram: fixed, log-spaced lower edges (ms); the last bucket is open-ended.
HISTOGRAM_EDGES_MS: tuple[int, ...] = (
    0, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000,
)

# ── Reasons (shown to people as plain text) ───────────────────────────────────

REASON_NO_RESULTS = "this run has no test results"
REASON_NO_TIMESTAMPS = "report carried no timestamps"
REASON_NO_PHASES = "report carried no setup/teardown split"
REASON_NO_WORKERS = "the report did not say which worker ran each test"
REASON_NO_DURATIONS = "no test in this run reported a duration"
REASON_NO_SUITE_ROWS = "suite timing was not recorded for this run yet"
REASON_SUITE_NOT_IN_WINDOW = "no execution of this suite in the window"
REASON_NO_RUNS = "no run in this scope in the window"
REASON_NO_SUITES_IN_WINDOW = "no suite ran in the window"
REASON_NO_TEST_DURATIONS = "no test execution in the window carried a duration"
REASON_DENIED = "No project in this scope is readable by the caller."

_FAILED = TestStatus.FAILED.value
_PASSED = TestStatus.PASSED.value
_EFFECTIVE_SUITE = effective_suite_sql("tr", "tc")


# ── Small helpers (pure) ──────────────────────────────────────────────────────


def _int_param(value: Any, name: str, code: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AnalyticsQueryError(
            code, f"{name} is an integer, {low}-{high}", param=name, allowed={"min": low, "max": high},
        )
    return int(value)


def parse_branch(value: Any) -> Optional[str]:
    """The optional branch filter: absent or empty is "every branch"; 1-255 characters, matched exactly (a git ref's
    case is meaningful). The value is never echoed in the refusal."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnalyticsQueryError(
            "branch_length", f"branch is 1-{MAX_BRANCH_LENGTH} characters", param="branch",
            allowed={"min_length": 1, "max_length": MAX_BRANCH_LENGTH},
        )
    if value == "":
        return None
    if len(value) > MAX_BRANCH_LENGTH:
        raise AnalyticsQueryError(
            "branch_length", f"branch is 1-{MAX_BRANCH_LENGTH} characters", param="branch",
            allowed={"min_length": 1, "max_length": MAX_BRANCH_LENGTH},
        )
    return value


def single_suite_key(scope: AnalyticsScope) -> str:
    """The ONE suite a suite-history request is about (``suite_key`` form: trimmed, lower-cased)."""
    keys = suite_keys(scope.suite_names)
    if not keys:
        raise AnalyticsQueryError(
            "missing_parameter", "suite_name is required on this route: name the suite to chart",
            param="suite_name", allowed={"min": 1, "max": 1},
        )
    if len(keys) > 1:
        raise AnalyticsQueryError(
            "suite_single_valued", "suite_name is single-valued on this route: send exactly one suite",
            param="suite_name", allowed={"max": 1},
        )
    return keys[0]


def _utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _span_ms(started: Optional[datetime], finished: Optional[datetime]) -> Optional[int]:
    if started is None or finished is None or finished < started:
        return None
    return int(round((finished - started).total_seconds() * 1000))


def _opt_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _opt_float(value: Any, places: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _share(part: Optional[int], *parts: Optional[int]) -> Optional[float]:
    """``part`` over the sum of the reported parts, 0-1; None when nothing was reported."""
    total = sum(p for p in parts if p is not None)
    if part is None or total <= 0:
        return None
    return round(part / total, 4)


def _change_pct(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None or previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 1)


def percentiles(n: Any, p50: Any, p95: Any, *, reason: str) -> dict[str, Any]:
    """An ``ExecutionPercentiles`` block; ``measured`` only over a non-empty sample."""
    count = int(n or 0)
    measured = count > 0 and p50 is not None
    return {
        "n": count,
        "p50_ms": _opt_float(p50) if measured else None,
        "p95_ms": _opt_float(p95) if measured else None,
        "measured": measured,
        "reason": None if measured else reason,
    }


def _worker_sort_key(worker_id: Optional[str]) -> tuple:
    """Numeric worker ids in number order, other ids by text, the unnamed lane last."""
    if worker_id is None:
        return (2, 0, "")
    text_id = str(worker_id)
    if text_id.isdigit():
        return (0, int(text_id), text_id)
    return (1, 0, text_id)


def _get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(key)
    return getattr(row, key, None)


def _branch_sql(params: dict, branch: Optional[str], column: str) -> str:
    if branch is None:
        return ""
    params["branch"] = branch
    return f"AND {column} = :branch"


def _suite_key_sql(params: dict, keys: Sequence[str], column: str = "se.suite_key") -> str:
    """``AND`` the ``suite_executions`` row is one of the requested suites. The column already holds the normalised
    key EXEC-02 wrote from the effective-suite rule, so this is a plain equality on an indexed column."""
    if not keys:
        return ""
    if len(keys) == 1:
        params["suite_key"] = keys[0]
        return f"AND {column} = :suite_key"
    params["suite_keys"] = list(keys)
    return f"AND {column} IN :suite_keys"


def _baseline_block(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    sample_count = int(_get(row, "sample_count") or 0)
    if sample_count <= 0:
        return None
    return {
        "mean_ms": round(float(_get(row, "mean_ms") or 0.0), 2),
        "stddev_ms": round(float(_get(row, "stddev_ms") or 0.0), 2),
        "p95_ms": _opt_float(_get(row, "p95_ms")),
        "sample_count": sample_count,
        "last_observed_ms": _opt_int(_get(row, "last_observed_ms")),
        "last_observed_at": _utc(_get(row, "last_observed_at")),
    }


async def load_baselines(
    db: AsyncSession, project_id: uuid.UUID, fingerprints: Iterable[Optional[str]],
) -> dict[str, dict[str, Any]]:
    """``perf_baselines`` blocks keyed by fingerprint, for ONE project (``uq_perf_baseline_fingerprint``)."""
    wanted = sorted({fp for fp in fingerprints if fp})
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(
                PerfBaseline.test_fingerprint,
                PerfBaseline.mean_ms,
                PerfBaseline.stddev_ms,
                PerfBaseline.p95_ms,
                PerfBaseline.sample_count,
                PerfBaseline.last_observed_ms,
                PerfBaseline.last_observed_at,
            ).where(
                PerfBaseline.project_id == project_id,
                PerfBaseline.test_fingerprint.in_(wanted),
            )
        )
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        block = _baseline_block(row)
        if block is not None:
            out[str(row.test_fingerprint)] = block
    return out


# ── 1. Run timing ────────────────────────────────────────────────────────────

# One pass over the run's rows (``test_run_id`` prefix of every test_cases index).
_RUN_AGGREGATE_SQL = text(
    "SELECT COUNT(*) AS test_count, "
    "COUNT(*) FILTER (WHERE tc.started_at IS NOT NULL AND tc.finished_at IS NOT NULL) AS timed_count, "
    "COUNT(tc.duration_ms) AS duration_count, "
    "SUM(tc.duration_ms) AS summed_ms, "
    "COUNT(*) FILTER (WHERE tc.setup_ms IS NOT NULL OR tc.call_ms IS NOT NULL "
    "OR tc.teardown_ms IS NOT NULL) AS phased_count, "
    "SUM(tc.setup_ms) AS setup_ms, SUM(tc.call_ms) AS call_ms, SUM(tc.teardown_ms) AS teardown_ms, "
    "MIN(tc.started_at) FILTER (WHERE tc.finished_at IS NOT NULL) AS first_started_at, "
    "MAX(tc.finished_at) FILTER (WHERE tc.started_at IS NOT NULL) AS last_finished_at, "
    "COUNT(tc.worker_id) FILTER (WHERE tc.started_at IS NOT NULL AND tc.finished_at IS NOT NULL) AS worker_rows "
    "FROM test_cases tc WHERE tc.test_run_id = :run_id"
)

# The slowest N of the run, straight off ``ix_test_cases_run_duration (test_run_id, duration_ms DESC NULLS LAST)``.
# The ``tc.id`` tie-break only orders rows of equal duration (an incremental sort on top of the index scan).
_SLOWEST_SQL = text(
    "SELECT tc.id AS test_id, tc.test_name, tc.test_fingerprint, "
    f"{_EFFECTIVE_SUITE} AS suite_name, "
    "tc.status, tc.duration_ms, tc.started_at, tc.finished_at, tc.setup_ms, tc.call_ms, tc.teardown_ms, "
    "tc.worker_id, tc.timing_source "
    "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
    "WHERE tc.test_run_id = :run_id AND tc.duration_ms IS NOT NULL "
    "ORDER BY tc.duration_ms DESC NULLS LAST, tc.id "
    "LIMIT :limit"
)

# Worker lanes over the TIMED tests; ``lane_total`` is the number of lanes before the cap.
_LANES_SQL = text(
    "SELECT tc.worker_id, COUNT(*) AS test_count, SUM(tc.duration_ms) AS busy_ms, "
    "MIN(tc.started_at) AS started_at, MAX(tc.finished_at) AS finished_at, "
    "COUNT(*) OVER () AS lane_total "
    "FROM test_cases tc "
    "WHERE tc.test_run_id = :run_id AND tc.started_at IS NOT NULL AND tc.finished_at IS NOT NULL "
    "GROUP BY tc.worker_id "
    "ORDER BY COUNT(*) DESC, tc.worker_id NULLS LAST "
    "LIMIT :limit"
)

# The timeline: the LONGEST timed tests when more qualify than the cap (the same index order).
_TIMELINE_SQL = text(
    "SELECT tc.id AS test_id, tc.test_name, "
    f"{_EFFECTIVE_SUITE} AS suite_name, "
    "tc.status, tc.worker_id, tc.started_at, tc.finished_at, tc.duration_ms "
    "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
    "WHERE tc.test_run_id = :run_id AND tc.started_at IS NOT NULL AND tc.finished_at IS NOT NULL "
    "ORDER BY tc.duration_ms DESC NULLS LAST, tc.id "
    "LIMIT :limit"
)


def run_figures(run: Any, agg: Any) -> dict[str, Any]:
    """The run's headline figures. ``summed_ms`` is ``test_runs.duration_ms`` (the summed meaning EXEC-02 keeps),
    else the rows' own sum; the parallelism is only ever summed over a KNOWN wall clock."""
    wall = _opt_int(_get(run, "wall_clock_ms"))
    summed = _opt_int(_get(run, "duration_ms"))
    if summed is None:
        summed = _opt_int(_get(agg, "summed_ms"))
    return {
        "wall_clock_ms": wall,
        "summed_ms": summed,
        "parallelism": parallelism_of(summed, wall),
        "timing_source": _get(run, "timing_source"),
        "exec_started_at": _utc(_get(run, "exec_started_at")),
        "exec_finished_at": _utc(_get(run, "exec_finished_at")),
        "test_count": int(_get(agg, "test_count") or 0),
        "timed_count": int(_get(agg, "timed_count") or 0),
        "duration_count": int(_get(agg, "duration_count") or 0),
    }


def phase_block(agg: Any) -> dict[str, Any]:
    phased = int(_get(agg, "phased_count") or 0)
    test_count = int(_get(agg, "test_count") or 0)
    if phased <= 0:
        return {
            "measured": False,
            "reason": REASON_NO_RESULTS if test_count == 0 else REASON_NO_PHASES,
            "tests_with_phases": 0,
            "setup_ms": None, "call_ms": None, "teardown_ms": None,
            "setup_share": None, "teardown_share": None,
        }
    setup = _opt_int(_get(agg, "setup_ms"))
    call = _opt_int(_get(agg, "call_ms"))
    teardown = _opt_int(_get(agg, "teardown_ms"))
    return {
        "measured": True,
        "reason": None,
        "tests_with_phases": phased,
        "setup_ms": setup,
        "call_ms": call,
        "teardown_ms": teardown,
        "setup_share": _share(setup, setup, call, teardown),
        "teardown_share": _share(teardown, setup, call, teardown),
    }


def _suite_row(row: Any) -> dict[str, Any]:
    return {
        "suite_name": _get(row, "suite_name") or _get(row, "suite_key") or "",
        "suite_key": _get(row, "suite_key") or "",
        "source": _get(row, "source"),
        "started_at": _utc(_get(row, "started_at")),
        "finished_at": _utc(_get(row, "finished_at")),
        "wall_ms": _opt_int(_get(row, "wall_ms")),
        "summed_ms": _opt_int(_get(row, "summed_ms")),
        "setup_ms": _opt_int(_get(row, "setup_ms")),
        "teardown_ms": _opt_int(_get(row, "teardown_ms")),
        "test_count": int(_get(row, "test_count") or 0),
        "timed_count": int(_get(row, "timed_count") or 0),
        "passed": int(_get(row, "passed") or 0),
        "failed": int(_get(row, "failed") or 0),
        "broken": int(_get(row, "broken") or 0),
        "skipped": int(_get(row, "skipped") or 0),
        "unknown": int(_get(row, "unknown") or 0),
        "parallelism": _opt_float(_get(row, "parallelism")),
    }


def suites_block(rows: Sequence[Any]) -> dict[str, Any]:
    items = [_suite_row(row) for row in rows[:MAX_RUN_SUITES]]
    if not rows:
        return {"measured": False, "reason": REASON_NO_SUITE_ROWS, "total": 0, "truncated": False, "items": []}
    return {
        "measured": True,
        "reason": None,
        "total": len(rows),
        "truncated": len(rows) > MAX_RUN_SUITES,
        "items": items,
    }


def _test_item(row: Any, baselines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fingerprint = _get(row, "test_fingerprint")
    baseline = baselines.get(str(fingerprint)) if fingerprint else None
    duration = _opt_int(_get(row, "duration_ms"))
    ratio = None
    if baseline is not None and duration is not None and baseline["mean_ms"] > 0:
        ratio = round(duration / baseline["mean_ms"], 2)
    return {
        "test_id": _get(row, "test_id"),
        "test_name": _get(row, "test_name") or "",
        "test_fingerprint": fingerprint,
        "suite_name": _get(row, "suite_name"),
        "status": str(_get(row, "status") or TestStatus.UNKNOWN.value),
        "duration_ms": duration,
        "started_at": _utc(_get(row, "started_at")),
        "finished_at": _utc(_get(row, "finished_at")),
        "setup_ms": _opt_int(_get(row, "setup_ms")),
        "call_ms": _opt_int(_get(row, "call_ms")),
        "teardown_ms": _opt_int(_get(row, "teardown_ms")),
        "worker_id": _get(row, "worker_id"),
        "timing_source": _get(row, "timing_source"),
        "baseline": baseline,
        "baseline_ratio": ratio,
    }


def slowest_block(
    rows: Sequence[Any], baselines: dict[str, dict[str, Any]], *, limit: int, agg: Any,
) -> dict[str, Any]:
    total = int(_get(agg, "duration_count") or 0)
    if total == 0:
        test_count = int(_get(agg, "test_count") or 0)
        return {
            "limit": limit, "total": 0, "measured": False,
            "reason": REASON_NO_RESULTS if test_count == 0 else REASON_NO_DURATIONS, "items": [],
        }
    return {
        "limit": limit,
        "total": total,
        "measured": True,
        "reason": None,
        "items": [_test_item(row, baselines) for row in rows[:limit]],
    }


def workers_block(rows: Sequence[Any], agg: Any) -> dict[str, Any]:
    timed = int(_get(agg, "timed_count") or 0)
    test_count = int(_get(agg, "test_count") or 0)
    if timed == 0:
        return {
            "measured": False, "reason": REASON_NO_RESULTS if test_count == 0 else REASON_NO_TIMESTAMPS,
            "total": 0, "truncated": False, "lanes": [],
        }
    lanes: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        total = max(total, int(_get(row, "lane_total") or 0))
        started = _utc(_get(row, "started_at"))
        finished = _utc(_get(row, "finished_at"))
        worker = _get(row, "worker_id")
        lanes.append({
            "worker_id": None if worker is None else str(worker),
            "test_count": int(_get(row, "test_count") or 0),
            "busy_ms": _opt_int(_get(row, "busy_ms")),
            "started_at": started,
            "finished_at": finished,
            "span_ms": _span_ms(started, finished),
        })
    lanes = lanes[:MAX_LANES]
    lanes.sort(key=lambda lane: _worker_sort_key(lane["worker_id"]))
    named = int(_get(agg, "worker_rows") or 0) > 0
    total = max(total, len(lanes))
    return {
        "measured": named,
        "reason": None if named else REASON_NO_WORKERS,
        "total": total,
        "truncated": total > len(lanes),
        "lanes": lanes,
    }


def timeline_block(rows: Sequence[Any], agg: Any, *, limit: int) -> dict[str, Any]:
    timed = int(_get(agg, "timed_count") or 0)
    test_count = int(_get(agg, "test_count") or 0)
    if timed == 0:
        return {
            "measured": False, "reason": REASON_NO_RESULTS if test_count == 0 else REASON_NO_TIMESTAMPS,
            "limit": limit, "total": 0, "truncated": False, "origin": None, "end": None, "items": [],
        }
    origin = _utc(_get(agg, "first_started_at"))
    end = _utc(_get(agg, "last_finished_at"))
    items = []
    for row in rows[:limit]:
        started = _utc(_get(row, "started_at"))
        finished = _utc(_get(row, "finished_at"))
        if started is None or finished is None:  # the WHERE excludes these; a fake row may not
            continue
        base = origin or started
        worker = _get(row, "worker_id")
        items.append({
            "test_id": _get(row, "test_id"),
            "test_name": _get(row, "test_name") or "",
            "suite_name": _get(row, "suite_name"),
            "status": str(_get(row, "status") or TestStatus.UNKNOWN.value),
            "worker_id": None if worker is None else str(worker),
            "started_at": started,
            "finished_at": finished,
            "offset_ms": max(0, int(round((started - base).total_seconds() * 1000))),
            "duration_ms": _opt_int(_get(row, "duration_ms")),
        })
    items.sort(key=lambda item: (item["started_at"], str(item["test_id"])))
    return {
        "measured": True,
        "reason": None,
        "limit": limit,
        "total": timed,
        "truncated": timed > len(items),
        "origin": origin,
        "end": end,
        "items": items,
    }


async def run_timing(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    slowest: int = DEFAULT_SLOWEST,
    timeline_limit: int = DEFAULT_TIMELINE_LIMIT,
) -> Optional[dict[str, Any]]:
    """The run timing breakdown, or None for an unknown run (the route answers 404).

    The caller has already authorised ``run_id`` (``require_run_access``); every statement below is keyed by that
    run, and the only cross-run read -- the baselines -- is pinned to the run's project.
    """
    slowest = max(1, min(int(slowest), MAX_SLOWEST))
    timeline_limit = max(1, min(int(timeline_limit), MAX_TIMELINE_LIMIT))
    run = (
        await db.execute(
            select(
                TestRun.id, TestRun.project_id, TestRun.status, TestRun.build_number, TestRun.branch,
                TestRun.created_at, TestRun.duration_ms, TestRun.wall_clock_ms, TestRun.timing_source,
                TestRun.exec_started_at, TestRun.exec_finished_at,
            ).where(TestRun.id == run_id)
        )
    ).one_or_none()
    if run is None:
        return None

    params = {"run_id": run_id}
    agg = (await db.execute(_RUN_AGGREGATE_SQL, params)).one()

    suite_rows = (
        await db.execute(
            select(SuiteExecution)
            .where(SuiteExecution.test_run_id == run_id)
            .order_by(
                SuiteExecution.wall_ms.desc().nulls_last(),
                SuiteExecution.summed_ms.desc().nulls_last(),
                SuiteExecution.suite_key,
            )
            .limit(MAX_RUN_SUITES + 1)
        )
    ).scalars().all()

    slow_rows: list[Any] = []
    baselines: dict[str, dict[str, Any]] = {}
    if int(agg.duration_count or 0) > 0:
        slow_rows = list((await db.execute(_SLOWEST_SQL, {**params, "limit": slowest})).all())
        baselines = await load_baselines(db, run.project_id, (r.test_fingerprint for r in slow_rows))

    lane_rows: list[Any] = []
    timeline_rows: list[Any] = []
    if int(agg.timed_count or 0) > 0:
        lane_rows = list((await db.execute(_LANES_SQL, {**params, "limit": MAX_LANES})).all())
        timeline_rows = list((await db.execute(_TIMELINE_SQL, {**params, "limit": timeline_limit})).all())

    payload: dict[str, Any] = {
        "run_id": run.id,
        "project_id": run.project_id,
        "status": str(run.status),
        "build_number": run.build_number,
        "branch": run.branch,
        "created_at": _utc(run.created_at),
        "run": run_figures(run, agg),
        "phases": phase_block(agg),
        "suites": suites_block(suite_rows),
        "slowest": slowest_block(slow_rows, baselines, limit=slowest, agg=agg),
        "workers": workers_block(lane_rows, agg),
        "timeline": timeline_block(timeline_rows, agg, limit=timeline_limit),
        "duration_findings": None,
    }

    # Last, and on its own: EXEC-05's read can compute the checks on the fly for a run that was not evaluated on
    # ingest. A failure there must not cost the page every other figure.
    try:
        findings = await regressions.get_duration_regressions(db, run_id)
    except Exception as exc:  # noqa: BLE001 -- logged; the block is then null ("not available")
        logger.warning("run_timing_duration_findings_failed", run_id=str(run_id), error=str(exc))
        findings = None
    if findings is not None:
        payload["duration_findings"] = {"run_id": run_id, **findings}
    return payload


# ── 2. Suite history ─────────────────────────────────────────────────────────


def _suite_history_where(
    params: dict, scope: AnalyticsScope, suite_key: str, branch: Optional[str],
) -> str:
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    params["se_project_id"] = scope.project_id
    params["suite_key"] = suite_key
    return f"""
        FROM suite_executions se
        JOIN test_runs tr ON tr.id = se.test_run_id
        WHERE se.project_id = :se_project_id
          AND se.suite_key = :suite_key
          AND se.run_created_at >= :period_start
          {tenant}
          {release_filter_sql(params, scope.release_arg)}
          {_branch_sql(params, branch, "se.branch")}
    """


def suite_history_point(row: Any) -> dict[str, Any]:
    return {
        "run_id": _get(row, "run_id"),
        "build_number": _get(row, "build_number"),
        "run_created_at": _utc(_get(row, "run_created_at")),
        "branch": _get(row, "branch"),
        "run_status": None if _get(row, "run_status") is None else str(_get(row, "run_status")),
        "source": _get(row, "source"),
        "wall_ms": _opt_int(_get(row, "wall_ms")),
        "summed_ms": _opt_int(_get(row, "summed_ms")),
        "parallelism": _opt_float(_get(row, "parallelism")),
        "started_at": _utc(_get(row, "started_at")),
        "finished_at": _utc(_get(row, "finished_at")),
        "test_count": int(_get(row, "test_count") or 0),
        "timed_count": int(_get(row, "timed_count") or 0),
        "passed": int(_get(row, "passed") or 0),
        "failed": int(_get(row, "failed") or 0),
        "broken": int(_get(row, "broken") or 0),
        "skipped": int(_get(row, "skipped") or 0),
        "unknown": int(_get(row, "unknown") or 0),
    }


def _empty_suite_history(
    scope: AnalyticsScope, *, branch: Optional[str], limit: int, reason: str, suite_key: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "project_id": None if scope.denied else scope.project_id,
        "suite_name": None if scope.denied else (scope.suite_names[0] if scope.suite_names else None),
        "suite_key": None if scope.denied else suite_key,
        "branch": branch,
        "days": scope.window_days,
        "limit": limit,
        "total": 0,
        "truncated": False,
        "measured": False,
        "reason": reason,
        "points": [],
        "stats": {
            "wall": percentiles(0, None, None, reason=reason),
            "summed": percentiles(0, None, None, reason=reason),
            "parallelism_p50": None,
        },
    }


async def suite_history(
    db: AsyncSession,
    scope: AnalyticsScope,
    *,
    branch: Any = None,
    limit: Any = SUITE_HISTORY_DEFAULT_LIMIT,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """One suite's executions over the window, oldest first, plus window percentiles over EVERY execution in it."""
    branch = parse_branch(branch)
    limit = _int_param(limit, "limit", "limit_range", 1, SUITE_HISTORY_MAX_LIMIT)
    suite_key = single_suite_key(scope)
    if scope.denied:
        return _empty_suite_history(scope, branch=branch, limit=limit, reason=REASON_DENIED)
    if scope.project_id is None:  # unreachable: the route's policy requires a project
        return _empty_suite_history(scope, branch=branch, limit=limit, reason=REASON_DENIED)

    params: dict[str, Any] = {"period_start": window_start(scope.window_days, now=now), "limit": limit}
    where = _suite_history_where(params, scope, suite_key, branch)
    rows = (
        await db.execute(
            scoped_text(
                f"""
                SELECT se.test_run_id AS run_id, tr.build_number, se.run_created_at, se.branch,
                       tr.status AS run_status, se.source, se.wall_ms, se.summed_ms, se.parallelism,
                       se.started_at, se.finished_at, se.test_count, se.timed_count,
                       se.passed, se.failed, se.broken, se.skipped, se.unknown, se.suite_name
                {where}
                ORDER BY se.run_created_at DESC, se.test_run_id DESC
                LIMIT :limit
                """,
                params,
            ),
            params,
        )
    ).all()
    stats_params: dict[str, Any] = {"period_start": params["period_start"]}
    stats_where = _suite_history_where(stats_params, scope, suite_key, branch)
    stats = (
        await db.execute(
            scoped_text(
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(se.wall_ms) AS wall_n,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY se.wall_ms) AS wall_p50,
                       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY se.wall_ms) AS wall_p95,
                       COUNT(se.summed_ms) AS summed_n,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY se.summed_ms) AS summed_p50,
                       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY se.summed_ms) AS summed_p95,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY se.parallelism) AS parallelism_p50
                {stats_where}
                """,
                stats_params,
            ),
            stats_params,
        )
    ).one()
    return assemble_suite_history(scope, suite_key, rows, stats, branch=branch, limit=limit)


def assemble_suite_history(
    scope: AnalyticsScope, suite_key: str, rows: Sequence[Any], stats: Any, *, branch: Optional[str], limit: int,
) -> dict[str, Any]:
    total = int(_get(stats, "total") or 0)
    if total == 0 or not rows:
        out = _empty_suite_history(
            scope, branch=branch, limit=limit, reason=REASON_SUITE_NOT_IN_WINDOW, suite_key=suite_key,
        )
        return out
    newest_first = [suite_history_point(row) for row in rows]
    label = _get(rows[0], "suite_name") or (scope.suite_names[0] if scope.suite_names else suite_key)
    wall_reason = "no execution of this suite in the window reported a wall clock"
    return {
        "project_id": scope.project_id,
        "suite_name": label,
        "suite_key": suite_key,
        "branch": branch,
        "days": scope.window_days,
        "limit": limit,
        "total": total,
        "truncated": total > len(newest_first),
        "measured": True,
        "reason": None,
        "points": list(reversed(newest_first)),
        "stats": {
            "wall": percentiles(
                _get(stats, "wall_n"), _get(stats, "wall_p50"), _get(stats, "wall_p95"), reason=wall_reason,
            ),
            "summed": percentiles(
                _get(stats, "summed_n"), _get(stats, "summed_p50"), _get(stats, "summed_p95"),
                reason="no execution of this suite in the window reported a duration",
            ),
            "parallelism_p50": _opt_float(_get(stats, "parallelism_p50")),
        },
    }


# ── 3. Project execution metrics ─────────────────────────────────────────────


def parse_metrics_request(top: Any, suite_limit: Any, branch: Any) -> tuple[int, int, Optional[str]]:
    """Validate before any data query; nothing sent is echoed."""
    return (
        _int_param(top, "top", "top_range", 1, METRICS_MAX_TOP),
        _int_param(suite_limit, "suite_limit", "suite_limit_range", 1, METRICS_MAX_SUITE_LIMIT),
        parse_branch(branch),
    )


def _day_axis(start: datetime, now: datetime) -> list[str]:
    first = start.astimezone(timezone.utc).date()
    last = now.astimezone(timezone.utc).date()
    days = []
    cursor = first
    while cursor <= last:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def per_day_block(rows: Sequence[Any], *, grain: str, start: datetime, now: datetime) -> tuple[dict, dict]:
    """``(per_day, totals)``: every UTC day of the window (a day with no runs has null figures), and the window's
    totals. Parallelism is over the runs carrying BOTH figures only."""
    by_day = {str(_get(row, "day")): row for row in rows}
    points = []
    runs = wall_runs = summed_runs = 0
    wall_total: Optional[int] = None
    summed_total: Optional[int] = None
    both_summed = both_wall = 0
    for day in _day_axis(start, now):
        row = by_day.get(day)
        day_runs = int(_get(row, "runs") or 0) if row is not None else 0
        day_wall_runs = int(_get(row, "wall_runs") or 0) if row is not None else 0
        day_summed_runs = int(_get(row, "summed_runs") or 0) if row is not None else 0
        wall = _opt_int(_get(row, "wall_ms")) if row is not None and day_wall_runs else None
        summed = _opt_int(_get(row, "summed_ms")) if row is not None and day_summed_runs else None
        points.append({
            "day": day, "runs": day_runs,
            "wall_clock_ms": wall, "wall_runs": day_wall_runs,
            "summed_ms": summed, "summed_runs": day_summed_runs,
        })
        runs += day_runs
        wall_runs += day_wall_runs
        summed_runs += day_summed_runs
        if wall is not None:
            wall_total = (wall_total or 0) + wall
        if summed is not None:
            summed_total = (summed_total or 0) + summed
        if row is not None:
            both_summed += int(_get(row, "both_summed_ms") or 0)
            both_wall += int(_get(row, "both_wall_ms") or 0)
    totals = {
        "runs": runs,
        "wall_clock_ms": wall_total,
        "wall_runs": wall_runs,
        "summed_ms": summed_total,
        "summed_runs": summed_runs,
        "parallelism": parallelism_of(both_summed, both_wall) if both_wall > 0 else None,
    }
    return {"grain": grain, "points": points}, totals


def _per_day_run_sql(params: dict, scope: AnalyticsScope, branch: Optional[str]) -> str:
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    return f"""
        SELECT TO_CHAR(DATE_TRUNC('day', tr.created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
               COUNT(*) AS runs,
               SUM(tr.wall_clock_ms) AS wall_ms,
               COUNT(tr.wall_clock_ms) AS wall_runs,
               SUM(tr.duration_ms) AS summed_ms,
               COUNT(tr.duration_ms) AS summed_runs,
               SUM(tr.duration_ms) FILTER (WHERE tr.wall_clock_ms IS NOT NULL) AS both_summed_ms,
               SUM(tr.wall_clock_ms) FILTER (WHERE tr.duration_ms IS NOT NULL) AS both_wall_ms
        FROM test_runs tr
        WHERE tr.created_at >= :period_start
          {tenant}
          {release_filter_sql(params, scope.release_arg)}
          {_branch_sql(params, branch, "tr.branch")}
        GROUP BY 1
    """


def _per_day_suite_sql(params: dict, scope: AnalyticsScope, branch: Optional[str], keys: Sequence[str]) -> str:
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    params["se_project_id"] = scope.project_id
    return f"""
        SELECT TO_CHAR(DATE_TRUNC('day', se.run_created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS day,
               COUNT(DISTINCT se.test_run_id) AS runs,
               SUM(se.wall_ms) AS wall_ms,
               COUNT(DISTINCT se.test_run_id) FILTER (WHERE se.wall_ms IS NOT NULL) AS wall_runs,
               SUM(se.summed_ms) AS summed_ms,
               COUNT(DISTINCT se.test_run_id) FILTER (WHERE se.summed_ms IS NOT NULL) AS summed_runs,
               SUM(se.summed_ms) FILTER (WHERE se.wall_ms IS NOT NULL) AS both_summed_ms,
               SUM(se.wall_ms) FILTER (WHERE se.summed_ms IS NOT NULL) AS both_wall_ms
        FROM suite_executions se
        JOIN test_runs tr ON tr.id = se.test_run_id
        WHERE se.project_id = :se_project_id
          AND se.run_created_at >= :period_start
          {tenant}
          {release_filter_sql(params, scope.release_arg)}
          {_branch_sql(params, branch, "se.branch")}
          {_suite_key_sql(params, keys)}
        GROUP BY 1
    """


def _suites_sql(params: dict, scope: AnalyticsScope, branch: Optional[str], keys: Sequence[str]) -> str:
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    params["se_project_id"] = scope.project_id
    cur = "se.run_created_at >= :period_start"
    prev = "se.run_created_at < :period_start"

    def pct(fraction: str, column: str, window: str) -> str:
        return f"PERCENTILE_CONT({fraction}) WITHIN GROUP (ORDER BY {column}) FILTER (WHERE {window})"

    return f"""
        WITH g AS (
            SELECT se.suite_key,
                   MIN(se.suite_name) FILTER (WHERE {cur}) AS suite_name,
                   COUNT(*) FILTER (WHERE {cur}) AS cur_runs,
                   COUNT(se.wall_ms) FILTER (WHERE {cur}) AS cur_wall_n,
                   {pct('0.5', 'se.wall_ms', cur)} AS cur_wall_p50,
                   {pct('0.95', 'se.wall_ms', cur)} AS cur_wall_p95,
                   COUNT(se.summed_ms) FILTER (WHERE {cur}) AS cur_summed_n,
                   {pct('0.5', 'se.summed_ms', cur)} AS cur_summed_p50,
                   {pct('0.95', 'se.summed_ms', cur)} AS cur_summed_p95,
                   COUNT(*) FILTER (WHERE {prev}) AS prev_runs,
                   COUNT(se.wall_ms) FILTER (WHERE {prev}) AS prev_wall_n,
                   {pct('0.5', 'se.wall_ms', prev)} AS prev_wall_p50,
                   {pct('0.95', 'se.wall_ms', prev)} AS prev_wall_p95,
                   COUNT(se.summed_ms) FILTER (WHERE {prev}) AS prev_summed_n,
                   {pct('0.5', 'se.summed_ms', prev)} AS prev_summed_p50,
                   {pct('0.95', 'se.summed_ms', prev)} AS prev_summed_p95
            FROM suite_executions se
            JOIN test_runs tr ON tr.id = se.test_run_id
            WHERE se.project_id = :se_project_id
              AND se.run_created_at >= :previous_start
              {tenant}
              {release_filter_sql(params, scope.release_arg)}
              {_branch_sql(params, branch, "se.branch")}
              {_suite_key_sql(params, keys)}
            GROUP BY se.suite_key
            HAVING COUNT(*) FILTER (WHERE {cur}) > 0
        )
        SELECT g.*, COUNT(*) OVER () AS suite_total
        FROM g
        ORDER BY g.cur_wall_p95 DESC NULLS LAST, g.cur_summed_p95 DESC NULLS LAST, g.suite_key
        LIMIT :suite_limit
    """


def _window(row: Any, prefix: str, *, reason_wall: str, reason_summed: str) -> dict[str, Any]:
    return {
        "runs": int(_get(row, f"{prefix}_runs") or 0),
        "wall": percentiles(
            _get(row, f"{prefix}_wall_n"), _get(row, f"{prefix}_wall_p50"), _get(row, f"{prefix}_wall_p95"),
            reason=reason_wall,
        ),
        "summed": percentiles(
            _get(row, f"{prefix}_summed_n"), _get(row, f"{prefix}_summed_p50"), _get(row, f"{prefix}_summed_p95"),
            reason=reason_summed,
        ),
    }


def suites_table(rows: Sequence[Any], *, limit: int) -> dict[str, Any]:
    items = []
    total = 0
    for row in rows[:limit]:
        total = max(total, int(_get(row, "suite_total") or 0))
        current = _window(
            row, "cur",
            reason_wall="no run of this suite in the window reported a wall clock",
            reason_summed="no run of this suite in the window reported a duration",
        )
        previous = _window(
            row, "prev",
            reason_wall="no run of this suite in the previous window reported a wall clock",
            reason_summed="no run of this suite in the previous window reported a duration",
        )
        items.append({
            "suite_key": _get(row, "suite_key"),
            "suite_name": _get(row, "suite_name") or _get(row, "suite_key"),
            "current": current,
            "previous": previous,
            "wall_p50_change_pct": _change_pct(current["wall"]["p50_ms"], previous["wall"]["p50_ms"]),
            "wall_p95_change_pct": _change_pct(current["wall"]["p95_ms"], previous["wall"]["p95_ms"]),
            "summed_p50_change_pct": _change_pct(current["summed"]["p50_ms"], previous["summed"]["p50_ms"]),
            "summed_p95_change_pct": _change_pct(current["summed"]["p95_ms"], previous["summed"]["p95_ms"]),
        })
    total = max(total, len(items))
    return {"limit": limit, "total": total, "truncated": total > len(items), "items": items}


def _regression_pairs_sql(params: dict, scope: AnalyticsScope, branch: Optional[str], keys: Sequence[str]) -> str:
    """Each suite x branch that ran in the window (newest first, capped), then its own newest
    ``REGRESSION_HISTORY_DEPTH`` executions on that branch: one index range per pair
    (``ix_suite_executions_project_suite_run`` read backwards), never the suite's whole history. The scope's release
    filter selects WHICH suites are judged; each is judged against its own branch history."""
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    params["se_project_id"] = scope.project_id
    params["pair_cap"] = REGRESSION_PAIR_CAP
    params["history_depth"] = REGRESSION_HISTORY_DEPTH
    return f"""
        WITH pairs AS (
            SELECT se.suite_key, se.branch, MAX(se.run_created_at) AS last_at
            FROM suite_executions se
            JOIN test_runs tr ON tr.id = se.test_run_id
            WHERE se.project_id = :se_project_id
              AND se.run_created_at >= :period_start
              {tenant}
              {release_filter_sql(params, scope.release_arg)}
              {_branch_sql(params, branch, "se.branch")}
              {_suite_key_sql(params, keys)}
            GROUP BY se.suite_key, se.branch
            ORDER BY MAX(se.run_created_at) DESC, se.suite_key
            LIMIT :pair_cap
        )
        SELECT p.suite_key, p.branch, h.suite_name, h.wall_ms, h.summed_ms, h.test_run_id, h.run_created_at
        FROM pairs p
        CROSS JOIN LATERAL (
            SELECT s.suite_name, s.wall_ms, s.summed_ms, s.test_run_id, s.run_created_at
            FROM suite_executions s
            WHERE s.project_id = :se_project_id
              AND s.suite_key = p.suite_key
              AND s.branch IS NOT DISTINCT FROM p.branch
            ORDER BY s.run_created_at DESC
            LIMIT :history_depth
        ) h
        ORDER BY p.last_at DESC, p.suite_key, p.branch NULLS LAST, h.run_created_at DESC, h.test_run_id DESC
    """


def judge_suite_regressions(rows: Sequence[Any], *, top: int) -> dict[str, Any]:
    """EXEC-05's suite rule (``duration_regression_service.judge_suite_history``) over each suite x branch history,
    newest first: median of the last 5 runs >= 1.5x the median of the 20 before them."""
    histories: dict[tuple[str, Optional[str]], list[Any]] = {}
    for row in rows:
        histories.setdefault((_get(row, "suite_key"), _get(row, "branch")), []).append(row)
    needed = regressions.SUITE_RECENT_RUNS + regressions.SUITE_MIN_BASELINE_RUNS
    found = []
    evaluated = 0
    deepest = 0
    for (key, branch), history in histories.items():
        # Newest first (the statement's order; re-sorted so a caller's row order cannot matter).
        history = sorted(
            history,
            key=lambda r: (_utc(_get(r, "run_created_at")) or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        verdict = regressions.judge_suite_history(
            [{"wall_ms": _get(r, "wall_ms"), "summed_ms": _get(r, "summed_ms")} for r in history]
        )
        deepest = max(deepest, verdict.history)
        if not verdict.measured:
            continue
        evaluated += 1
        if not verdict.regression or verdict.ratio is None:
            continue
        latest = history[0]
        found.append({
            "suite_key": key,
            "suite_name": _get(latest, "suite_name"),
            "branch": branch,
            "metric": verdict.metric or "summed",
            "ratio": verdict.ratio,
            "recent_median_ms": verdict.recent_median_ms,
            "baseline_median_ms": verdict.baseline_median_ms,
            "recent_runs": regressions.SUITE_RECENT_RUNS,
            "baseline_runs": verdict.baseline_runs,
            "observed_ms": verdict.observed_ms,
            "was_slow_before": verdict.was_regression_before,
            "latest_run_id": _get(latest, "test_run_id"),
            "latest_run_at": _utc(_get(latest, "run_created_at")),
        })
    found.sort(key=lambda item: (-item["ratio"], item["suite_key"], item["branch"] or ""))
    if not histories:
        reason: Optional[str] = REASON_NO_SUITES_IN_WINDOW
    elif not evaluated:
        reason = f"not enough history ({min(deepest, needed)} of {needed})"
    else:
        reason = None
    return {
        "measured": evaluated > 0,
        "reason": reason,
        "evaluated": evaluated,
        "considered": len(histories),
        "total": len(found),
        "items": found[:top],
    }


_CANDIDATES_SQL = text(
    "SELECT pb.test_fingerprint, pb.test_name, pb.suite_name, pb.mean_ms, pb.p95_ms, pb.sample_count "
    "FROM perf_baselines pb "
    "WHERE pb.project_id = :project_id AND pb.sample_count >= :min_samples AND pb.mean_ms > 0 "
    "AND pb.last_observed_ms IS NOT NULL AND pb.last_observed_at >= :period_start "
    "ORDER BY pb.last_observed_ms / pb.mean_ms DESC, pb.test_fingerprint "
    "LIMIT :candidates"
)


def _window_p50_sql(params: dict, scope: AnalyticsScope, branch: Optional[str]) -> str:
    """The window p50 of the candidate tests, over the population their baselines are fed from (PASSED and FAILED
    executions with a positive duration). Project-scoped: a fingerprint is not salted."""
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    return f"""
        SELECT tc.test_fingerprint,
               COUNT(*) AS n,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tc.duration_ms) AS p50
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tc.test_fingerprint IN :fingerprints
          AND tr.created_at >= :period_start
          AND tc.duration_ms > 0
          AND tc.status IN ('{_PASSED}', '{_FAILED}')
          {tenant}
          {release_filter_sql(params, scope.release_arg)}
          {suite_filter_sql(params, scope.suite_arg)}
          {_branch_sql(params, branch, "tr.branch")}
        GROUP BY tc.test_fingerprint
    """


def judge_test_regressions(
    candidates: Sequence[Any], windows: Sequence[Any], *, top: int, flag_on: bool,
) -> dict[str, Any]:
    by_fp = {str(_get(row, "test_fingerprint")): row for row in windows}
    found = []
    considered = 0
    for candidate in candidates:
        fp = str(_get(candidate, "test_fingerprint"))
        window = by_fp.get(fp)
        mean = float(_get(candidate, "mean_ms") or 0.0)
        p50 = _get(window, "p50") if window is not None else None
        if window is None or p50 is None or mean <= 0:
            continue
        considered += 1
        ratio = float(p50) / mean
        if ratio < TEST_RATIO:
            continue
        found.append({
            "test_fingerprint": fp,
            "test_name": _get(candidate, "test_name"),
            "suite_name": _get(candidate, "suite_name"),
            "baseline_mean_ms": round(mean, 2),
            "baseline_p95_ms": _opt_float(_get(candidate, "p95_ms")),
            "sample_count": int(_get(candidate, "sample_count") or 0),
            "window_p50_ms": round(float(p50), 2),
            "window_n": int(_get(window, "n") or 0),
            "ratio": round(ratio, 2),
        })
    found.sort(key=lambda item: (-item["ratio"], item["test_fingerprint"]))
    reason: Optional[str] = None
    if not candidates:
        reason = (
            f"no test in this project has a duration baseline of at least {TEST_MIN_SAMPLES} samples "
            "observed in the window"
        )
        if not flag_on:
            reason += " (baselines are only built while the perf_regression_detection flag is on)"
    elif not considered:
        reason = "no baselined test ran in this scope in the window"
    return {
        "measured": considered > 0,
        "reason": reason,
        "considered": considered,
        "total": len(found),
        "items": found[:top],
    }


def _slowest_tests_sql(params: dict, scope: AnalyticsScope, branch: Optional[str]) -> str:
    """Per test over the window: p95 of its durations, the slowest first. Ranked by the percentile itself -- the
    chart-data ``duration_p95`` x ``test`` + ``top_n`` read ranks a percentile by its SAMPLE size (the most executed
    tests), which is not "the slowest"."""
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    return f"""
        SELECT tc.test_fingerprint,
               MIN(tc.test_name) AS test_name,
               MIN({_EFFECTIVE_SUITE}) AS suite_name,
               COUNT(*) AS n,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tc.duration_ms) AS p50,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY tc.duration_ms) AS p95,
               MAX(tc.duration_ms) AS max_ms
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tr.created_at >= :period_start
          AND tc.duration_ms IS NOT NULL
          {tenant}
          {release_filter_sql(params, scope.release_arg)}
          {suite_filter_sql(params, scope.suite_arg)}
          {_branch_sql(params, branch, "tr.branch")}
        GROUP BY tc.test_fingerprint
        ORDER BY p95 DESC NULLS LAST, tc.test_fingerprint
        LIMIT :top
    """


def slowest_tests_block(rows: Sequence[Any], *, limit: int) -> dict[str, Any]:
    items = [
        {
            "test_fingerprint": str(_get(row, "test_fingerprint")),
            "test_name": _get(row, "test_name"),
            "suite_name": _get(row, "suite_name"),
            "n": int(_get(row, "n") or 0),
            "p50_ms": _opt_float(_get(row, "p50")),
            "p95_ms": _opt_float(_get(row, "p95")),
            "max_ms": _opt_int(_get(row, "max_ms")),
        }
        for row in rows[:limit]
        if _get(row, "test_fingerprint")
    ]
    return {
        "limit": limit,
        "measured": bool(items),
        "reason": None if items else REASON_NO_TEST_DURATIONS,
        "items": items,
    }


def _histogram_bucket_columns() -> str:
    """One ``COUNT(*) FILTER`` per bucket, generated from the hard-coded edges (never from input). The first bucket
    has no lower bound and the last no upper one, so the buckets always add up to ``n``."""
    edges = HISTOGRAM_EDGES_MS
    columns = []
    for index, lower in enumerate(edges):
        upper = edges[index + 1] if index + 1 < len(edges) else None
        conditions = []
        if index > 0:
            conditions.append(f"tc.duration_ms >= {int(lower)}")
        if upper is not None:
            conditions.append(f"tc.duration_ms < {int(upper)}")
        columns.append(f"COUNT(*) FILTER (WHERE {' AND '.join(conditions)}) AS b{index}")
    return ",\n               ".join(columns)


def _histogram_sql(params: dict, scope: AnalyticsScope, branch: Optional[str]) -> str:
    tenant = tenant_filter_sql(params, project_id=scope.project, allowed_project_ids=scope.allowed_project_ids)
    return f"""
        SELECT COUNT(*) AS n,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY tc.duration_ms) AS p50,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY tc.duration_ms) AS p95,
               {_histogram_bucket_columns()}
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        WHERE tr.created_at >= :period_start
          AND tc.duration_ms IS NOT NULL
          {tenant}
          {release_filter_sql(params, scope.release_arg)}
          {suite_filter_sql(params, scope.suite_arg)}
          {_branch_sql(params, branch, "tr.branch")}
    """


def histogram_block(row: Any) -> dict[str, Any]:
    edges = HISTOGRAM_EDGES_MS
    n = int(_get(row, "n") or 0) if row is not None else 0
    buckets = [
        {
            "lower_ms": int(lower),
            "upper_ms": int(edges[index + 1]) if index + 1 < len(edges) else None,
            "count": int(_get(row, f"b{index}") or 0) if row is not None else 0,
        }
        for index, lower in enumerate(edges)
    ]
    measured = n > 0
    return {
        "measured": measured,
        "reason": None if measured else REASON_NO_TEST_DURATIONS,
        "n": n,
        "buckets": buckets,
        "p50_ms": _opt_float(_get(row, "p50")) if measured else None,
        "p95_ms": _opt_float(_get(row, "p95")) if measured else None,
    }


def regression_rule() -> dict[str, Any]:
    return {
        "recent_runs": regressions.SUITE_RECENT_RUNS,
        "baseline_runs": regressions.SUITE_BASELINE_RUNS,
        "min_baseline_runs": regressions.SUITE_MIN_BASELINE_RUNS,
        "ratio": regressions.SUITE_RATIO,
        "same_branch": True,
        "test_min_samples": TEST_MIN_SAMPLES,
        "test_ratio": TEST_RATIO,
    }


def empty_metrics(
    scope: AnalyticsScope, *, branch: Optional[str], top: int, suite_limit: int, reason: str,
    start: datetime, now: datetime, previous_start: datetime,
) -> dict[str, Any]:
    """The shape with nothing in it: the denied scope, or a window with no run."""
    per_day, totals = per_day_block(
        [], grain="suite" if suite_keys(scope.suite_names) and not scope.denied else "run", start=start, now=now,
    )
    return {
        "project_id": None if scope.denied else scope.project_id,
        "days": scope.window_days,
        "branch": branch,
        "window_start": start,
        "window_end": now,
        "previous_window_start": previous_start,
        "measured": False,
        "reason": reason,
        "totals": totals,
        "per_day": per_day,
        "suites": {"limit": suite_limit, "total": 0, "truncated": False, "items": []},
        "regressions": {
            "rule": regression_rule(),
            "suites": {"measured": False, "reason": reason, "evaluated": 0, "considered": 0, "total": 0, "items": []},
            "tests": {"measured": False, "reason": reason, "considered": 0, "total": 0, "items": []},
        },
        "slowest_tests": {"limit": top, "measured": False, "reason": reason, "items": []},
        "duration_histogram": {**histogram_block(None), "reason": reason},
    }


async def project_execution_metrics(
    db: AsyncSession,
    scope: AnalyticsScope,
    *,
    branch: Any = None,
    top: Any = METRICS_DEFAULT_TOP,
    suite_limit: Any = METRICS_DEFAULT_SUITE_LIMIT,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The project's execution metrics over ``scope``'s window (see the module docstring)."""
    top, suite_limit, branch = parse_metrics_request(top, suite_limit, branch)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    days = scope.window_days
    start = window_start(days, now=now)
    previous_start = start - timedelta(days=days)
    if scope.denied or scope.project_id is None:
        return empty_metrics(
            scope, branch=branch, top=top, suite_limit=suite_limit, reason=REASON_DENIED,
            start=start, now=now, previous_start=previous_start,
        )
    keys = suite_keys(scope.suite_names)
    grain = "suite" if keys else "run"

    # Per day + totals.
    day_params: dict[str, Any] = {"period_start": start}
    day_sql = (
        _per_day_suite_sql(day_params, scope, branch, keys) if keys else _per_day_run_sql(day_params, scope, branch)
    )
    day_rows = (await db.execute(scoped_text(day_sql, day_params), day_params)).all()
    per_day, totals = per_day_block(day_rows, grain=grain, start=start, now=now)
    if totals["runs"] == 0:
        return empty_metrics(
            scope, branch=branch, top=top, suite_limit=suite_limit, reason=REASON_NO_RUNS,
            start=start, now=now, previous_start=previous_start,
        )

    # Per suite, this window against the previous one.
    suite_params: dict[str, Any] = {
        "period_start": start, "previous_start": previous_start, "suite_limit": suite_limit,
    }
    suite_sql = _suites_sql(suite_params, scope, branch, keys)
    suite_rows = (await db.execute(scoped_text(suite_sql, suite_params), suite_params)).all()

    # Suite regressions.
    pair_params: dict[str, Any] = {"period_start": start}
    pair_sql = _regression_pairs_sql(pair_params, scope, branch, keys)
    pair_rows = (await db.execute(scoped_text(pair_sql, pair_params), pair_params)).all()
    suite_regressions = judge_suite_regressions(pair_rows, top=top)

    # Test regressions: baselined candidates, then their window p50.
    candidates = (
        await db.execute(
            _CANDIDATES_SQL,
            {
                "project_id": scope.project_id, "min_samples": TEST_MIN_SAMPLES,
                "period_start": start, "candidates": TEST_REGRESSION_CANDIDATES,
            },
        )
    ).all()
    windows: list[Any] = []
    if candidates:
        window_params: dict[str, Any] = {
            "period_start": start,
            "fingerprints": [str(row.test_fingerprint) for row in candidates],
        }
        window_sql = _window_p50_sql(window_params, scope, branch)
        statement = scoped_text(window_sql, window_params).bindparams(bindparam("fingerprints", expanding=True))
        windows = list((await db.execute(statement, window_params)).all())
    flag_on = await perf._feature_enabled(db, project_id=scope.project_id) if not candidates else True
    test_regressions = judge_test_regressions(candidates, windows, top=top, flag_on=flag_on)

    # Slowest tests and the duration histogram.
    slow_params: dict[str, Any] = {"period_start": start, "top": top}
    slow_sql = _slowest_tests_sql(slow_params, scope, branch)
    slow_rows = (await db.execute(scoped_text(slow_sql, slow_params), slow_params)).all()
    hist_params: dict[str, Any] = {"period_start": start}
    hist_sql = _histogram_sql(hist_params, scope, branch)
    hist_row = (await db.execute(scoped_text(hist_sql, hist_params), hist_params)).one_or_none()

    return {
        "project_id": scope.project_id,
        "days": days,
        "branch": branch,
        "window_start": start,
        "window_end": now,
        "previous_window_start": previous_start,
        "measured": True,
        "reason": None,
        "totals": totals,
        "per_day": per_day,
        "suites": suites_table(suite_rows, limit=suite_limit),
        "regressions": {
            "rule": regression_rule(),
            "suites": suite_regressions,
            "tests": test_regressions,
        },
        "slowest_tests": slowest_tests_block(slow_rows, limit=top),
        "duration_histogram": histogram_block(hist_row),
    }


__all__ = [
    "DEFAULT_SLOWEST",
    "DEFAULT_TIMELINE_LIMIT",
    "HISTOGRAM_EDGES_MS",
    "MAX_SLOWEST",
    "MAX_TIMELINE_LIMIT",
    "METRICS_DEFAULT_DAYS",
    "METRICS_DEFAULT_SUITE_LIMIT",
    "METRICS_DEFAULT_TOP",
    "REASON_NO_TIMESTAMPS",
    "SUITE_HISTORY_DEFAULT_DAYS",
    "SUITE_HISTORY_DEFAULT_LIMIT",
    "judge_suite_regressions",
    "judge_test_regressions",
    "load_baselines",
    "parse_branch",
    "project_execution_metrics",
    "run_timing",
    "single_suite_key",
    "suite_history",
]
