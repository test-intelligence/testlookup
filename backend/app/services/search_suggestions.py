"""Suggested searches for one project (W12 SRCH-03, ``GET /search/suggested``).

What the /search page offers while its box is empty and one project is
pinned: the tests failing most in the last 7 days, the newest open defects
that carry a Jira key (the key is what global search matches, so a key is
what finds the defect again), and the project's active release.

Three statements on the request session, each bounded by ``SUGGESTED_MAX``
(or 1), each on an active project only. Each list is a TOP-N, never a count:
a failing test carries its own failure count in ``detail``. Commit-free.

The caller has already authorised ``project_id`` (``resolve_project_scope``).
Every query a suggestion runs is built with ``quote_term`` and lands in the
search box as typed text: it is parsed like anything a person types.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.search_vocab import (
    SUGGESTED_FAILING_DAYS,
    SUGGESTED_MAX,
    quote_term,
)
from app.models.postgres import Defect, Project, Release, TestCase, TestRun
from app.services import defect_status

_FAILING = ("FAILED", "BROKEN")
_STATUS_LABEL = {defect_status.OPEN: "Open", defect_status.IN_PROGRESS: "In progress"}


def failing_tests_statement(project_id: uuid.UUID, since: datetime):
    failures = func.count().label("failures")
    return (
        select(TestCase.test_name, failures)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .join(Project, Project.id == TestRun.project_id)
        .where(
            TestRun.project_id == project_id,
            Project.is_active.is_(True),
            TestRun.created_at >= since,
            TestCase.created_at >= since,
            TestCase.status.in_(_FAILING),
        )
        .group_by(TestCase.test_name)
        .order_by(func.count().desc(), TestCase.test_name)
        .limit(SUGGESTED_MAX)
    )


def open_defects_statement(project_id: uuid.UUID):
    return (
        select(Defect.jira_ticket_id, Defect.severity, Defect.resolution_status)
        .join(Project, Project.id == Defect.project_id)
        .where(
            Defect.project_id == project_id,
            Project.is_active.is_(True),
            Defect.jira_ticket_id.is_not(None),
            Defect.resolution_status.in_(defect_status.ACTIVE_STATUSES),
        )
        .order_by(Defect.created_at.desc())
        .limit(SUGGESTED_MAX)
    )


def active_release_statement(project_id: uuid.UUID):
    return (
        select(Release.name)
        .join(Project, Project.id == Release.project_id)
        .where(
            Release.project_id == project_id,
            Project.is_active.is_(True),
            Release.is_active.is_(True),
        )
        .limit(1)
    )


def _failures_detail(count: int) -> str:
    return f"{count} failure{'' if count == 1 else 's'} in {SUGGESTED_FAILING_DAYS} days"


def _defect_detail(status: Optional[str], severity: Optional[str]) -> str:
    parts = [_STATUS_LABEL.get(str(status or "").upper(), "Open")]
    if severity:
        parts.append(str(severity).upper())
    return " · ".join(parts)


async def suggestions_for_project(db: AsyncSession, project_id: uuid.UUID, *, now: datetime) -> dict:
    """A ``SearchSuggestionsResponse`` body for ``project_id`` (already authorised)."""
    since = now - timedelta(days=SUGGESTED_FAILING_DAYS)

    failing = []
    for row in (await db.execute(failing_tests_statement(project_id, since))).all():
        q = quote_term(row.test_name or "")
        if q:
            failing.append({
                "kind": "failing_test", "label": row.test_name, "q": q, "scope": "tests",
                "detail": _failures_detail(int(row.failures or 0)),
            })

    defects = []
    for row in (await db.execute(open_defects_statement(project_id))).all():
        q = quote_term(row.jira_ticket_id or "")
        if q:
            defects.append({
                "kind": "open_defect", "label": row.jira_ticket_id, "q": q, "scope": "defects",
                "detail": _defect_detail(row.resolution_status, row.severity),
            })

    release = None
    name = (await db.execute(active_release_statement(project_id))).scalar_one_or_none()
    term = quote_term(name or "")
    if term:
        release = {
            "kind": "active_release", "label": name, "q": f"release:{term} status:failed",
            "scope": "tests", "detail": "Active release",
        }

    return {
        "project_id": project_id,
        "window_days": SUGGESTED_FAILING_DAYS,
        "failing_tests": failing,
        "open_defects": defects,
        "active_release": release,
    }
