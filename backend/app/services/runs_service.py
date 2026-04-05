from __future__ import annotations

import uuid

from sqlalchemy import func, select
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
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
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


async def list_project_runs(
    db: AsyncSession,
    project_id: str | None,
    page: int,
    size: int,
    status: str | None = None,
    release_id: str | None = None,
    accessible_project_ids: set | None = None,
):
    query = select(TestRun)
    if project_id:
        query = query.where(TestRun.project_id == project_id)
    elif accessible_project_ids is not None:
        # Tenant isolation: non-admin users only see runs from their projects
        query = query.where(TestRun.project_id.in_(accessible_project_ids))
    if status:
        query = query.where(TestRun.status == status)
    if release_id:
        linked_ids_q = select(ReleaseTestRunLink.test_run_id).where(ReleaseTestRunLink.release_id == uuid.UUID(release_id))
        query = query.where(TestRun.id.in_(linked_ids_q))
    runs, total, pages = await paginate_query(db, query.order_by(TestRun.created_at.desc()), page, size)
    release_map = await fetch_release_map(db, [run.id for run in runs])
    project_ids = list({run.project_id for run in runs if run.project_id})
    project_map = await fetch_project_name_map(db, project_ids)
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
        query = query.where(TestCase.suite_name.ilike(f"%{suite}%"))
    return await paginate_query(db, query.order_by(TestCase.status, TestCase.test_name), page, size)
