from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import ManagedTestCase, TestPlanItem, User
from app.models.schemas import (
    AIOptimizePlanRequest,
    AITaskEnqueueResponse,
    ExecuteTestPlanItemRequest,
    TestPlanCreate,
    TestPlanItemCreate,
    TestPlanItemResponse,
    TestPlanListResponse,
    TestPlanResponse,
    TestPlanUpdate,
)
from app.routers.test_management_shared import logger, require_plan_access, row
from app.services.test_management_audit_service import audit_event
from app.services.test_management_service import (
    add_test_plan_item,
    create_test_plan,
    get_plan_or_404,
    list_test_plans,
    record_test_plan_execution,
    recompute_plan_counts,
    remove_test_plan_item,
    update_test_plan,
)

router = APIRouter()


@router.get("/plans", response_model=TestPlanListResponse)
async def list_plans(
    project_id: Optional[uuid.UUID] = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # F-042: this check ran ONLY in the ``not project_id`` branch, so naming a
    # project skipped it entirely — the guard fired only where there was
    # nothing to guard. Third recurrence of the class (F-033 digests, F-040
    # chat). ``resolve_project_scope`` 403s a non-admin naming a project they
    # do not belong to; the empty return below preserves today's behaviour for
    # a non-admin who names no project at all.
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    if scoped_project_id is None and allowed is not None:
        return {"items": [], "total": 0, "page": page, "size": size, "pages": 0}
    items, total, pages = await list_test_plans(db, project_id, page, size, status)
    return {"items": [row(plan, TestPlanResponse) for plan in items], "total": total, "page": page, "size": size, "pages": pages}


@router.post("/plans", response_model=TestPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: TestPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    plan = await create_test_plan(db, payload, current_user)
    await db.commit()
    await db.refresh(plan)
    return row(plan, TestPlanResponse)


@router.get("/plans/{plan_id}", response_model=TestPlanResponse)
async def get_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _plan=Depends(require_plan_access),
):
    return row(await get_plan_or_404(db, plan_id), TestPlanResponse)


@router.patch("/plans/{plan_id}", response_model=TestPlanResponse)
async def update_plan(
    plan_id: uuid.UUID,
    payload: TestPlanUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _plan=Depends(require_plan_access),
):
    plan = await update_test_plan(db, plan_id, payload, current_user)
    await db.commit()
    await db.refresh(plan)
    return row(plan, TestPlanResponse)


@router.get("/plans/{plan_id}/items", response_model=list[TestPlanItemResponse])
async def list_plan_items(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _plan=Depends(require_plan_access),
):
    await get_plan_or_404(db, plan_id)
    result = await db.execute(select(TestPlanItem).where(TestPlanItem.plan_id == plan_id).order_by(TestPlanItem.order_index))
    return [row(item, TestPlanItemResponse) for item in result.scalars().all()]


@router.post("/plans/{plan_id}/items", response_model=TestPlanItemResponse, status_code=status.HTTP_201_CREATED)
async def add_plan_item(
    plan_id: uuid.UUID,
    payload: TestPlanItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _plan=Depends(require_plan_access),
):
    item = await add_test_plan_item(db, plan_id, payload, current_user)
    await db.commit()
    await db.refresh(item)
    return row(item, TestPlanItemResponse)


@router.delete("/plans/{plan_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_plan_item(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _plan=Depends(require_plan_access),
):
    await remove_test_plan_item(db, plan_id, item_id)
    await db.commit()


@router.patch("/plans/{plan_id}/items/{item_id}/execute", response_model=TestPlanItemResponse)
async def record_execution(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ExecuteTestPlanItemRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _plan=Depends(require_plan_access),
):
    item = await record_test_plan_execution(
        db,
        plan_id,
        item_id,
        payload.execution_status,
        payload.execution_notes,
        payload.actual_duration_minutes,
        current_user,
    )
    await db.commit()
    await db.refresh(item)
    return row(item, TestPlanItemResponse)


@router.post("/plans/ai-create", response_model=TestPlanResponse, status_code=status.HTTP_201_CREATED)
async def ai_create_plan(
    payload: AIOptimizePlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from app.core.deps import resolve_project_scope
    from app.services.test_case_ai_agent import ai_optimize_plan
    from app.models.postgres import TestPlan, TestPlanItem

    # Tenant guard — caller must be a member of the target project.
    await resolve_project_scope(db, current_user, str(payload.project_id))

    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.project_id == payload.project_id,
            ManagedTestCase.status.in_(["approved", "active"]),
        )
    )
    cases = result.scalars().all()
    if not cases:
        raise HTTPException(status_code=400, detail="No approved test cases found for this project")

    optimization = await ai_optimize_plan(
        [{"title": case.title, "priority": case.priority, "test_type": case.test_type, "estimated_duration_minutes": case.estimated_duration_minutes or 5} for case in cases],
        payload.constraints or "",
        project_id=payload.project_id,
    )

    plan = TestPlan(
        project_id=payload.project_id,
        name=payload.plan_name or f"AI Test Plan - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        description=optimization.get("optimization_notes"),
        ai_generated=True,
        ai_generation_context=payload.constraints,
        created_by_id=current_user.id,
        total_cases=len(cases),
    )
    db.add(plan)
    await db.flush()
    order_map = {entry.get("title", ""): entry.get("execution_order", 999) for entry in optimization.get("optimized_order", [])}
    for case in cases:
        db.add(TestPlanItem(plan_id=plan.id, test_case_id=case.id, order_index=order_map.get(case.title, 999)))
    await recompute_plan_counts(db, plan)
    await audit_event(db, "test_plan", plan.id, payload.project_id, "ai_generated", current_user, details="AI-optimized test plan created")
    await db.commit()
    await db.refresh(plan)
    return row(plan, TestPlanResponse)


@router.post("/plans/ai-create/async", response_model=AITaskEnqueueResponse)
async def ai_create_plan_async(
    payload: AIOptimizePlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Tenant guard — caller must be a member of the target project before we
    # enqueue a worker task that builds a plan in it.
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))
    try:
        from app.worker.tasks import create_ai_test_plan_task

        task = create_ai_test_plan_task.delay(
            str(payload.project_id),
            str(current_user.id),
            payload.plan_name,
            payload.constraints,
        )
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        logger.error("Failed to enqueue create_ai_test_plan_task: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI task queue unavailable. Check that the Celery worker and Redis are running.",
        )
