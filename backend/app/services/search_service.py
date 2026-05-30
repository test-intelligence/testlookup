from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.postgres import TestCase, TestRun
from app.services.sql_utils import like_contains


def build_search_filters(
    q: str,
    project_id: str | None,
    status: str | None,
    days: int | None,
    allowed_project_ids: Iterable[uuid.UUID] | None = None,
):
    pattern = like_contains(q)
    filters = [
        or_(
            TestCase.test_name.ilike(pattern, escape="\\"),
            TestCase.suite_name.ilike(pattern, escape="\\"),
            # Run-level suite label — surfaces tests of live_stream runs
            # whose per-event ``tc.suite_name`` is the test class name
            # (old TestNG-listener behaviour). Without this, a query for
            # the session-supplied label ("API Regression Multi-Class")
            # never matches those tests even though they're clearly
            # part of that suite.
            TestRun.primary_suite_name.ilike(pattern, escape="\\"),
            TestCase.error_message.ilike(pattern, escape="\\"),
        )
    ]
    if project_id:
        # Single-project pin (access already verified by the router).
        filters.append(TestRun.project_id == project_id)
    elif allowed_project_ids is not None:
        # Non-admin fan-out across the user's accessible projects. Empty set
        # means "no memberships" → we inject a guaranteed-false predicate so
        # the query returns zero rows without a database round-trip.
        allowed = list(allowed_project_ids)
        if not allowed:
            filters.append(TestRun.project_id.in_([]))
        else:
            filters.append(TestRun.project_id.in_(allowed))
    if status:
        filters.append(TestCase.status == status.upper())
    if days:
        filters.append(TestCase.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    return filters


async def search_test_cases_query(
    db: AsyncSession,
    q: str,
    page: int,
    size: int,
    project_id: str | None = None,
    status: str | None = None,
    days: int | None = None,
    allowed_project_ids: Iterable[uuid.UUID] | None = None,
):
    # Correlated subquery for failure_count — scoped to the *same project* as
    # the matched test case. The previous implementation used an unscoped join
    # on ``test_fingerprint`` which leaked failure aggregates across tenants.
    history_case = aliased(TestCase, name="history_case")
    history_run = aliased(TestRun, name="history_run")

    failure_count_subq = (
        select(func.count())
        .select_from(history_case)
        .join(history_run, history_run.id == history_case.test_run_id)
        .where(
            history_case.test_fingerprint == TestCase.test_fingerprint,
            history_case.status == "FAILED",
            history_run.project_id == TestRun.project_id,
        )
        .correlate(TestCase, TestRun)
        .scalar_subquery()
    )

    filters = build_search_filters(q, project_id, status, days, allowed_project_ids)

    # Dedupe: one row per logical test case (test_fingerprint within a
    # project). Without this, the same test that ran across N builds shows
    # up N times in search results — the user reported seeing 3 copies of
    # a single test because it had executed in 3 runs. ``DISTINCT ON
    # (project, fingerprint)`` keeps the most recent execution thanks to
    # the matching ORDER BY clause. The page/limit/offset is then applied
    # over the distinct set.
    distinct_keys = [TestRun.project_id, TestCase.test_fingerprint]
    inner = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at.label("last_run_date"),
            TestCase.test_fingerprint.label("_fp"),
            TestRun.project_id.label("_pid"),
            failure_count_subq.label("failure_count"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
        .distinct(*distinct_keys)
        # DISTINCT ON requires the leading ORDER BY columns to match the
        # distinct columns; recency is the tiebreaker we actually want.
        .order_by(
            TestRun.project_id,
            TestCase.test_fingerprint,
            TestCase.created_at.desc(),
        )
        .subquery()
    )
    query = (
        select(
            inner.c.test_case_id,
            inner.c.test_run_id,
            inner.c.test_name,
            inner.c.suite_name,
            inner.c.status,
            inner.c.last_run_date,
            inner.c.failure_count,
        )
        .order_by(inner.c.last_run_date.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    # Count distinct logical tests (one per (project, fingerprint)), not raw
    # rows, so the pagination total matches what the user actually sees.
    distinct_pairs = (
        select(TestRun.project_id, TestCase.test_fingerprint)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
        .distinct()
        .subquery()
    )
    count_query = select(func.count()).select_from(distinct_pairs)
    rows = (await db.execute(query)).all()
    total = (await db.execute(count_query)).scalar() or 0
    items = []
    for row in rows:
        item = dict(row._mapping)
        item["match_reasons"] = ["Keyword match on test name or error message"]
        item["source_mode_used"] = "keyword"
        item["relevance_score"] = 1.0
        items.append(item)
    return items, total, -(-total // size)
