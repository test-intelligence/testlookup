"""The per-project "Require review before Active" setting (W28 TCM-02).

``projects.require_case_review`` (migration 0222, default false: owner decision
OR-04). Reads only; nothing here flushes or commits.

Both reads go through ``(await db.execute(stmt))`` and compare with ``is True``
so a hand-built session in a test (an ``AsyncMock``, the integration suite's
``FakeAsyncSession``) reads "not required" instead of crashing: a MagicMock
answer is not ``True``.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project


async def project_requires_case_review(db: AsyncSession, project_id) -> bool:
    """Whether the project requires an approved review before a case is Active."""
    if project_id is None:
        return False
    value = (
        await db.execute(
            select(Project.require_case_review).where(Project.id == project_id)
        )
    ).scalar_one_or_none()
    return value is True


async def review_required_by_project(
    db: AsyncSession, project_ids: Iterable
) -> dict[uuid.UUID, bool]:
    """The setting for several projects in one statement ({} and no statement for none)."""
    ids = {pid for pid in project_ids if pid is not None}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(Project.id, Project.require_case_review).where(Project.id.in_(ids))
        )
    ).all()
    result: dict[uuid.UUID, bool] = {}
    if not isinstance(rows, (list, tuple)):
        return result
    for entry in rows:
        try:
            project_id, required = entry
        except (TypeError, ValueError):
            continue
        result[project_id] = required is True
    return result
