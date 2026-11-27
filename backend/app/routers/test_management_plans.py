from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, require_role
from app.db.postgres import get_db
from app.models.postgres import ManagedTestCase, TestPlanItem, User, UserRole
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

NO_APPROVED_CASES = "No approved test cases found for this project"
#: D-58: the async route refuses before anything is queued when no model can answer.
AI_PLAN_NO_MODEL = "A test plan is built by a model, and no model can answer right now."


async def _approved_cases(db: AsyncSession, project_id: uuid.UUID) -> list[ManagedTestCase]:
    result = await db.execute(
        select(ManagedTestCase).where(
            ManagedTestCase.project_id == project_id,
            ManagedTestCase.status.in_(["approved", "active"]),
        )
    )
    return list(result.scalars().all())


async def _no_model_reason(db: AsyncSession) -> Optional[str]:
    """The refusal sentence when the effective model cannot answer, else None.

    Reads the effective AI config (Settings > AI over env) through the request
    session and asks ``model_status_service`` (the Settings > AI model-status
    read: an Ollama probe, a cloud key, the offline ceiling) whether the model
    tier is available. Never raises: ``build_model_status`` degrades to env.
    """
    from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415
    from app.services.model_status_service import build_model_status  # noqa: PLC0415

    effective = dict(await get_effective_ai_config(db=db))
    mapped = {
        "llm_provider": effective.get("provider"),
        "llm_model": effective.get("model"),
        "ai_offline_mode": effective.get("offline_mode"),
        "base_url": effective.get("base_url"),
        "analysis_mode": effective.get("analysis_mode"),
    }
    model_status = await build_model_status({**effective, **{k: v for k, v in mapped.items() if v is not None}})
    tier = next(
        (entry for entry in model_status.get("fallback_chain") or [] if entry.get("mode") == "llm"),
        None,
    )
    if tier is None or tier.get("available"):
        return None
    reason = str(tier.get("reason") or "").strip() or "Check Settings › AI."
    return f"{AI_PLAN_NO_MODEL} {reason}"

# Role floors (instance role, taken as a route PARAMETER so the decorator lines
# and their activity-coverage baseline keys are unchanged). These routes checked
# project membership only, so a VIEWER created and edited plans and recorded
# executions under their own name, while a test-case write already needed
# TESTER. Plan writes and executions: TESTER. The two AI routes spend the
# project's model budget: QA_ENGINEER, like the run-compare narrative. Reads
# stay open to every member.


@router.get("/plans", response_model=TestPlanListResponse)
async def list_plans(
    project_id: Optional[uuid.UUID] = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if status is not None and status not in TestPlanUpdate.PLAN_STATUSES:
        # An unknown status used to answer an empty page, which read as "no such plans".
        raise HTTPException(
            status_code=422, detail="status must be one of: " + ", ".join(TestPlanUpdate.PLAN_STATUSES),
        )
    # F-042: this check ran ONLY in the ``not project_id`` branch, so naming a
    # project skipped it entirely — the guard fired only where there was
    # nothing to guard. Third recurrence of the class (F-033 digests, F-040
    # chat). ``resolve_project_scope`` 403s a non-admin naming a project they
    # do not belong to. A non-admin who names no project (All Projects) reads
    # the plans of the projects they belong to (S-D-4: this used to answer an
    # empty list for every non-admin).
    from app.core.deps import resolve_project_scope  # noqa: PLC0415

    scoped_project_id, allowed = await resolve_project_scope(
        db, current_user, str(project_id) if project_id else None
    )
    items, total, pages = await list_test_plans(
        db, scoped_project_id, page, size, status, allowed_project_ids=allowed
    )
    return {"items": [row(plan, TestPlanResponse) for plan in items], "total": total, "page": page, "size": size, "pages": pages}


@router.post("/plans", response_model=TestPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: TestPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
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
    current_user: User = Depends(require_role(UserRole.TESTER)),
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
    # W28 TCM-05: each item with its case's CURRENT version (one outer join),
    # beside the version its recorded execution ran against. D-62: the same
    # join carries the case's title, so a plan row names its case.
    result = await db.execute(
        select(TestPlanItem, ManagedTestCase.version, ManagedTestCase.title)
        .outerjoin(ManagedTestCase, ManagedTestCase.id == TestPlanItem.test_case_id)
        .where(TestPlanItem.plan_id == plan_id)
        .order_by(TestPlanItem.order_index)
    )
    items: list[TestPlanItemResponse] = []
    for item, case_version, case_title in result.all():
        response = row(item, TestPlanItemResponse)
        response.case_version = case_version if isinstance(case_version, int) else None
        response.test_case_title = case_title if isinstance(case_title, str) else None
        items.append(response)
    return items


@router.post("/plans/{plan_id}/items", response_model=TestPlanItemResponse, status_code=status.HTTP_201_CREATED)
async def add_plan_item(
    plan_id: uuid.UUID,
    payload: TestPlanItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
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
    current_user: User = Depends(require_role(UserRole.TESTER)),
    _plan=Depends(require_plan_access),
):
    await remove_test_plan_item(db, plan_id, item_id, current_user)
    await db.commit()


@router.patch("/plans/{plan_id}/items/{item_id}/execute", response_model=TestPlanItemResponse)
async def record_execution(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ExecuteTestPlanItemRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
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
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    from app.core.deps import resolve_project_scope
    from app.services.test_case_ai_agent import ai_optimize_plan
    from app.models.postgres import TestPlan, TestPlanItem

    # Tenant guard — caller must be a member of the target project.
    await resolve_project_scope(db, current_user, str(payload.project_id))

    cases = await _approved_cases(db, payload.project_id)
    if not cases:
        raise HTTPException(status_code=400, detail=NO_APPROVED_CASES)

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
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    # Tenant guard — caller must be a member of the target project before we
    # enqueue a worker task that builds a plan in it.
    from app.core.deps import resolve_project_scope
    await resolve_project_scope(db, current_user, str(payload.project_id))
    # D-58: refuse up front what the worker cannot do. It used to queue the
    # task and answer "queued" with no approved case (the task then returned
    # an error dict as a success) and with no model that could answer.
    if not await _approved_cases(db, payload.project_id):
        raise HTTPException(status_code=400, detail=NO_APPROVED_CASES)
    no_model = await _no_model_reason(db)
    if no_model is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=no_model)
    try:
        from app.worker.tasks import create_ai_test_plan_task

        task = create_ai_test_plan_task.delay(
            str(payload.project_id),
            str(current_user.id),
            payload.plan_name,
            payload.constraints,
        )
        # The status route serves a task only to whoever queued it or to a
        # member of its project; without this record it answers 404.
        from app.services.test_management_ai_service import record_ai_task_owner

        await record_ai_task_owner(task.id, user_id=current_user.id, project_id=payload.project_id)
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        logger.error("ai_task_enqueue_failed", task="create_ai_test_plan_task", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI task queue unavailable. Check that the Celery worker and Redis are running.",
        )
