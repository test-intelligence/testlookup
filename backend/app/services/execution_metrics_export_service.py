"""EXEC-06: the execution metrics as CSV files.

* :func:`load_run_timing_rows` -- EVERY test of one run with its timing (the
  JSON ``/runs/{run_id}/timing`` returns only the slowest 100 and a bounded
  timeline; the export is the whole run). Slowest first, straight off
  ``ix_test_cases_run_duration (test_run_id, duration_ms DESC NULLS LAST)``.
* :func:`execution_suite_rows` -- the per-suite table of
  ``/analytics/execution-metrics`` (``execution_metrics_service.
  project_execution_metrics``), one row per suite, current window against the
  previous one.

Null is "not reported": a figure the report did not carry is an EMPTY cell,
never ``0`` (and a measured ``0`` is ``0``). Every cell goes through
``csv_safe`` (formula injection neutralised, see there).

Pure reads: nothing here writes, flushes or commits.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics_scope import effective_suite_sql
from app.services.csv_safe import filename_segment

#: One row per test of the run. Units: ms; instants ISO 8601 UTC.
RUN_TIMING_COLUMNS: tuple[str, ...] = (
    "test_name",
    "suite_name",
    "status",
    "duration_ms",
    "started_at",
    "finished_at",
    "setup_ms",
    "call_ms",
    "teardown_ms",
    "worker_id",
    "timing_source",
)

#: The most rows one run export holds. A run is bounded by the ingest cap
#: (``MAX_RESULTS_PER_INGEST`` per upload) but several uploads can feed one
#: run; past this the file says it was cut (``X-Truncated``) rather than
#: passing a prefix off as the whole run.
MAX_RUN_TIMING_ROWS = 100_000

# The effective suite (a live-stream run's label, else the row's own suite) is
# the rule every suite read uses; spelled by ``analytics_scope``, not here.
_RUN_TIMING_SQL = text(
    "SELECT tc.test_name, "
    f"{effective_suite_sql('tr', 'tc')} AS suite_name, "
    "tc.status, tc.duration_ms, tc.started_at, tc.finished_at, "
    "tc.setup_ms, tc.call_ms, tc.teardown_ms, tc.worker_id, tc.timing_source "
    "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
    "WHERE tc.test_run_id = :run_id "
    "ORDER BY tc.duration_ms DESC NULLS LAST, tc.id "
    "LIMIT :limit"
)


def _status(value: Any) -> Optional[str]:
    raw = getattr(value, "value", value)
    return None if raw is None else str(raw).upper()


def run_timing_record(row: Any) -> tuple[Any, ...]:
    """One CSV record, in :data:`RUN_TIMING_COLUMNS` order."""
    return (
        row.test_name,
        row.suite_name,
        _status(row.status),
        row.duration_ms,
        row.started_at,
        row.finished_at,
        row.setup_ms,
        row.call_ms,
        row.teardown_ms,
        row.worker_id,
        row.timing_source,
    )


async def load_run_timing_rows(
    db: AsyncSession, run_id: uuid.UUID, *, limit: int = MAX_RUN_TIMING_ROWS,
) -> tuple[list[tuple[Any, ...]], bool]:
    """Every test of the run as CSV records, plus whether the cap cut them.

    One bounded statement of narrow columns (no error text, no stack trace).
    Read inside the request, not inside the response stream: on this FastAPI
    the ``get_db`` session is closed before a streamed body is sent, and a
    second session per download is the pool-starvation shape the guards
    already removed elsewhere. The caller has authorised ``run_id``.
    """
    result = await db.execute(_RUN_TIMING_SQL, {"run_id": run_id, "limit": limit + 1})
    rows = list(result.all())
    truncated = len(rows) > limit
    return [run_timing_record(row) for row in rows[:limit]], truncated


def run_timing_filename(build_number: Any, run_id: uuid.UUID) -> str:
    """``run-<build>-timing.csv``; the run id's first block when the build
    number is missing or has nothing filename-safe in it."""
    return f"run-{filename_segment(build_number, fallback=str(run_id)[:8])}-timing.csv"


# ── Project execution metrics, per suite ─────────────────────────────────────

#: One row per suite: the current window and the previous one (same length,
#: just before it). Percentiles in ms; ``*_change_pct`` in percent; ``*_n`` is
#: the number of runs that carried the figure.
EXECUTION_SUITE_COLUMNS: tuple[str, ...] = (
    "suite_name",
    "suite_key",
    "runs",
    "wall_n",
    "wall_p50_ms",
    "wall_p95_ms",
    "summed_n",
    "summed_p50_ms",
    "summed_p95_ms",
    "previous_runs",
    "previous_wall_n",
    "previous_wall_p50_ms",
    "previous_wall_p95_ms",
    "previous_summed_n",
    "previous_summed_p50_ms",
    "previous_summed_p95_ms",
    "wall_p50_change_pct",
    "wall_p95_change_pct",
    "summed_p50_change_pct",
    "summed_p95_change_pct",
)


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _window_cells(window: Any) -> list[Any]:
    wall = _get(window, "wall") or {}
    summed = _get(window, "summed") or {}
    return [
        _get(window, "runs"),
        _get(wall, "n"),
        _get(wall, "p50_ms"),
        _get(wall, "p95_ms"),
        _get(summed, "n"),
        _get(summed, "p50_ms"),
        _get(summed, "p95_ms"),
    ]


def execution_suite_rows(payload: Any) -> list[list[Any]]:
    """The ``suites.items`` of an ``/analytics/execution-metrics`` payload (a
    dict or the response model) as records in :data:`EXECUTION_SUITE_COLUMNS`
    order. The denied / empty shape has no items: the file is the header."""
    suites = _get(payload, "suites") or {}
    items: Sequence[Any] = _get(suites, "items") or []
    out: list[list[Any]] = []
    for item in items:
        out.append([
            _get(item, "suite_name"),
            _get(item, "suite_key"),
            *_window_cells(_get(item, "current") or {}),
            *_window_cells(_get(item, "previous") or {}),
            _get(item, "wall_p50_change_pct"),
            _get(item, "wall_p95_change_pct"),
            _get(item, "summed_p50_change_pct"),
            _get(item, "summed_p95_change_pct"),
        ])
    return out


def execution_metrics_filename(project_id: Optional[uuid.UUID], days: int) -> str:
    """``execution-metrics-<project first block>-<days>d.csv``; the denied
    shape names no project."""
    project = str(project_id)[:8] if project_id is not None else "none"
    return f"execution-metrics-{project}-{int(days)}d.csv"


__all__ = [
    "EXECUTION_SUITE_COLUMNS",
    "MAX_RUN_TIMING_ROWS",
    "RUN_TIMING_COLUMNS",
    "execution_metrics_filename",
    "execution_suite_rows",
    "load_run_timing_rows",
    "run_timing_filename",
    "run_timing_record",
]
