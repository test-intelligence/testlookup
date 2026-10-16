"""Value Metrics endpoints — operational value dashboard for customers."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import analytics_error_contract
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.services.analytics_meta import build_meta, ignored, with_meta
from app.services.analytics_scope import AnalyticsScope, ScopePolicy, analytics_scope
from app.services.value_metrics_service import get_methodology, get_value_metrics

router = APIRouter(prefix="/api/v1/value-metrics", tags=["Value Metrics"])


#: VIZ-201/202: the shared scope. A non-admin who names no project, or one
#: they cannot read, gets the historical empty payload (``on_denied="empty"``).
_VALUE_SCOPE = ScopePolicy(default_days=30, max_days=365, on_denied="empty")

#: Why value metrics do not take release or suite (VIZ-202). Stated in
#: ``meta.ignored_filters`` whenever either is sent -- never dropped silently.
_VALUE_IGNORED = {
    "release": (
        "Value metrics count product activity over time (clusters, duplicate "
        "tickets, promoted defects, coach results, release decisions, the "
        "hours-saved model). Several of those carry no release, so filtering "
        "only the ones that do would mix two scopes in one total."
    ),
    "suite": (
        "Value metrics are project-level counts over time; defects, coach "
        "results and the hours-saved model carry no suite, so a suite filter "
        "cannot be applied to the totals consistently."
    ),
}


@router.get("")
@analytics_error_contract
async def get_metrics(
    months: int = Query(6, ge=1, le=24),
    scope: AnalyticsScope = Depends(analytics_scope(_VALUE_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """Return operational value metrics for a project (or all) over a time
    window, including the US-12.1 engineer-hours-saved model (``months``
    bounds the monthly trend).

    ``release_id`` / ``suite_name`` are accepted, authorised and declared in
    ``meta.ignored_filters`` with the reason: these are time-based counts
    over the project, and most of them have no release or suite to filter.
    """
    declared = [
        ignored(dimension, _VALUE_IGNORED[dimension])
        for dimension, sent in (("release", scope.release_ids), ("suite", scope.suite_names))
        if sent
    ]
    # Every counter here is windowed and project-scoped, not run-derived: an
    # all-zero answer is a measurement, so ``measured`` is not tied to runs.
    meta = await build_meta(
        db, scope, ignored_filters=declared, measured=None if scope.denied else True,
    )
    if scope.denied:
        # Non-admin: the provided project_id is verified by the scope --
        # otherwise a caller could read any tenant's value metrics via
        # ?project_id=<foreign-uuid>. The historical empty payload.
        return {"meta": meta}
    payload = await get_value_metrics(
        db, project_id=scope.project_id, days=scope.window_days, months=months,
    )
    return with_meta(payload, meta)


@router.get("/methodology")
async def get_methodology_page(
    current_user: User = Depends(get_current_active_user),
):
    """US-12.1: the hours-saved model documented — legs, formulas, caveats,
    defaults, research anchors. Static content; login-only, not
    project-scoped."""
    return get_methodology()


#: W08 TRND-08: ownership rules are per project, so a project is required (a
#: missing one is the scope's 422, a foreign one a 403). Up to a year, as the
#: Overview's window.
_TEAM_SCOPE = ScopePolicy(default_days=30, max_days=365, project_required=True)

#: What ``meta.definitions`` says the by-team numbers are.
_TEAM_DEFINITIONS = {
    "team": (
        "A test's team is what the project's ownership rules (priority first) or its "
        "component map resolve for it -- the resolver /analytics/top-failing uses. A "
        "blank team, 'unknown' or no match is Unassigned."
    ),
    "tests": (
        "Distinct tests per effective suite executed in the window (run clock). A test "
        "that ran under two suites is counted under each."
    ),
    "pass_rate": "passed / (passed + failed + broken) x 100; null when nothing was evaluated.",
    "flaky_tests": "Tests with at least one execution in the window flagged is_flaky_run.",
    "defects": "Defects created in the window, by their test's team; mttr_hours over the resolved ones.",
    "hours": (
        "The headline's own legs: clustered failures x triage minutes (hours.triage) and "
        "duplicates absorbed x filing minutes (hours.dedup), attributed through each "
        "cluster's members (a cluster spanning teams is split by member). Runs unblocked "
        "by quarantine are per run, not per test: not_attributable_hours."
    ),
    "headline_hours": (
        "The window's hours-saved total, computed in this request from the same legs "
        "over the same window, so the teams' hours plus not_attributable_hours equal it. "
        "It is not gated on the Overview's minimum history."
    ),
}


@router.get("/by-team")
@analytics_error_contract
async def get_metrics_by_team(
    scope: AnalyticsScope = Depends(analytics_scope(_TEAM_SCOPE)),
    db: AsyncSession = Depends(get_db),
):
    """W08 TRND-08 -- value metrics by owning team, for one project.

    One row per team (``Unassigned`` last): tests, executions, the evaluated
    pass rate, flaky tests, defects and MTTR, and the hours saved attributed
    from the headline's own legs, plus ``not_attributable_hours`` (runs
    unblocked by quarantine) and ``headline_hours``. ``rules_configured:
    false`` / ``measured: false`` when the project has no ownership rules and
    no component map. ``release_id`` / ``suite_name`` are accepted, authorised
    and declared in ``meta.ignored_filters``: these are project-level counts
    over time, as on the Overview.
    """
    from app.services.team_value_metrics_service import get_team_value_metrics

    declared = [
        ignored(dimension, _VALUE_IGNORED[dimension])
        for dimension, sent in (("release", scope.release_ids), ("suite", scope.suite_names))
        if sent
    ]
    payload = await get_team_value_metrics(db, scope)
    meta = await build_meta(
        db,
        scope,
        ignored_filters=declared,
        measured=bool(payload["measured"]),
        reason=payload["reason"],
    )
    meta["definitions"] = dict(_TEAM_DEFINITIONS)
    return with_meta(payload, meta)


@router.get("/export")
async def export_metrics(
    project_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Export value metrics as a downloadable JSON report.

    HON-04: the scope is ``resolve_project_scope``'s, as on ``/by-team``. A
    project the caller cannot read is a 403. It used to be a 200 carrying a
    one-key ``{"report_type": "value_metrics"}`` stub, which the browser saved
    as if it were the report. A non-admin who names no project is refused
    too: the unpinned export is the every-project aggregate, which spans
    tenants, so only an admin may take it.
    """
    from app.core.deps import resolve_project_scope
    pid, allowed = await resolve_project_scope(db, current_user, project_id)
    if allowed is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Select a project to export its value metrics",
        )
    import json
    metrics = await get_value_metrics(db, project_id=pid, days=days)
    content = json.dumps({"report_type": "value_metrics", **metrics}, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=value-metrics-{days}d.json"},
    )
