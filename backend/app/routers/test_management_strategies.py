from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, resolve_project_scope
from app.db.postgres import get_db
from app.models.postgres import User
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
    current_user: User = Depends(get_current_active_user),
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
    current_user: User = Depends(get_current_active_user),
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
    return row(await get_strategy_or_404(db, strategy_id), TestStrategyResponse)


@router.put("/strategies/{strategy_id}", response_model=TestStrategyResponse)
async def update_strategy(
    strategy_id: uuid.UUID,
    payload: TestStrategyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    strategy = await update_strategy_model(db, strategy_id, payload, current_user)
    await db.commit()
    await db.refresh(strategy)
    return row(strategy, TestStrategyResponse)
