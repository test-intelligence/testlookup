"""EXEC-06 -- the execution metrics as CSV downloads.

* ``GET /api/v1/runs/{run_id}/timing.csv`` -- every test of one run with its
  timing (the Timing tab's "Export CSV"). Guard: ``require_run_access`` on the
  path id, exactly as ``GET /runs/{run_id}/timing``: an unknown run, or one the
  caller cannot read, is a 404 (never a 403 that confirms it exists).
* ``GET /api/v1/analytics/execution-metrics.csv`` -- the per-suite table of
  ``GET /analytics/execution-metrics`` (Trends > Execution time's
  "Export CSV"). Same scope policy as that route (one project required, every
  release authorised; a project the caller cannot read answers the EMPTY file,
  a header and no rows -- never that project's data), the same VIZ-210 error
  bodies, and the VIZ-209 rate limit and statement timeout (``cache=False``:
  the layer caches JSON bodies, not files).

Its own module, beside ``routers/execution_metrics.py`` (the JSON reads), so
the two are built and reviewed independently; the numbers come from the same
service functions. Every cell is written by ``services/csv_safe`` (formula
injection neutralised); a null figure is an empty cell, never 0.

No ``from __future__ import annotations``: the VIZ-209 layer reads the real
annotations to tell repeatable parameters from single-valued ones.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.core.deps import require_run_access
from app.db.postgres import get_db
from app.models.postgres import TestRun, User
from app.services import chart_data_service
from app.services import execution_metrics_export_service as export
from app.services import execution_metrics_service as svc
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.csv_safe import iter_csv

runs_router = APIRouter(prefix="/api/v1/runs", tags=["Test Runs"])
analytics_router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

#: ``GET /analytics/execution-metrics``'s policy, restated (that router keeps
#: its own private). ``tests/test_execution_metrics_export.py`` pins the two
#: equal, so the file and the JSON can never be scoped differently.
EXECUTION_METRICS_SCOPE = ScopePolicy(
    default_days=svc.METRICS_DEFAULT_DAYS, max_days=365, project_required=True, on_denied="empty",
)

_CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def _csv_response(columns, records, *, filename: str, truncated: bool, total: int) -> StreamingResponse:
    return StreamingResponse(
        iter_csv(columns, records),
        media_type=_CSV_MEDIA_TYPE,
        headers={
            # ``filename`` is built from ``csv_safe.filename_segment``: no
            # quote, CR or LF can reach this header.
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Row-Count": str(total),
            "X-Truncated": "true" if truncated else "false",
            "Cache-Control": "private, no-store",
        },
    )


@runs_router.get(
    "/{run_id}/timing.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "One row per test, slowest first."}},
)
async def export_run_timing_csv(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """EXEC-06: every test of the run with its timing, as CSV, slowest first.

    Columns: test_name, suite_name (the effective suite), status, duration_ms,
    started_at, finished_at (ISO 8601 UTC), setup_ms, call_ms, teardown_ms,
    worker_id, timing_source (``report`` | ``derived`` | ``live``). A figure
    the report did not carry (a JUnit file has no timestamps) is an empty
    cell, never 0. ``X-Row-Count`` is the number of tests in the file and
    ``X-Truncated`` says whether the 100,000-row cap cut it. Read-only.
    """
    run = (
        await db.execute(select(TestRun.id, TestRun.build_number).where(TestRun.id == run_id))
    ).one_or_none()
    if run is None:
        # ``require_run_access`` lets an administrator through without looking
        # the run up, so the existence check is here too.
        raise HTTPException(status_code=404, detail="Test run not found")
    records, truncated = await export.load_run_timing_rows(db, run_id)
    return _csv_response(
        export.RUN_TIMING_COLUMNS,
        records,
        filename=export.run_timing_filename(run.build_number, run_id),
        truncated=truncated,
        total=len(records),
    )


@analytics_router.get(
    "/execution-metrics.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "One row per suite."}},
)
@analytics_error_contract
@analytics_read(namespace="execution_metrics_csv", cache=False)
async def export_execution_metrics_csv(
    branch: Optional[str] = Query(
        None,
        description=(
            f"Only runs of this branch (exact, case-sensitive, 1-{svc.MAX_BRANCH_LENGTH} characters). "
            "Absent or empty: every branch."
        ),
    ),
    suite_limit: int = Query(
        svc.METRICS_MAX_SUITE_LIMIT,
        description=(
            f"Suites in the file, 1-{svc.METRICS_MAX_SUITE_LIMIT} (default: all of them, up to "
            f"{svc.METRICS_MAX_SUITE_LIMIT})."
        ),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(EXECUTION_METRICS_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """EXEC-06: the per-suite table of ``GET /analytics/execution-metrics`` as
    CSV -- wall clock and summed test time p50/p95 (ms) for the window and the
    previous window of the same length, and their change in percent. Same
    ``project_id`` / ``release_id`` / ``suite_name`` / ``days`` / ``branch``
    as that route. A project the caller cannot read gets the header and no
    rows. A suite whose runs carried no wall clock has empty wall cells, never
    0. ``X-Truncated`` says whether ``suite_limit`` cut the table."""
    now = chart_data_service.request_clock()
    payload = await svc.project_execution_metrics(
        db, scope, branch=branch, top=1, suite_limit=suite_limit, now=now,
    )
    suites = payload.get("suites") or {}
    records = export.execution_suite_rows(payload)
    return _csv_response(
        export.EXECUTION_SUITE_COLUMNS,
        records,
        filename=export.execution_metrics_filename(payload.get("project_id"), scope.window_days),
        truncated=bool(suites.get("truncated")),
        total=len(records),
    )
