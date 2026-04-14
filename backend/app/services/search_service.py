from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.postgres import TestCase, TestRun
from app.services.sql_utils import like_contains


def build_search_filters(q: str, project_id: str | None, status: str | None, days: int | None):
    pattern = like_contains(q)
    filters = [
        or_(
            TestCase.test_name.ilike(pattern, escape="\\"),
            TestCase.suite_name.ilike(pattern, escape="\\"),
            TestCase.error_message.ilike(pattern, escape="\\"),
        )
    ]
    if project_id:
        filters.append(TestRun.project_id == project_id)
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

    filters = build_search_filters(q, project_id, status, days)
    query = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at.label("last_run_date"),
            failure_count_subq.label("failure_count"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
        .order_by(TestCase.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    count_query = (
        select(func.count(func.distinct(TestCase.id)))
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(*filters)
    )
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
