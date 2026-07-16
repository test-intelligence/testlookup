"""My Failures inbox — the calling user's auto-assigned failures (migration 0080).

When a test run is finalised, every FAILED/BROKEN TestCase gets its
``assigned_to_user_id`` set to the resolved suite owner (see
``failed_test_assignment_service``). This router surfaces those rows as a
single inbox so QA leads can drain their action queue without browsing
runs.

Why a dedicated module: the inbox is read-only, user-scoped (``WHERE
assigned_to_user_id = :me``), and serves both the table page and the
sidebar badge count. Putting it under ``/api/v1/me`` rather than extending
``/api/v1/users/{id}`` keeps the implicit "self" scope explicit in the URL.

Scope semantics:
  * Caller can only ever see rows where they are the assignee — no other
    project-membership check is required because the assignee column is
    set at ingest time and is itself the authorisation predicate.
  * ``project_id`` filter narrows further if set. ``ALL_PROJECTS_ID = "all"``
    is a frontend sentinel; the backend treats it (or omission) as
    "no project filter."
  * ``days`` filter trims by ``TestCase.created_at`` — defaults to 30 so the
    inbox doesn't grow forever before we have a "resolved" concept.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.models.postgres import (
    Project,
    TestCase,
    TestRun,
    TestStatus,
    TriageStatus,
    User,
    UserRole,
)
from app.models.schemas import MyFailureItem, MyFailureListResponse, TriageStatusUpdate
from app.services.failed_test_reassignment_service import (
    ReassignmentError,
    get_reassignment_options,
    reassign_failure,
)
from app.services.failed_test_triage_service import (
    TriageError,
    update_triage_status,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/me", tags=["My Failures"])


# Match the assignment service's actionable set so the inbox can't list a
# status that was never auto-assigned in the first place.
_ACTIONABLE_STATUSES = (TestStatus.FAILED, TestStatus.BROKEN)


def _parse_project_id(raw: Optional[str]) -> Optional[uuid.UUID]:
    """Honour the All-Projects sentinel — ``"all"`` and empty both mean unscoped."""
    if not raw or raw == "all":
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        # Don't 400 — fall back to "no filter" so a stale URL doesn't
        # surface as a broken page. The inbox is implicitly user-scoped
        # so there's no leak risk.
        return None


@router.get("/assigned-failures", response_model=MyFailureListResponse)
async def list_my_assigned_failures(
    project_id: Optional[str] = Query(None, description='Project UUID or "all"'),
    days: int = Query(30, ge=1, le=365, description="Time window (created_at)"),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=100),
    scope: str = Query(
        "mine",
        pattern="^(mine|team)$",
        description=(
            "'mine' = caller's assigned failures only; "
            "'team' = all failures across project (QA_LEAD/ADMIN only)"
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the caller's FAILED/BROKEN TestCase rows, newest first.

    Pagination notes:
      * ``unresolved_total`` is the same-filter total without pagination so
        the sidebar badge stays accurate while the user is on page 2.
      * ``total`` is the paginated total (matches ``items`` count summed
        across pages) — currently equal to ``unresolved_total`` because we
        don't yet have a "resolved" concept; future-proofed by keeping both
        fields distinct now.
    """
    scoped_project_id = _parse_project_id(project_id)
    period_start = datetime.now(timezone.utc) - timedelta(days=days)

    # Team scope is only honoured for QA_LEAD / ADMIN. Anyone else
    # silently falls back to ``mine`` so the URL can't be tampered with
    # to leak cross-user data. The synthetic default-QA-Lead user picks
    # up most auto-assignments, so callers viewing as admin would
    # otherwise see almost nothing — team scope is the natural way to
    # see project-wide unresolved failures without re-assigning rows.
    effective_scope = scope
    if scope == "team" and current_user.role not in (
        UserRole.QA_LEAD.value, UserRole.ADMIN.value,
    ):
        effective_scope = "mine"

    # Phase: triage workflow (migration 0088). Inbox shows only rows
    # the assignee hasn't actioned yet. ``REVIEWED_APPROVED /
    # DEFECT_CREATED / WONT_FIX`` rows are off the inbox by design;
    # they remain accessible from the per-run detail page where the
    # status badge surfaces the resolution.
    base_filters = [
        TestCase.status.in_(_ACTIONABLE_STATUSES),
        TestCase.triage_status == TriageStatus.PENDING_REVIEW.value,
        TestCase.created_at >= period_start,
    ]
    if effective_scope == "mine":
        base_filters.append(TestCase.assigned_to_user_id == current_user.id)
    if scoped_project_id is not None:
        base_filters.append(TestRun.project_id == scoped_project_id)

    # Total count for pagination + badge.
    count_stmt = (
        select(func.count(TestCase.id))
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*base_filters)
    )
    total = int((await db.execute(count_stmt)).scalar() or 0)

    if total == 0:
        return MyFailureListResponse(
            items=[], total=0, page=page, size=size, pages=0, unresolved_total=0,
        )

    # Fetch the page. Pull TestRun + Project columns inline so we don't
    # N+1 to render the table.
    list_stmt = (
        select(
            TestCase.id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.class_name,
            TestCase.status,
            TestCase.severity,
            TestCase.failure_category,
            TestCase.error_message,
            TestCase.duration_ms,
            TestCase.created_at,
            TestCase.test_run_id,
            TestCase.canonical_test_case_id,
            TestCase.triage_status,
            TestCase.triage_notes,
            # US-8.4: assignee + stack trace drive the read-time "via
            # CODEOWNERS: …" reason derivation below.
            TestCase.assigned_to_user_id,
            TestCase.stack_trace,
            TestRun.build_number,
            TestRun.project_id,
            Project.name.label("project_name"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .join(Project, Project.id == TestRun.project_id)
        .where(*base_filters)
        .order_by(desc(TestCase.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(list_stmt)).all()

    # US-8.4: derive a compact "via CODEOWNERS: <pattern>" reason for rows
    # whose located path matches a path rule resolving to the row's assignee.
    # Best-effort + bounded to the page — never raises.
    from app.services.codeowners_service import codeowners_reasons_for_rows

    assignment_reasons = await codeowners_reasons_for_rows(db, list(rows))

    # Per-test failure count inside the same window. Grouped by the natural
    # identity (project + suite + class + test name) so the same test across
    # runs collapses to a single bucket. Single query for the whole page →
    # no N+1.
    count_by_key: dict[tuple, int] = {}
    if rows:
        count_stmt = (
            select(
                TestRun.project_id,
                TestCase.suite_name,
                TestCase.class_name,
                TestCase.test_name,
                func.count(TestCase.id).label("n"),
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(*base_filters)
            .group_by(
                TestRun.project_id,
                TestCase.suite_name,
                TestCase.class_name,
                TestCase.test_name,
            )
        )
        for c in (await db.execute(count_stmt)).all():
            count_by_key[(c.project_id, c.suite_name, c.class_name, c.test_name)] = int(c.n)

    # Per-(project, suite) run sequence so the inbox can show "Run #N"
    # instead of the opaque SDK-supplied build_number. The map is bulk-
    # fetched once for every distinct test_run_id on the page.
    from app.services.runs_service import (
        fetch_run_seq_map,
        first_failed_step_by_canonical,
    )
    distinct_run_ids = list({r.test_run_id for r in rows})
    run_seq_map = await fetch_run_seq_map(db, distinct_run_ids)

    # Granular enrichment (Phase 5): the first FAILED/BROKEN step name per
    # failure, read from the LATEST-RUN-ONLY snapshot anchored to the test's
    # canonical id. Batched once for the whole page → no N+1. Tests without a
    # captured snapshot (or without a failing step) are simply absent → None.
    canonical_ids = [
        r.canonical_test_case_id for r in rows if r.canonical_test_case_id is not None
    ]
    step_by_canonical = await first_failed_step_by_canonical(db, canonical_ids)

    items: list[MyFailureItem] = []
    for r in rows:
        # Truncate error_message to keep payloads bounded — full message is
        # available on the test-case detail page if the user drills in.
        err = (r.error_message or "")
        if len(err) > 280:
            err = err[:277] + "..."
        key = (r.project_id, r.suite_name, r.class_name, r.test_name)
        items.append(MyFailureItem(
            id=r.id,
            test_name=r.test_name,
            suite_name=r.suite_name,
            class_name=r.class_name,
            status=r.status,
            severity=r.severity,
            failure_category=r.failure_category,
            error_message=err or None,
            duration_ms=r.duration_ms,
            created_at=r.created_at,
            test_run_id=r.test_run_id,
            build_number=str(r.build_number) if r.build_number is not None else None,
            project_id=r.project_id,
            project_name=r.project_name,
            navigation_url=f"/runs/{r.test_run_id}/tests/{r.id}",
            triage_status=r.triage_status,
            triage_notes=r.triage_notes,
            run_seq=run_seq_map.get(str(r.test_run_id)),
            failure_count=count_by_key.get(key, 1),
            last_failure_step=step_by_canonical.get(r.canonical_test_case_id),
            assignment_reason=assignment_reasons.get(r.id),
        ))

    pages = math.ceil(total / size) if size > 0 else 0
    return MyFailureListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
        unresolved_total=total,
    )


@router.get("/assigned-failures/count")
async def my_assigned_failures_count(
    project_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    scope: str = Query("mine", pattern="^(mine|team)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Cheap COUNT for the sidebar badge — no row hydration.

    The list endpoint already returns ``unresolved_total`` for pages it
    serves, but the sidebar polls independently of the page state, so a
    dedicated endpoint keeps the badge fresh without fetching 25 rows on
    every poll.
    """
    scoped_project_id = _parse_project_id(project_id)
    period_start = datetime.now(timezone.utc) - timedelta(days=days)

    effective_scope = scope
    if scope == "team" and current_user.role not in (
        UserRole.QA_LEAD.value, UserRole.ADMIN.value,
    ):
        effective_scope = "mine"

    filters = [
        TestCase.status.in_(_ACTIONABLE_STATUSES),
        # Match the inbox list endpoint — badge counts only PENDING_REVIEW.
        TestCase.triage_status == TriageStatus.PENDING_REVIEW.value,
        TestCase.created_at >= period_start,
    ]
    if effective_scope == "mine":
        filters.append(TestCase.assigned_to_user_id == current_user.id)
    if scoped_project_id is not None:
        filters.append(TestRun.project_id == scoped_project_id)

    stmt = (
        select(func.count(TestCase.id))
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
    )
    count = int((await db.execute(stmt)).scalar() or 0)
    return {"count": count}


# ── Reassignment ──────────────────────────────────────────────────────────


@router.get("/assigned-failures/{test_case_id}/reassign-options")
async def get_reassign_options(
    test_case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Picker payload for the reassign modal.

    Returns the resolved suite owner (when one exists) plus every
    QA_ENGINEER project member. The frontend renders these as the
    only valid reassignment targets — anyone outside this set fails
    the ``PUT .../reassign`` endpoint's 422 validation.

    Same authorisation contract as the PUT: caller must be QA_LEAD or
    ADMIN on the project. Returning 403 here (instead of an empty
    payload) keeps the UI honest about WHY the picker is unavailable.
    """
    try:
        return await get_reassignment_options(db, test_case_id, current_user)
    except ReassignmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.put("/assigned-failures/{test_case_id}/reassign", response_model=MyFailureItem)
async def reassign_assigned_failure(
    test_case_id: uuid.UUID,
    new_assignee_user_id: uuid.UUID = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Move a FAILED/BROKEN TestCase to a new owner.

    Authorisation:
      * Caller must be QA_LEAD or ADMIN on the failure's project.
      * ``new_assignee_user_id`` must be EITHER the resolved suite owner
        OR a QA_ENGINEER project member. Anyone else 422s — including
        another QA_LEAD or ADMIN. The auto-assigner already covers
        intra-Lead reassignment via its pool distribution.

    Returns the updated ``MyFailureItem`` so the frontend can drop the
    new row into the (now-correct) owner's view without re-fetching the
    whole list. The caller's own list shrinks by one on the next poll.
    """
    try:
        tc = await reassign_failure(
            db, test_case_id, new_assignee_user_id, current_user,
        )
        await db.commit()
    except ReassignmentError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    # Hydrate a MyFailureItem for the response. Lean re-select so we
    # surface the same column set the list endpoint uses, avoiding a
    # frontend-side type divergence.
    row = (
        await db.execute(
            select(
                TestCase.id,
                TestCase.test_name,
                TestCase.suite_name,
                TestCase.class_name,
                TestCase.status,
                TestCase.severity,
                TestCase.failure_category,
                TestCase.error_message,
                TestCase.duration_ms,
                TestCase.created_at,
                TestCase.test_run_id,
                TestRun.build_number,
                TestRun.project_id,
                Project.name.label("project_name"),
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .join(Project, Project.id == TestRun.project_id)
            .where(TestCase.id == tc.id)
        )
    ).one()
    err = (row.error_message or "")
    if len(err) > 280:
        err = err[:277] + "..."
    from app.services.runs_service import fetch_run_seq_map
    seq_map = await fetch_run_seq_map(db, [row.test_run_id])
    return MyFailureItem(
        id=row.id,
        test_name=row.test_name,
        suite_name=row.suite_name,
        class_name=row.class_name,
        status=row.status,
        severity=row.severity,
        failure_category=row.failure_category,
        error_message=err or None,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        test_run_id=row.test_run_id,
        build_number=str(row.build_number) if row.build_number is not None else None,
        project_id=row.project_id,
        project_name=row.project_name,
        navigation_url=f"/runs/{row.test_run_id}/tests/{row.id}",
        run_seq=seq_map.get(str(row.test_run_id)),
        failure_count=1,
    )


# ── Triage status ─────────────────────────────────────────────────────────


@router.put(
    "/assigned-failures/{test_case_id}/triage",
    response_model=MyFailureItem,
)
async def update_failure_triage_status(
    test_case_id: uuid.UUID,
    payload: TriageStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Move a failure to a new ``triage_status``.

    Statuses (see ``TriageStatus`` enum):

    * ``PENDING_REVIEW``     — default; the row appears on /my-failures.
    * ``REVIEWED_APPROVED``  — looked at, no action. Known flake or
                               environmental issue.
    * ``DEFECT_CREATED``     — defect/bug logged. ``notes`` typically
                               holds the bug link.
    * ``WONT_FIX``           — deprecated test or accepted failure.
                               ``notes`` typically holds the rationale.

    Authorisation: the assignee themselves, OR a QA_LEAD / ADMIN on the
    project. Anyone else 403s.

    The row drops off ``GET /assigned-failures`` (and the count badge)
    on the next poll once status moves off PENDING_REVIEW — that's the
    primary mechanism for "resolving" an inbox item.
    """
    try:
        tc = await update_triage_status(
            db,
            test_case_id=test_case_id,
            new_status=payload.status,
            notes=payload.notes,
            actor_user=current_user,
        )
        await db.commit()
    except TriageError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    # Hydrate a MyFailureItem so the frontend can splice the row into
    # the (new) status bucket without re-fetching.
    row = (
        await db.execute(
            select(
                TestCase.id,
                TestCase.test_name,
                TestCase.suite_name,
                TestCase.class_name,
                TestCase.status,
                TestCase.severity,
                TestCase.failure_category,
                TestCase.error_message,
                TestCase.duration_ms,
                TestCase.created_at,
                TestCase.test_run_id,
                TestCase.triage_status,
                TestCase.triage_notes,
                TestRun.build_number,
                TestRun.project_id,
                Project.name.label("project_name"),
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .join(Project, Project.id == TestRun.project_id)
            .where(TestCase.id == tc.id)
        )
    ).one()
    err = (row.error_message or "")
    if len(err) > 280:
        err = err[:277] + "..."
    from app.services.runs_service import fetch_run_seq_map
    seq_map = await fetch_run_seq_map(db, [row.test_run_id])
    return MyFailureItem(
        id=row.id,
        test_name=row.test_name,
        suite_name=row.suite_name,
        class_name=row.class_name,
        status=row.status,
        severity=row.severity,
        failure_category=row.failure_category,
        error_message=err or None,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        test_run_id=row.test_run_id,
        build_number=str(row.build_number) if row.build_number is not None else None,
        project_id=row.project_id,
        project_name=row.project_name,
        navigation_url=f"/runs/{row.test_run_id}/tests/{row.id}",
        triage_status=row.triage_status,
        triage_notes=row.triage_notes,
        run_seq=seq_map.get(str(row.test_run_id)),
        failure_count=1,
    )
