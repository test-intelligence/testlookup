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

from datetime import datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.analytics_read_layer import analytics_read
from app.db.postgres import get_db
from app.services import coverage_map_service, coverage_snapshot_service
from app.services.analytics_meta import build_meta, ignored, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.chart_data_service import request_clock, window_start
from app.services.metrics_service import PASS_RATE_BASIS_EXECUTIONS

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

#: W08 TRND-07: the Coverage History tab. A snapshot is a row per project a
#: day, so a project is required; up to a year of them.
COVERAGE_HISTORY_POLICY = ScopePolicy(
    default_days=90, max_days=coverage_snapshot_service.HISTORY_MAX_DAYS, project_required=True,
)
#: W08 TRND-07: the Fix tagging panel's list -- the Coverage page's window.
UNTAGGED_TESTS_POLICY = ScopePolicy(default_days=30, max_days=90, project_required=True)

#: Why the history takes no release or suite (stated in ``meta.ignored_filters``).
_HISTORY_IGNORED = {
    "release": (
        "A coverage snapshot is project-wide: it counts the catalogue and every test "
        "exercised in 30 days, whatever release the runs were for."
    ),
    "suite": (
        "A coverage snapshot is project-wide; its per-suite rows are not a filter over "
        "the catalogue count."
    ),
}

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
    other_page: Optional[int] = Query(
        None,
        ge=2,
        le=coverage_map_service.OTHER_PAGE_MAX,
        description=(
            "Open the level's 'Other (n)' node: page 2 is the next "
            f"{coverage_map_service.KEPT_WHEN_FOLDED} children of the same parent, "
            "page 3 the ones after, ... Absent is the first page."
        ),
    ),
    scope: AnalyticsScope = Depends(analytics_scope(COVERAGE_MAP_POLICY)),
    db: AsyncSession = Depends(get_db),
):
    """One level of the coverage map as contract C3 ``tree``: the parent as the
    single root, its children (at most 499, the rest in one "Other (n)" node,
    which ``other_page`` opens), each with the ``stats`` block (W08:
    ``needs_review_count`` in one project). Test-execution coverage, not code
    coverage.
    """
    level = coverage_map_service.parse_level(depth, suite, class_key, other_page)
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


@router.get("/coverage/history")
@analytics_error_contract
@analytics_read(namespace="coverage_history")
async def coverage_history(
    scope: AnalyticsScope = Depends(analytics_scope(COVERAGE_HISTORY_POLICY)),
    db: AsyncSession = Depends(get_db),
):
    """W08 TRND-07 -- the project's daily coverage snapshots over the window:
    tests in the catalogue, tests exercised in the 30 days ending each day
    (and their share), suites exercised. One point per stored day, ascending;
    a day with no snapshot is absent (a gap). ``measured: false`` before the
    project's first snapshot. Release and suite are declared ignored: a
    snapshot is project-wide.
    """
    today = coverage_snapshot_service.utc_today()
    body = await coverage_snapshot_service.coverage_history(db, scope, today=today)
    declared = [
        ignored(dimension, _HISTORY_IGNORED[dimension])
        for dimension, sent in (("release", scope.release_ids), ("suite", scope.suite_names))
        if sent
    ]
    first_day = coverage_snapshot_service.history_window_start(scope.window_days, today)
    meta = await build_meta(
        db,
        scope,
        window_start=datetime.combine(first_day, time.min, tzinfo=timezone.utc),
        ignored_filters=declared,
        measured=bool(body["measured"]),
        reason=body["reason"],
    )
    meta["definitions"] = dict(coverage_snapshot_service.HISTORY_DEFINITIONS)
    return with_meta(body, meta)


@router.get("/coverage/untagged-tests")
@analytics_error_contract
@analytics_read(namespace="coverage_untagged")
async def coverage_untagged_tests(
    page: int = Query(1, ge=1, description="The page, from 1."),
    size: int = Query(
        50, ge=1, le=coverage_snapshot_service.UNTAGGED_PAGE_MAX, description="Tests per page (1-200).",
    ),
    scope: AnalyticsScope = Depends(analytics_scope(UNTAGGED_TESTS_POLICY)),
    db: AsyncSession = Depends(get_db),
):
    """W08 TRND-07 -- the tests behind the Coverage page's Untagged executions
    callout: the distinct tests whose executions in the window carry no suite
    label, busiest first, each with its current catalogue suite (the Fix
    tagging panel). ``total`` is the count beyond this page.
    """
    body = await coverage_snapshot_service.untagged_tests(db, scope, page=page, size=size)
    # ``now - days``: the window the list (and the callout) reads.
    meta = await build_meta(db, scope)
    meta["definitions"] = dict(coverage_snapshot_service.UNTAGGED_DEFINITIONS)
    return with_meta(body, meta)
