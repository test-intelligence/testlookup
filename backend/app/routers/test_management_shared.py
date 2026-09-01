from __future__ import annotations

import uuid
from typing import cast

import structlog
from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, resolve_project_scope
from app.db.postgres import get_db
from app.models.postgres import ManagedTestCase, TestCase, TestPlan, TestRun, User

logger = structlog.get_logger(__name__)


def row(model_instance, schema_class):
    """Map ORM instance to Pydantic schema using from_attributes."""
    return schema_class.model_validate(model_instance)


async def get_or_404(db: AsyncSession, model, entity_id, detail: str):
    instance = await db.get(model, entity_id)
    if not instance:
        raise HTTPException(status_code=404, detail=detail)
    return instance


async def require_case_access(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ManagedTestCase:
    """Dependency: load a ManagedTestCase and enforce the caller's project
    access before any ``/cases/{case_id}*`` handler runs.

    The service helpers (``get_*_or_404``) fetch by PK only, so without this
    every by-id endpoint was a cross-tenant IDOR (read/edit/delete/review of
    another project's test case).
    """
    case = await get_or_404(db, ManagedTestCase, case_id, "Test case not found")
    await resolve_project_scope(db, current_user, str(case.project_id))
    return cast(ManagedTestCase, case)


async def require_case_access_for_review_target(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ManagedTestCase | TestCase:
    """Authorize both managed IDs and automation IDs for the review shim.

    The service turns an accessible automation-row ID into the documented 400
    guidance. Unknown or inaccessible IDs remain indistinguishable to callers.
    """
    managed = await db.get(ManagedTestCase, case_id)
    if managed is not None:
        await resolve_project_scope(db, current_user, str(managed.project_id))
        return managed

    result = await db.execute(
        select(TestCase, TestRun.project_id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.id == case_id)
        .limit(1)
    )
    automation_row = result.first()
    if automation_row is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    test_case, project_id = automation_row
    await resolve_project_scope(db, current_user, str(project_id))
    return cast(TestCase, test_case)


async def require_plan_access(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> TestPlan:
    """Dependency: load a TestPlan and enforce project access — same rationale
    as ``require_case_access`` for the ``/plans/{plan_id}*`` surface."""
    plan = await get_or_404(db, TestPlan, plan_id, "Test plan not found")
    await resolve_project_scope(db, current_user, str(plan.project_id))
    return cast(TestPlan, plan)


async def paginate_scalars(db: AsyncSession, query, page: int, size: int):
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    result = await db.execute(query.offset((page - 1) * size).limit(size))
    return result.scalars().all(), total, -(-total // size)


def apply_model_updates(model_instance, values: dict) -> None:
    for field, value in values.items():
        if field == "status":
            raise ValueError("status updates must use the test-case lifecycle service")
        setattr(model_instance, field, value)
