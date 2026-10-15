"""EXEC-03 -- the execution-metrics reads.

* ``GET /api/v1/runs/{run_id}/timing`` -- one run's timing breakdown (``require_run_access`` on the path id: an
  unknown or unreadable run is a 404).
* ``GET /api/v1/analytics/execution/suite-history`` -- one suite's wall clock / summed time per run.
* ``GET /api/v1/analytics/execution-metrics`` -- a project's execution metrics over a window.

The two analytics reads take the shared scope (VIZ-201: one project required, every release authorised, a project
the caller cannot read answers the empty shape -- never its data, never a 403 that confirms it exists), the VIZ-209
read layer (cache + ETag, statement timeout, rate limit) and the VIZ-210 error bodies, like ``/test-scatter``.
The contract (field meanings, units, null semantics) is on the response models in ``models/schemas.py``; the logic
is ``services/execution_metrics_service.py``. Every route resolves the caller through ``get_current_active_user``
(``require_run_access`` and ``analytics_scope`` both depend on it).

No ``from __future__ import annotations``: the VIZ-209 layer reads the real annotations to tell repeatable
parameters from single-valued ones.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.core.deps import require_run_access
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import (
    ExecutionMetricsResponse,
    RunTimingResponse,
    SuiteExecutionHistoryResponse,
)
from app.services import chart_data_service
from app.services import execution_metrics_service as svc
from app.services.analytics_meta import build_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope

router = APIRouter(prefix="/api/v1", tags=["Execution Metrics"])

#: One suite of ONE project (suite keys are per project), default 90 days.
_SUITE_HISTORY = ScopePolicy(
    default_days=svc.SUITE_HISTORY_DEFAULT_DAYS, max_days=365, project_required=True, on_denied="empty",
)
#: One project, default 30 days (the previous window is the same length before it).
_EXECUTION_METRICS = ScopePolicy(
    default_days=svc.METRICS_DEFAULT_DAYS, max_days=365, project_required=True, on_denied="empty",
)

_BRANCH_DESCRIPTION = (
    f"Only runs of this branch (exact, case-sensitive, 1-{svc.MAX_BRANCH_LENGTH} characters). "
    "Absent or empty: every branch."
)


@router.get("/runs/{run_id}/timing", response_model=RunTimingResponse, tags=["Test Runs"])
async def get_run_timing(
    run_id: uuid.UUID,
    slowest: int = Query(
        svc.DEFAULT_SLOWEST, ge=1, le=svc.MAX_SLOWEST, description="How many slowest tests to return.",
    ),
    timeline_limit: int = Query(
        svc.DEFAULT_TIMELINE_LIMIT, ge=1, le=svc.MAX_TIMELINE_LIMIT,
        description="Max timeline bars; the LONGEST tests are kept when more qualify (timeline.truncated).",
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_run_access()),
):
    """EXEC-03: how long the run took and where the time went.

    Wall clock vs summed test time and parallelism, phase totals, the run's suite executions, the slowest tests
    with their duration baselines, worker lanes, a timeline of at most ``timeline_limit`` tests for the waterfall,
    and the run's duration regressions (the ``/duration-regressions`` body). A figure the report did not carry is
    ``null`` with ``measured: false`` and a ``reason`` on its block -- e.g. ``timeline.reason`` "report carried no
    timestamps" for a JUnit run -- never 0. Read-only.
    """
    payload = await svc.run_timing(db, run_id, slowest=slowest, timeline_limit=timeline_limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    return RunTimingResponse(**payload)


@router.get(
    "/analytics/execution/suite-history",
    response_model=SuiteExecutionHistoryResponse,
    tags=["Analytics"],
)
@analytics_error_contract
@analytics_read(namespace="execution_suite_history")
async def get_suite_execution_history(
    branch: Optional[str] = Query(None, description=_BRANCH_DESCRIPTION),
    limit: int = Query(
        svc.SUITE_HISTORY_DEFAULT_LIMIT,
        description=f"Max points, 1-{svc.SUITE_HISTORY_MAX_LIMIT} (the newest are kept).",
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_SUITE_HISTORY)),
    db: AsyncSession = Depends(get_db),
):
    """EXEC-03: one suite's wall clock and summed test time per run over the window (oldest first), with window
    p50/p95 over every execution in it. ``suite_name`` is required and single-valued here (matched trimmed and
    case-insensitively); ``release_id`` filters the runs."""
    now = chart_data_service.request_clock()
    payload = await svc.suite_history(db, scope, branch=branch, limit=limit, now=now)
    payload["meta"] = await build_meta(
        db,
        scope,
        window_start=chart_data_service.window_start(scope.window_days, now=now),
        truncated=bool(payload["truncated"]),
        truncated_total=payload["total"] if payload["truncated"] else None,
    )
    return SuiteExecutionHistoryResponse(**payload).model_dump(mode="json")


@router.get(
    "/analytics/execution-metrics",
    response_model=ExecutionMetricsResponse,
    tags=["Analytics"],
)
@analytics_error_contract
@analytics_read(namespace="execution_metrics")
async def get_execution_metrics(
    branch: Optional[str] = Query(None, description=_BRANCH_DESCRIPTION),
    top: int = Query(
        svc.METRICS_DEFAULT_TOP,
        description=f"Size of slowest_tests and of each regressions list, 1-{svc.METRICS_MAX_TOP}.",
    ),
    suite_limit: int = Query(
        svc.METRICS_DEFAULT_SUITE_LIMIT,
        description=f"Size of the per-suite table, 1-{svc.METRICS_MAX_SUITE_LIMIT}.",
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_EXECUTION_METRICS)),
    db: AsyncSession = Depends(get_db),
):
    """EXEC-03: the project's execution time per UTC day (wall clock and summed), p50/p95 per suite against the
    previous window, the biggest suite regressions (median of the last 5 runs >= 1.5x the 20 before, same branch)
    and test regressions (window p50 vs the duration baseline, >= 10 samples), the slowest tests (by p95) and the
    duration histogram. ``suite_name`` (repeatable) narrows every block and switches ``per_day`` to the suite
    grain."""
    now = chart_data_service.request_clock()
    payload = await svc.project_execution_metrics(
        db, scope, branch=branch, top=top, suite_limit=suite_limit, now=now,
    )
    payload["meta"] = await build_meta(
        db,
        scope,
        window_start=chart_data_service.window_start(scope.window_days, now=now),
        truncated=bool(payload["suites"]["truncated"]),
        truncated_total=payload["suites"]["total"] if payload["suites"]["truncated"] else None,
    )
    return ExecutionMetricsResponse(**payload).model_dump(mode="json")
