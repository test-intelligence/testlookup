from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.postgres import TestCase, TestRun


def build_search_filters(q: str, project_id: str | None, status: str | None, days: int | None):
    filters = [
        or_(
            TestCase.test_name.ilike(f"%{q}%"),
            TestCase.suite_name.ilike(f"%{q}%"),
            TestCase.error_message.ilike(f"%{q}%"),
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
    history_case = aliased(TestCase)
    filters = build_search_filters(q, project_id, status, days)
    query = (
        select(
            TestCase.id.label("test_case_id"),
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at.label("last_run_date"),
            func.count().filter(history_case.status == "FAILED").label("failure_count"),
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .outerjoin(history_case, history_case.test_fingerprint == TestCase.test_fingerprint)
        .where(*filters)
        .group_by(
            TestCase.id,
            TestCase.test_run_id,
            TestCase.test_name,
            TestCase.suite_name,
            TestCase.status,
            TestCase.created_at,
        )
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
