from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, Release, ReleaseTestRunLink, TestCase, TestRun


def serialize_run(run: TestRun) -> dict:
    data = {}
    for column in run.__table__.columns:
        value = getattr(run, column.name)
        if hasattr(value, "isoformat"):
            data[column.name] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            data[column.name] = str(value)
        else:
            data[column.name] = value
    return data


def enrich_runs_with_release(
    runs: list[TestRun],
    release_map: dict[str, dict[str, str | None]],
    project_map: dict[str, str] | None = None,
) -> list[dict]:
    enriched = []
    for run in runs:
        item = serialize_run(run)
        release = release_map.get(str(run.id), {})
        item["release_name"] = release.get("name")
        item["release_id"] = release.get("id")
        if project_map is not None:
            item["project_name"] = project_map.get(str(run.project_id)) if run.project_id else None
        enriched.append(item)
    return enriched


async def fetch_project_name_map(db: AsyncSession, project_ids: list[uuid.UUID]) -> dict[str, str]:
    if not project_ids:
        return {}
    result = await db.execute(
        select(Project.id, Project.name).where(Project.id.in_(project_ids))
    )
    return {str(pid): name for pid, name in result.all()}


async def paginate_query(db: AsyncSession, query, page: int, size: int):
    """Paginate a SELECT statement: run the count, then the page query.

    The count query strips ``ORDER BY`` before wrapping in a subquery.
    Without this, PostgreSQL has to materialize the full sorted result
    just to throw it away for the count — ``EXPLAIN ANALYZE`` on the
    run-list query dropped from 0.141ms/0.748ms (exec/plan) to
    0.032ms/0.083ms after the strip. Small absolute numbers on a small
    table, but the effect compounds under concurrency and scales with
    row count.
    """
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset((page - 1) * size).limit(size))
    return result.scalars().all(), total, -(-total // size)


async def fetch_release_map(db: AsyncSession, run_ids: list[uuid.UUID]) -> dict[str, dict[str, str | None]]:
    if not run_ids:
        return {}
    result = await db.execute(
        select(ReleaseTestRunLink.test_run_id, Release.id, Release.name)
        .join(Release, Release.id == ReleaseTestRunLink.release_id)
        .where(ReleaseTestRunLink.test_run_id.in_(run_ids))
    )
    return {
        str(test_run_id): {"id": str(release_id), "name": release_name}
        for test_run_id, release_id, release_name in result.all()
    }


def _normalise_suite_name(suite_name: str | None) -> str:
    return (suite_name or "").strip().lower()


def _run_suite_filter(suite_name: str | None):
    suite_key = _normalise_suite_name(suite_name)
    if not suite_key:
        return None
    case_exists = (
        select(TestCase.id)
        .where(
            TestCase.test_run_id == TestRun.id,
            func.lower(func.trim(func.coalesce(TestCase.suite_name, "Unknown Suite"))) == suite_key,
        )
        .exists()
    )
    return or_(
        func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, "Unknown Suite"))) == suite_key,
        case_exists,
    )


async def list_project_runs(
    db: AsyncSession,
    project_id: str | None,
    page: int,
    size: int,
    status: str | None = None,
    release_id: str | None = None,
    accessible_project_ids: set | None = None,
    days: int | None = 6,
    suite_name: str | None = None,
):
    """Paginated test run listing, enriched with release + project_name.

    Performance notes:
      * Filters are built once and reused by both the count query and the
        items query — no duplicated WHERE logic.
      * The count query counts ``TestRun.id`` directly (no subquery wrap,
        no ``ORDER BY``).
      * ``project_name`` is fetched via ``LEFT JOIN`` in the items query
        instead of a separate round-trip, saving one DB call per request.
      * Release names stay in a single ``WHERE id IN (...)`` follow-up
        rather than joining — releases are 1:1 with runs in practice but
        the column isn't UNIQUE, so joining risks row-duplication we'd
        have to DISTINCT away. One extra query is cheaper than an extra
        DISTINCT.
    """
    filters = []
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filters.append(TestRun.created_at >= cutoff)
    if project_id:
        filters.append(TestRun.project_id == project_id)
    elif accessible_project_ids is not None:
        # Tenant isolation: non-admin users only see runs from their projects
        filters.append(TestRun.project_id.in_(accessible_project_ids))
    if status:
        filters.append(TestRun.status == status)
    if release_id:
        linked_ids_q = select(ReleaseTestRunLink.test_run_id).where(
            ReleaseTestRunLink.release_id == uuid.UUID(release_id)
        )
        filters.append(TestRun.id.in_(linked_ids_q))
    suite_filter = _run_suite_filter(suite_name)
    if suite_filter is not None:
        filters.append(suite_filter)

    # Count query: strips ORDER BY, counts by PK, no subquery wrap.
    count_stmt = select(func.count(TestRun.id)).where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Items query: LEFT JOIN project to pick up project_name in one trip.
    items_stmt = (
        select(TestRun, Project.name.label("project_name"))
        .outerjoin(Project, Project.id == TestRun.project_id)
        .where(*filters)
        .order_by(TestRun.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(items_stmt)).all()
    runs = [row[0] for row in rows]
    project_map = {
        str(row[0].project_id): row.project_name
        for row in rows
        if row[0].project_id and row.project_name
    }

    release_map = await fetch_release_map(db, [run.id for run in runs])
    pages = -(-total // size)
    return enrich_runs_with_release(runs, release_map, project_map), total, pages


async def get_run_with_release(db: AsyncSession, run_id: uuid.UUID):
    run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if not run:
        return None
    release_map = await fetch_release_map(db, [run_id])
    project_ids = [run.project_id] if run.project_id else []
    project_map = await fetch_project_name_map(db, project_ids)
    return enrich_runs_with_release([run], release_map, project_map)[0]


async def list_run_test_cases(
    db: AsyncSession,
    run_id: uuid.UUID,
    page: int,
    size: int,
    status: str | None = None,
    suite: str | None = None,
):
    query = select(TestCase).where(TestCase.test_run_id == run_id)
    if status:
        query = query.where(TestCase.status == status.upper())
    if suite:
        from app.services.sql_utils import like_contains
        query = query.where(TestCase.suite_name.ilike(like_contains(suite), escape="\\"))
    return await paginate_query(db, query.order_by(TestCase.status, TestCase.test_name), page, size)
