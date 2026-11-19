from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    holds_role_for_project_write,
    require_role,
    resolve_project_scope,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    AIGenerateStrategyRequest,
    AITaskEnqueueResponse,
    TestStrategyResponse,
    TestStrategyUpdate,
)
from app.routers.test_management_shared import row
from app.services.test_management_ai_service import (
    enqueue_ai_strategy_generation,
    generate_ai_strategy,
    get_strategy_or_404,
    list_strategies as list_strategy_models,
    update_strategy as update_strategy_model,
)

router = APIRouter()

# Role floors (instance role, taken as a route PARAMETER so the decorator lines
# and their activity-coverage baseline keys are unchanged). These routes checked
# project membership only: a VIEWER rewrote a strategy, set it to "approved"
# and started AI generation charged to the project. Editing: TESTER, like a
# test-case write. Changing the status is a sign-off: QA_LEAD. The two AI
# routes spend model budget: QA_ENGINEER. Reads stay open to every member.

STRATEGY_STATUS_CHANGE_REFUSED = (
    "Changing a strategy's status requires at least QA_LEAD role. Ask a QA lead to "
    "change the status, or save your other edits without it."
)


@router.get("/strategies", response_model=list[TestStrategyResponse])
async def list_strategies(
    project_id: Optional[uuid.UUID] = None,
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
        return []
    strategies = await list_strategy_models(db, project_id)
    return [row(strategy, TestStrategyResponse) for strategy in strategies]


@router.post("/strategies/ai-generate", response_model=TestStrategyResponse)
async def ai_generate_strategy(
    payload: AIGenerateStrategyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    # Tenant guard — caller must be a member of the target project before we
    # persist an AI-generated strategy into it (matches /plans/ai-create).
    await resolve_project_scope(db, current_user, str(payload.project_id))
    strategy = await generate_ai_strategy(db, payload, current_user)
    await db.commit()
    await db.refresh(strategy)
    return row(strategy, TestStrategyResponse)


@router.post("/strategies/ai-generate/async", response_model=AITaskEnqueueResponse)
async def ai_generate_strategy_async(
    payload: AIGenerateStrategyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    # Verify access before queueing — the worker runs without request context.
    await resolve_project_scope(db, current_user, str(payload.project_id))
    return await enqueue_ai_strategy_generation(payload, current_user)


@router.get("/strategies/{strategy_id}", response_model=TestStrategyResponse)
async def get_strategy(
    strategy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # ``get_strategy_or_404`` is a bare ``db.get`` by primary key. Every other
    # route in this file resolves project scope; these two did not, so GET read
    # and PUT overwrote another tenant's strategy -- and ``update_strategy``
    # passes ``project_id`` to ``audit_event``, so the audit row landed in the
    # victim's project naming the attacker as actor.
    strategy = await get_strategy_or_404(db, strategy_id)
    await resolve_project_scope(db, current_user, str(strategy.project_id))
    return row(strategy, TestStrategyResponse)


@router.put("/strategies/{strategy_id}", response_model=TestStrategyResponse)
async def update_strategy(
    strategy_id: uuid.UUID,
    payload: TestStrategyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.TESTER)),
):
    existing = await get_strategy_or_404(db, strategy_id)
    await resolve_project_scope(db, current_user, str(existing.project_id))
    # A status the caller merely sends back unchanged is not a status change, so
    # a client that saves the whole strategy keeps working for a TESTER. The
    # whole request is refused, not just the status: no partial save.
    changes_status = "status" in payload.model_fields_set and payload.status != existing.status
    if changes_status and not await holds_role_for_project_write(
        db, current_user, existing.project_id, UserRole.QA_LEAD
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=STRATEGY_STATUS_CHANGE_REFUSED,
        )
    strategy = await update_strategy_model(db, strategy_id, payload, current_user)
    await db.commit()
    await db.refresh(strategy)
    return row(strategy, TestStrategyResponse)
