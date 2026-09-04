"""Dashboard metrics endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_accessible_project_ids,
    get_current_active_user,
    resolve_release_query_scope,
)
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.commit_attribution_service import get_tia_readiness
from app.services.flaky_detection_timing_service import (
    detection_timing as measure_detection_timing,
)
from app.services.flaky_readiness_service import get_flaky_readiness
from app.services.metrics_service import get_dashboard_summary, get_trend_data

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])


def _project_in_scope(project_id: str, accessible: set) -> bool:
    """True when ``project_id`` is one the caller may access."""
    try:
        return uuid.UUID(str(project_id)) in accessible
    except (ValueError, TypeError):
        return False


def _require_valid_project_id(project_id: str | None) -> str | None:
    """Reject a malformed ``project_id`` before it reaches a UUID column.

    ``ALL_PROJECTS_ID`` ("all") is a **frontend-only** sentinel; if it ever
    reaches the API it must not be treated as an id. Non-admins were already
    covered by accident — ``_project_in_scope`` returns False for a non-UUID,
    so they got an empty payload. But ``get_accessible_project_ids`` returns
    ``None`` for an ADMIN, which SKIPS that scope check entirely, so the raw
    string reached the query layer as a UUID comparison and produced a **500**.
    The bug was therefore role-dependent and invisible to non-admin testing.

    ``None`` stays valid — it means "all projects" for a caller allowed to see
    them. Mirrors ``/api/v1/runs``, which answers 400 "Invalid project_id".
    """
    if project_id is None:
        return None
    try:
        uuid.UUID(str(project_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid project_id — expected a UUID")
    return project_id


@router.get("/summary")
async def dashboard_summary(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    suite_name: str | None = Query(None, min_length=1),
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return aggregated KPI metrics for the Executive Dashboard."""
    project_id = _require_valid_project_id(project_id)
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        # Non-admin: must request a project they're a member of. Without
        # verifying the *provided* project_id a caller could read any tenant's
        # KPIs via ?project_id=<other-tenant-uuid> (the service trusts it).
        if not project_id or not _project_in_scope(project_id, accessible):
            return {}
    # Verifies the PROVIDED release, not merely the None path. The
    # architectural authorization ratchet checks evidence per-ROUTE and stops at
    # the first scoped parameter it can satisfy, so this route passes the
    # ratchet on ``project_id`` alone with the release entirely unchecked —
    # exactly the blind spot that hid nine IDORs before. Called explicitly
    # rather than as a dependency so the absent-release path issues no extra
    # query and stays byte-identical (NFR1).
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    return await get_dashboard_summary(
        db, project_id, days, suite_name=suite_name, release_id=release_id
    )


@router.get("/trends")
async def trend_data(
    project_id: str | None = None,
    days: int = Query(7, ge=1, le=90),
    suite_name: str | None = Query(None, min_length=1),
    release_id: str | None = Query(None, description="Scope to one release"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return daily pass/fail/skip breakdown for trend charts."""
    project_id = _require_valid_project_id(project_id)
    accessible = await get_accessible_project_ids(db, current_user)
    if accessible is not None:
        # Non-admin: only own-project trends (see dashboard_summary).
        if not project_id or not _project_in_scope(project_id, accessible):
            return {"data": [], "period_days": days}
    release_id = await resolve_release_query_scope(db, release_id, current_user)
    data = await get_trend_data(
        db, project_id, days, suite_name=suite_name, release_id=release_id
    )
    return {"data": data, "period_days": days}


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
