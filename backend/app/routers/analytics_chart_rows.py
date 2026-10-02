"""VIZ-208 -- ``GET /api/v1/analytics/chart-data/rows``: the executions behind a mark.

A thin handler over ``chart_rows_service``, decorated exactly like
``/chart-data`` (VIZ-209 layer: rate limit, statement timeout, epoch-keyed
cache, ETag; VIZ-210 error bodies; VIZ-201 scope). Registered AFTER
``/chart-data`` in ``bootstrap.py`` so the route table reads in drill order.

No ``from __future__ import annotations`` here: the VIZ-209 layer reads the
REAL annotations to tell a repeatable parameter (``list[str]``) from a
single-valued one, and string annotations would make ``group_by`` refuse its
second value.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import chart_data_service
from app.services import chart_rows_service as rows_service
from app.services.analytics_meta import build_meta, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

#: The chart's own policy (``routers/analytics.py`` ``_CHART_DATA``): the same
#: resolver as the chart it drills from, so the population is identical.
_ROWS = ScopePolicy(default_days=30, max_days=365)


def _bucket(dimension: str) -> Any:
    # A Query(...) default for an Optional[str] parameter, not the value itself.
    return Query(
        None,
        description=(
            f"The chart's key for the selected {dimension} bucket. Allowed only "
            f"when {dimension} is in group_by."
        ),
    )


@router.get("/chart-data/rows")
@analytics_error_contract
@analytics_read(namespace="chart_rows")
async def chart_data_rows(
    metric: str = Query(
        "executions",
        description=(
            "The chart's metric; it decides which executions count. One of: "
            + ", ".join(sorted(chart_data_service.METRICS))
        ),
    ),
    group_by: Optional[list[str]] = Query(
        None,
        description=(
            "The chart's dimensions (at most two). One of: "
            + ", ".join(rows_service.ROWS_DIMENSIONS)
        ),
    ),
    top_n: Optional[int] = Query(None, description="The chart's top_n, when it had one."),
    bucket_day: Optional[str] = _bucket("day"),
    bucket_week: Optional[str] = _bucket("week"),
    bucket_project: Optional[str] = _bucket("project"),
    bucket_release: Optional[str] = _bucket("release"),
    bucket_suite: Optional[str] = _bucket("suite"),
    bucket_status: Optional[str] = _bucket("status"),
    bucket_failure_category: Optional[str] = _bucket("failure_category"),
    bucket_branch: Optional[str] = _bucket("branch"),
    bucket_environment: Optional[str] = _bucket("environment"),
    bucket_ingestion_source: Optional[str] = _bucket("ingestion_source"),
    bucket_test: Optional[str] = _bucket("test"),
    bucket_error_signature: Optional[str] = _bucket("error_signature"),
    page: int = Query(1, description="From 1."),
    size: int = Query(
        rows_service.DEFAULT_PAGE_SIZE,
        description=f"1-{rows_service.MAX_PAGE_SIZE}; (page - 1) x size <= {rows_service.MAX_OFFSET}.",
    ),
    scope: AnalyticsScope = Depends(analytics_scope(_ROWS)),
    db: AsyncSession = Depends(get_db),
):
    """One page of the test executions behind a chart mark, newest first.

    ``reconciliation`` names the mark field these rows add up to (``y`` for a
    count, ``n`` for a rate or a duration) and the number to compare with it;
    ``meta.definitions.chart_grain`` says when the chart counted run
    aggregates, which can hold runs with no per-test rows.
    """
    selectors = {
        "day": bucket_day,
        "week": bucket_week,
        "project": bucket_project,
        "release": bucket_release,
        "suite": bucket_suite,
        "status": bucket_status,
        "failure_category": bucket_failure_category,
        "branch": bucket_branch,
        "environment": bucket_environment,
        "ingestion_source": bucket_ingestion_source,
        "test": bucket_test,
        "error_signature": bucket_error_signature,
    }
    req = rows_service.parse_rows_request(
        metric, group_by, top_n, selectors, page, size, scope=scope,
    )
    # ONE clock, the chart's: the window the rows are bounded by is the one
    # the chart that is being drilled was bounded by.
    now = chart_data_service.request_clock()
    payload = await rows_service.build_rows(db, scope, req, now=now)
    definitions = payload.pop("definitions")
    meta = await build_meta(
        db,
        scope,
        pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        window_start=chart_data_service.window_start(scope.window_days, now=now),
        measured=True,
    )
    meta["definitions"] = definitions
    return with_meta(payload, meta)
