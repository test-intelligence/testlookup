"""W08 TRND-06 -- ``GET /api/v1/analytics/trend-summary``: the Trends page's
verdict, confidence score and its four dimensions, variance and run gap,
computed by the server (``trend_summary_service``) over the page's own two
reads for the same scope.

Its own module, as ``analytics_coverage_map.py``, so the wave does not
serialise on ``routers/analytics.py``. Same prefix and layer: the VIZ-209
decorator (rate limit, statement timeout, project-epoch cache key, ETag, 304
-- only ``/api/v1/analytics/*`` GETs may wear it), the VIZ-210 error contract
and the VIZ-201 scope resolver. Registered in ``app.bootstrap`` beside
``analytics_coverage_map.router``.

The scope is ``/metrics/trends``' own (``days`` 1-90, ``on_denied="empty"``):
a project the caller cannot read answers the empty ``measured: false`` body,
not a 403, exactly as the trend chart beside it does.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import trend_summary_service
from app.services.analytics_meta import build_meta, utc_day_window_start, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

#: ``/metrics/trends``' rule (``routers/metrics.py`` ``METRICS_SCOPE``), with
#: the Trends page's 14-day default.
TREND_SUMMARY_POLICY = ScopePolicy(default_days=14, max_days=90, on_denied="empty")


@router.get("/trend-summary")
@analytics_error_contract
@analytics_read(namespace="trend_summary")
async def get_trend_summary(
    scope: AnalyticsScope = Depends(analytics_scope(TREND_SUMMARY_POLICY)),
    db: AsyncSession = Depends(get_db),
):
    """The trend verdict (``HEALTHY`` / ``MIXED`` / ``INSUFFICIENT`` /
    ``DECLINING`` / ``PENDING``), the 0-100 confidence score and its four
    weighted dimensions, the variance of the daily pass rate, and the longest
    run of days without executions in the window. ``null`` = not measured."""
    body = await trend_summary_service.trend_summary(db, scope)
    # The trend chart's buckets start at UTC midnight ``days - 1`` days ago.
    meta = await build_meta(
        db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        window_start=utc_day_window_start(scope.window_days),
    )
    return with_meta(body, meta)
