"""Dashboard metrics endpoints."""
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
)
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.analytics_meta import build_meta, utc_day_window_start
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.commit_attribution_service import get_tia_readiness
from app.services.flaky_detection_timing_service import (
    detection_timing as measure_detection_timing,
)
from app.services.flaky_readiness_service import get_flaky_readiness
from app.services.metrics_service import (
    PASS_RATE_BASIS_EXECUTIONS,
    get_dashboard_summary,
    get_trend_data,
)

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


def _project_in_scope(project_id: str, accessible: set) -> bool:
    """True when ``project_id`` is one the caller may access."""
    try:
        return uuid.UUID(str(project_id)) in accessible
    except (ValueError, TypeError):
        return False


#: The dashboard's scope (VIZ-201): ``days`` 1-90, as it always was.
#:
#: ``on_denied="empty"``: a non-admin who names no project, or one they are not
#: a member of, gets the empty payload rather than a 403. The service trusts
#: the id it is given, so this membership check is the tenant boundary
#: (review/metrics-service, 2026-06-01) -- it now lives in the shared resolver.
#:
#: A malformed ``project_id`` -- the frontend-only ``"all"`` sentinel, ``""``,
#: ``"undefined"`` -- is a 422 ``project_id_format`` before any query runs. It
#: used to reach the UUID comparison for an ADMIN, whose accessible set is
#: ``None`` and skips the membership check, and 500 (found 2026-08-07).
METRICS_SCOPE = ScopePolicy(default_days=7, max_days=90, on_denied="empty")

#: ``/summary`` fields that honour ``suite_name`` but NOT ``release_id``
#: while the rest of the body honours both. ``meta.ignored_filters`` is per
#: dimension, so this field-level exception is declared in the route's
#: OpenAPI description and on ``schemas.DashboardSummary``, and pinned by
#: ``tests/regression/test_summary_report_release_scope.py``. Why not scoped:
#: they are the inputs of the readiness verdict's hard caps (open CRITICAL
#: defects, flaky count, new failures in 24h), which the gate policy defines
#: project-wide; scoping them to a release changes what the gate gates on --
#: an owner decision, not a filter fix. (Pre-existing; VIZ-202 review.)
DASHBOARD_RELEASE_UNSCOPED: tuple[str, ...] = (
    "active_defects", "flaky_test_count", "new_failures_24h",
)

#: ``include`` tokens ``/summary`` understands (VIZ-302). Opt-in blocks: a
#: request that names none pays nothing for them -- no query, and the cache
#: key and payload the response has always had.
SUMMARY_INCLUDE_REPORT_METRICS = "report_metrics"
SUMMARY_INCLUDES: frozenset[str] = frozenset({SUMMARY_INCLUDE_REPORT_METRICS})


def summary_includes(values: Optional[list[str]]) -> frozenset[str]:
    """The known ``include`` tokens in ``values`` (repeated and/or
    comma-separated; surrounding whitespace and case ignored). An unknown token
    is ignored, like an unknown query parameter: ``include`` only ever ADDS a
    block, so a token this server does not know cannot change any field."""
    tokens = {
        token.strip().lower()
        for value in (values or ())
        for token in str(value).split(",")
    }
    return frozenset(tokens & SUMMARY_INCLUDES)


@router.get("/summary")
@analytics_error_contract
async def dashboard_summary(
    scope: AnalyticsScope = Depends(analytics_scope(METRICS_SCOPE)),
    db: AsyncSession = Depends(get_db),
    # ``Annotated`` so a direct call (tests, jobs) that omits it gets ``None``,
    # not FastAPI's ``Query`` object.
    include: Annotated[
        Optional[list[str]],
        Query(description=(
            "Opt-in blocks, repeatable or comma-separated. `report_metrics` adds the "
            "report strip's figures (contract C6). Unknown values are ignored."
        )),
    ] = None,
):
    """Return aggregated KPI metrics for the Executive Dashboard.

    ``include=report_metrics`` (VIZ-302, contract C6) adds the report strip's
    figures for this window and the previous one -- counts by status, total and
    average run duration, and ``previous.comparable`` with a reason when a
    delta would mislead. Unmeasured values are ``null`` with a reason, never 0.
    OPT-IN: without it the block is not computed, and the response, its SQL
    and its cache key are exactly what they were before the block existed.

    ``release_id`` and ``suite_name`` repeat: OR within a dimension, AND
    across. Every release id is authorised (403/404) before any KPI runs.

    Field-level exception: ``active_defects``, ``flaky_test_count`` and
    ``new_failures_24h`` honour ``suite_name`` but NOT ``release_id`` -- they
    are project-wide inputs of the readiness verdict's hard caps
    (``DASHBOARD_RELEASE_UNSCOPED``). Every other figure honours both.
    """
    if scope.denied:
        # The historical empty payload, plus the envelope (VIZ-204): it names
        # no project, so it confirms nothing the empty body did not. No
        # ``report_metrics`` either: the strip reads ``meta.measured`` /
        # ``meta.reason`` and shows every tile unmeasured.
        return {"meta": await build_meta(db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS)}

    async def _meta() -> dict:
        return await build_meta(db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS)

    # ``meta`` is built inside the service so it is cached with the payload
    # it describes (and the cache key carries the envelope's schema version).
    return await get_dashboard_summary(
        db, scope.project, scope.window_days,
        suite_name=scope.suite_arg, release_id=scope.release_arg, meta_builder=_meta,
        report_metrics=SUMMARY_INCLUDE_REPORT_METRICS in summary_includes(include),
    )


@router.get("/trends")
@analytics_error_contract
async def trend_data(
    scope: AnalyticsScope = Depends(analytics_scope(METRICS_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """Return daily pass/fail/skip breakdown for trend charts."""
    # The chart's buckets start at UTC midnight ``days - 1`` days ago
    # (``get_trend_data``), so the envelope states that same window.
    meta = await build_meta(
        db, scope, pass_rate_basis=PASS_RATE_BASIS_EXECUTIONS,
        window_start=utc_day_window_start(scope.window_days),
    )
    if scope.denied:
        return {"data": [], "period_days": scope.window_days, "meta": meta}
    data = await get_trend_data(
        db, scope.project, scope.window_days,
        suite_name=scope.suite_arg, release_id=scope.release_arg,
    )
    return {"data": data, "period_days": scope.window_days, "meta": meta}


@router.get("/tia-readiness")
async def tia_readiness(
    project_id: str = Query(
        ..., description="Project to measure — readiness is never a fleet average",
    ),
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Is this project's commit-range corpus big enough to train test-impact
    analysis on yet? (Epic 10 go/no-go.)

    Counts the runs whose commit range actually resolved AND carries
    per-commit changed files, the span they cover, and the distinct paths
    seen. Returns ``available: false`` with a concrete
    ``insufficient_data_reason`` until every published threshold is met —
    the same honesty contract as the value-metrics headline gate.

    ``project_id`` is REQUIRED: readiness is a claim about one project's own
    change/failure history, so there is no meaningful all-projects rollup.
    """
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and not _project_in_scope(project_id, accessible):
        # Non-admin asking about a project they can't see — same shape as an
        # empty project rather than a 403 that confirms the project exists.
        return {
            "project_id": project_id,
            "available": False,
            "insufficient_data_reason": "no commit ranges have been resolved for this project yet",
        }
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="project_id must be a UUID")
    return await get_tia_readiness(db, pid, days=days)


@router.get("/flaky-readiness")
async def flaky_readiness(
    project_id: str = Query(
        ..., description="Project to measure — readiness is never a fleet average",
    ),
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Does this project have enough history to score flakiness at all?
    (Roadmap Phase 0 gate on the continuous-score work.)

    The published evidence for probabilistic flakiness scoring comes from
    hyperscale monorepos; academic open-source corpora report per-test flake
    rates about an order of magnitude lower. A moving-window posterior needs
    runs *per fingerprint* to update a prior with — a test seen three times can
    only produce a number that is mostly prior, which is fabricated confidence
    wearing a decimal point.

    So this reports how many fingerprints clear the per-fingerprint run
    threshold, the median runs per test, and a plain ``available`` verdict with
    ``insufficient_data_reason`` when the corpus is too thin — the same honesty
    contract as ``/metrics/tia-readiness``.

    ``project_id`` is REQUIRED: corpus depth is a claim about one project's own
    history, so a fleet average would be meaningless.
    """
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and not _project_in_scope(project_id, accessible):
        # Same shape as an empty project rather than a 403 that would confirm
        # the project exists to someone who cannot see it.
        return {
            "project_id": project_id,
            "available": False,
            "insufficient_data_reason": "no test results are visible for this project",
        }
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="project_id must be a UUID")
    return await get_flaky_readiness(db, pid, window_days=days)


@router.get("/detection-timing")
async def detection_timing(
    project_id: str = Query(
        ..., description="Project to measure — detection latency is never a fleet average",
    ),
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """How long does this project wait to learn a test is flaky? (Roadmap Phase 6.)

    Two-tier detection: a short-cadence screen of new and directly-modified
    fingerprints, plus a nightly pass over the whole corpus for the
    environment- and dependency-induced flakiness a diff cannot reach.

    The cadence is expressed in **time**, not commits. A commit-count cadence
    assumes a commit range on most runs and enough of them to count; the Phase 0
    census measured zero of four genuine projects on the reference deployment
    clearing that, so such a cadence would simply never fire here.

    The number worth reading is ``bottleneck``. On a thin corpus, detection is
    limited by how often tests *run*, not by how often they are screened — a
    score needs observations before it is defensible — and this says which term
    dominates from measured numbers rather than assuming the flattering one.

    Latency is reported only over fingerprints whose first appearance was
    actually observed; the rest are counted and excluded, not backfilled to a
    zero that would read as instant detection.

    ``project_id`` is REQUIRED: detection latency is a claim about one
    project's own history, so a fleet average would be meaningless.
    """
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None and not _project_in_scope(project_id, accessible):
        # Same shape as an unmeasured project rather than a 403 that would
        # confirm the project exists to someone who cannot see it.
        return {
            "project_id": project_id,
            "available": False,
            "insufficient_data_reason": "no test results are visible for this project",
        }
    try:
        pid = uuid.UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="project_id must be a UUID")
    return await measure_detection_timing(db, pid, window_days=days)
