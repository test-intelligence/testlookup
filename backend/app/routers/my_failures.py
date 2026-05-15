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
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.models.postgres import (
    Project,
    TestCase,
    TestRun,
    TestStatus,
    User,
)
from app.models.schemas import MyFailureItem, MyFailureListResponse

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

    base_filters = [
        TestCase.assigned_to_user_id == current_user.id,
        TestCase.status.in_(_ACTIONABLE_STATUSES),
        TestCase.created_at >= period_start,
    ]
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

    items: list[MyFailureItem] = []
    for r in rows:
        # Truncate error_message to keep payloads bounded — full message is
        # available on the test-case detail page if the user drills in.
        err = (r.error_message or "")
        if len(err) > 280:
            err = err[:277] + "..."
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

    filters = [
        TestCase.assigned_to_user_id == current_user.id,
        TestCase.status.in_(_ACTIONABLE_STATUSES),
        TestCase.created_at >= period_start,
    ]
    if scoped_project_id is not None:
        filters.append(TestRun.project_id == scoped_project_id)

    stmt = (
        select(func.count(TestCase.id))
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
    )
    count = int((await db.execute(stmt)).scalar() or 0)
    return {"count": count}
