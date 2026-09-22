"""VIZ-302 (E3) -- the report metrics strip's figures: ``report_metrics`` on
``GET /api/v1/metrics/summary`` (contract C6, ``contracts/viz/README.md``).

For the current window and the one before it: runs, total tests, the four
statuses plus tests with no verdict, the pass rate, total and average run
duration -- over the SAME scope and basis as the endpoint's existing fields
(``metrics_service._period_stats``):

* the same run filter: project pin, live projects only, release (one, several
  or the ``unattributed`` sentinel, on the denormalised column), and a run
  "touching" a requested suite. ``tests/test_report_metrics.py`` pins that the
  predicates are the ones ``_period_stats`` applies;
* the same execution basis: run aggregates, or under a suite filter the rows
  in the suite by EFFECTIVE suite plus the run totals of runs whose rows have
  not landed yet (the mid-ingest live-stream fallback).

One statement covers both periods (``FILTER`` per period), two under a suite
filter (rows, then run-level figures), plus -- only when both periods have
runs -- one bounded ``LIMIT 1`` probe for history in the
:data:`HISTORY_LOOKBACK_DAYS` before the previous window
(:func:`history_before_statement`).

The block is OPT-IN (``include=report_metrics`` on the route): a request that
does not ask for it runs none of these statements and keeps its old cache key.

**Not measured is ``null`` with a reason, never 0.** No runs: every metric of
the period. Nothing evaluated: the pass rate. No run recorded a duration: the
durations. The existing fields keep their historical zeros untouched.

**Comparable** says whether a delta between the two periods means anything,
and when it does not, why (``reason_code``): ``not_measured`` (no current runs),
``no_data`` (no previous runs), ``partial_window`` (the scope has no run in the
:data:`HISTORY_LOOKBACK_DAYS` before the previous window, so its history is
taken to start inside it and cover part of it), ``different_basis``
(under a suite filter, one period counts whole-run totals for row-less runs
and the other does not).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, TestCase, TestRun
from app.models.viz_contracts import REPORT_METRIC_KEYS
from app.services.analytics_scope import (
    ReleaseArg,
    SuiteArg,
    effective_suite_clause,
    run_touches_suite_clause,
    suite_keys,
)

#: C6 ``schema_version``. Also a segment of the dashboard cache key
#: (``metrics_service.get_dashboard_summary``): bump it whenever the block
#: changes shape, and an entry written in the old shape is never read again.
REPORT_METRICS_SCHEMA_VERSION = 1

PASS_RATE_BASIS = "executions"

NO_RUNS = "No runs match this scope in the window."
NO_VERDICT = "No test in this scope produced a verdict: every one was skipped or has no status."
NO_DURATION = "No run in this scope recorded a duration."

_STATUSES = ("PASSED", "FAILED", "BROKEN", "SKIPPED")

#: How far before the previous window ``partial_window`` looks for the scope's
#: earlier history. The metrics routes' longest window (``METRICS_SCOPE``
#: ``max_days``), so the probe never reads more runs than one maximal report
#: window. A scope with no run in these days before the previous window is
#: treated as starting inside it; the reason says so in exactly those words.
HISTORY_LOOKBACK_DAYS = 90


def _window(start: datetime, end: datetime, days: int) -> dict:
    return {"from": start.date().isoformat(), "to": end.date().isoformat(), "days": days}


def period_figures(counts: dict, window: dict, *, empty_reason: str = NO_RUNS) -> dict:
    """One C6 period from raw sums. ``counts``: runs, total, passed, failed,
    broken, skipped, unknown, duration (``None`` when no run recorded one) and
    duration_runs."""
    runs = int(counts.get("runs") or 0)
    if runs == 0:
        period: dict[str, Any] = {key: None for key in REPORT_METRIC_KEYS}
        period["reasons"] = {key: empty_reason for key in REPORT_METRIC_KEYS}
        period["window"] = window
        return period
    passed, failed, broken = (int(counts[k] or 0) for k in ("passed", "failed", "broken"))
    evaluated = passed + failed + broken
    duration_runs = int(counts.get("duration_runs") or 0)
    duration = counts.get("duration")
    reasons: dict[str, str] = {}
    pass_rate: Optional[float] = None
    if evaluated:
        pass_rate = round(passed / evaluated * 100.0, 1)
    else:
        reasons["pass_rate"] = NO_VERDICT
    total_duration: Optional[int] = None
    avg_duration: Optional[int] = None
    if duration_runs and duration is not None:
        total_duration = int(duration)
        # ``int(AVG(duration_ms))``, as the existing ``avg_duration_ms`` reads it.
        avg_duration = total_duration // duration_runs
    else:
        reasons["total_duration_ms"] = reasons["avg_duration_ms"] = NO_DURATION
    return {
        "runs": runs,
        "total_tests": int(counts.get("total") or 0),
        "passed": passed,
        "failed": failed,
        "broken": broken,
        "skipped": int(counts.get("skipped") or 0),
        "unknown": int(counts.get("unknown") or 0),
        "pass_rate": pass_rate,
        "total_duration_ms": total_duration,
        "avg_duration_ms": avg_duration,
        "duration_runs": duration_runs,
        "reasons": reasons,
        "window": window,
    }


def comparability(
    *,
    current_runs: int,
    previous_runs: int,
    history_before: bool,
    first_previous: Optional[datetime],
    suite_scoped: bool,
    pending_current: int,
    pending_previous: int,
    previous_window: dict,
) -> tuple[bool, Optional[str], Optional[str]]:
    """``(comparable, reason_code, reason)``: the first reason that applies."""
    if not current_runs:
        return False, "not_measured", (
            "No runs match this scope in the current window, so there is nothing to compare."
        )
    if not previous_runs:
        return False, "no_data", (
            f"No runs match this scope in the previous period "
            f"({previous_window['from']} to {previous_window['to']})."
        )
    if not history_before:
        first = first_previous.date().isoformat() if first_previous is not None else "unknown"
        return False, "partial_window", (
            f"The previous period may be only partly covered: this scope has no run in the "
            f"{HISTORY_LOOKBACK_DAYS} days before it starts on {previous_window['from']}, and its "
            f"first run in it is on {first}."
        )
    if suite_scoped and (pending_current > 0) != (pending_previous > 0):
        return False, "different_basis", (
            "Some runs in one period have no per-test rows yet, so their whole-run totals are "
            "counted where the other period counts rows in the suite: the two periods are not "
            "counted on the same basis."
        )
    return True, None, None


def scope_conditions(project_id: Any, suite_filter: tuple[str, ...], release_id: ReleaseArg) -> list:
    """The run filter of ``metrics_service._period_stats`` without its window,
    in its order (and pinned equal to it by the unit tests)."""
    conditions: list = []
    if release_id and not isinstance(release_id, (str, uuid.UUID)):
        from app.core.release_filter import release_predicate

        conditions.extend(release_predicate(list(release_id)))
    elif release_id:
        from app.core.release_filter import is_unattributed

        if is_unattributed(str(release_id)):
            conditions.append(TestRun.primary_release_id.is_(None))
        else:
            conditions.append(TestRun.primary_release_id == release_id)
    conditions.append(
        TestRun.project_id.in_(select(Project.id).where(Project.is_active.is_(True)))
    )
    if project_id:
        conditions.append(TestRun.project_id == project_id)
    if suite_filter:
        touches = run_touches_suite_clause(suite_filter)
        assert touches is not None
        conditions.append(touches)
    return conditions


def history_before_statement(conditions: list, previous_start: datetime) -> Any:
    """``partial_window``'s probe: the scope's newest run in the
    :data:`HISTORY_LOOKBACK_DAYS` before the previous window opens, if any.

    Bounded on both sides of ``created_at`` and read newest-first, so the
    planner walks one project's (or one release's) runs in a closed range
    (``ix_test_runs_project_release_created`` / ``ix_test_runs_created_at``)
    and stops at the first match. The unbounded ``created_at < start LIMIT 1``
    it replaces had no order and no floor: under a suite filter with no
    earlier matching run it read the project's whole history, one suite
    ``EXISTS`` per run, on every cache miss.
    """
    lookback_start = previous_start - timedelta(days=HISTORY_LOOKBACK_DAYS)
    return (
        select(TestRun.id)
        .where(
            *conditions,
            TestRun.created_at >= lookback_start,
            TestRun.created_at < previous_start,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )


def _sum(expr: Any, when: Any) -> Any:
    return func.coalesce(func.sum(expr).filter(when), 0)


def _no_verdict(total: Any, *parts: Any) -> Any:
    """Per run: tests without one of the four verdicts, never below 0."""
    rest: Any = func.coalesce(total, 0)
    for part in parts:
        rest = rest - func.coalesce(part, 0)
    return func.greatest(rest, 0)


def _run_columns(tag: str, when: Any) -> list:
    """Run-aggregate sums for one period, filtered by ``when``."""
    return [
        func.count(TestRun.id).filter(when).label(f"runs_{tag}"),
        _sum(TestRun.total_tests, when).label(f"total_{tag}"),
        _sum(TestRun.passed_tests, when).label(f"passed_{tag}"),
        _sum(TestRun.failed_tests, when).label(f"failed_{tag}"),
        _sum(TestRun.broken_tests, when).label(f"broken_{tag}"),
        _sum(TestRun.skipped_tests, when).label(f"skipped_{tag}"),
        _sum(
            _no_verdict(TestRun.total_tests, TestRun.passed_tests, TestRun.failed_tests,
                        TestRun.broken_tests, TestRun.skipped_tests),
            when,
        ).label(f"unknown_{tag}"),
    ]


def _duration_columns(tag: str, when: Any) -> list:
    return [
        func.count(TestRun.id).filter(when).label(f"all_runs_{tag}"),
        # NULL when no run of the period recorded a duration: not measured.
        func.sum(TestRun.duration_ms).filter(when).label(f"duration_{tag}"),
        func.count(TestRun.duration_ms).filter(when).label(f"duration_runs_{tag}"),
    ]


def _row_values(row: Any, tag: str, prefix: str = "") -> dict:
    return {
        key: getattr(row, f"{prefix}{key}_{tag}")
        for key in ("runs", "total", "passed", "failed", "broken", "skipped", "unknown")
    }


async def build_report_metrics(
    db: AsyncSession,
    project_id: Optional[str],
    days: int,
    suite_name: SuiteArg,
    release_id: ReleaseArg,
    *,
    now: datetime,
) -> dict:
    """The C6 ``report_metrics`` block for one scope, ending at ``now``."""
    suite_filter = suite_keys(suite_name)
    period_start = now - timedelta(days=days)
    previous_start = now - timedelta(days=days * 2)
    conditions = scope_conditions(project_id, suite_filter, release_id)
    in_window = and_(TestRun.created_at >= previous_start, TestRun.created_at < now)
    is_current = TestRun.created_at >= period_start
    is_previous = TestRun.created_at < period_start
    first_previous = func.min(TestRun.created_at).filter(is_previous).label("first_p")

    sums: dict[str, dict] = {}
    pending = {"c": 0, "p": 0}
    if not suite_filter:
        row = (await db.execute(
            select(
                *_run_columns("c", is_current),
                *_run_columns("p", is_previous),
                *_duration_columns("c", is_current),
                *_duration_columns("p", is_previous),
                first_previous,
            ).where(*conditions, in_window)
        )).one()
        for tag in ("c", "p"):
            sums[tag] = _row_values(row, tag)
    else:
        in_suite = effective_suite_clause(suite_filter)
        assert in_suite is not None
        status = TestCase.status
        row_cur = TestRun.created_at >= period_start
        row_prev = TestRun.created_at < period_start

        def _rows(tag: str, when: Any) -> list:
            return [
                func.count(TestCase.id).filter(when).label(f"total_{tag}"),
                *(
                    func.count(TestCase.id).filter(and_(when, status == name)).label(f"{name.lower()}_{tag}")
                    for name in _STATUSES
                ),
                func.count(TestCase.id).filter(and_(when, status.notin_(_STATUSES))).label(f"unknown_{tag}"),
            ]

        # (a) Rows in the suite by effective suite -- ``_period_stats`` (a).
        rows = (await db.execute(
            select(*_rows("c", row_cur), *_rows("p", row_prev))
            .select_from(TestCase)
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(*conditions, in_window, in_suite)
        )).one()
        # (b) + runs: run-level figures; row-less runs contribute their run
        # totals, exactly ``_period_stats`` (b).
        no_rows = ~(
            select(TestCase.id).where(TestCase.test_run_id == TestRun.id).correlate(TestRun).exists()
        )
        row = (await db.execute(
            select(
                *_run_columns("c", and_(is_current, no_rows)),
                *_run_columns("p", and_(is_previous, no_rows)),
                *_duration_columns("c", is_current),
                *_duration_columns("p", is_previous),
                first_previous,
            ).where(*conditions, in_window)
        )).one()
        for tag in ("c", "p"):
            run_level = _row_values(row, tag)
            pending[tag] = int(run_level["runs"] or 0)
            sums[tag] = {
                "total": int(getattr(rows, f"total_{tag}") or 0) + int(run_level["total"] or 0),
                **{
                    key: int(getattr(rows, f"{key}_{tag}") or 0) + int(run_level[key] or 0)
                    for key in ("passed", "failed", "broken", "skipped", "unknown")
                },
            }
    for tag in ("c", "p"):
        sums[tag]["runs"] = int(getattr(row, f"all_runs_{tag}") or 0)
        sums[tag]["duration"] = getattr(row, f"duration_{tag}")
        sums[tag]["duration_runs"] = int(getattr(row, f"duration_runs_{tag}") or 0)

    # Read only where it decides something: ``comparability`` returns on a
    # missing period before it looks at history, so the probe runs only when
    # both periods have runs.
    history_before = True
    if sums["c"]["runs"] and sums["p"]["runs"]:
        history_before = (await db.execute(
            history_before_statement(conditions, previous_start)
        )).first() is not None

    current_window = _window(period_start, now, days)
    previous_window = _window(previous_start, period_start, days)
    comparable, reason_code, reason = comparability(
        current_runs=sums["c"]["runs"],
        previous_runs=sums["p"]["runs"],
        history_before=history_before,
        first_previous=row.first_p,
        suite_scoped=bool(suite_filter),
        pending_current=pending["c"],
        pending_previous=pending["p"],
        previous_window=previous_window,
    )
    previous = period_figures(sums["p"], previous_window)
    previous.update({"comparable": comparable, "reason_code": reason_code, "reason": reason})
    return {
        "schema_version": REPORT_METRICS_SCHEMA_VERSION,
        "pass_rate_basis": PASS_RATE_BASIS,
        "current": period_figures(sums["c"], current_window),
        "previous": previous,
    }
