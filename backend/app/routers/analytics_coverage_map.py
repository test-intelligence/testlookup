"""VIZ-206 -- ``GET /api/v1/analytics/coverage-map``: the coverage treemap, one
level per request. The handler is thin; :mod:`app.services.coverage_map_service`
holds the statement and the assembly (and says what every number means).

Its own module rather than another block in ``routers/analytics.py``, so the
Wave 3 routes do not serialise on one file. Same prefix, same layer as
``/chart-data``: the VIZ-209 decorator (rate limit, 5 s statement timeout,
project-epoch cache key, ETag, 304), the VIZ-210 error contract and the VIZ-201
scope resolver. Registered in ``app.bootstrap`` next to ``analytics.router``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import coverage_map_service
from app.services.analytics_meta import build_meta, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.chart_data_service import request_clock, window_start
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

#: Canonical tests are per project, so "All Projects" has no map: a project is
#: required (422 ``missing_parameter`` without one: the scope's own check,
#: as on every ``project_required`` policy; the heatmap's per-kind refusal is
#: the one that says ``project_required``). The API takes up to 365
#: days for direct callers; the UI asks for at most 90.
COVERAGE_MAP_POLICY = ScopePolicy(default_days=30, max_days=365, project_required=True)


@router.get("/coverage-map")
@analytics_error_contract
@analytics_read(namespace="coverage_map")
async def coverage_map(
    depth: int = Query(
        1, description="1: the suites. 2: the classes (or files) of one suite. 3: the tests of one class.",
    ),
    suite: Optional[str] = Query(
        None,
        description=(
            "depth 2 and 3: the suite KEY, as the level-1 node id carries it after "
            "its 's:' prefix. Echoed, never parsed."
        ),
    ),
    class_key: Optional[str] = Query(
        None,
        description=(
            "depth 3: the class KEY, as the level-2 node id carries it after "
            f"'c:<suite key>{coverage_map_service.KEY_SEPARATOR}' "
            f"('{coverage_map_service.NO_CLASS_KEY}' for tests with no class)."
        ),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(COVERAGE_MAP_POLICY)),
    db: AsyncSession = Depends(get_db),
):
    """One level of the coverage map as contract C3 ``tree``: the parent as the
    single root, its children (at most 499, the rest in one "Other (n)" node),
    each with the ``stats`` block. Test-execution coverage, not code coverage.
    """
    level = coverage_map_service.parse_level(depth, suite, class_key)
    # ONE clock for the window, every staleness and the envelope's as_of.
    now = request_clock()
    payload = await coverage_map_service.build_coverage_map(db, scope, level, now=now)
    definitions = payload.pop("definitions")
    envelope = {key: payload.pop(key) for key in coverage_map_service.ENVELOPE_KEYS}
    meta = await build_meta(
        db,
        scope,
        pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        window_start=window_start(scope.window_days, now=now),
        truncated=bool(envelope["truncated"]),
        truncated_total=envelope["truncated_total"],
    )
    meta["definitions"] = definitions
    return with_meta(payload, meta)
