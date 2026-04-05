from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
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
    strategies = await list_strategy_models(db, project_id)
    return [row(strategy, TestStrategyResponse) for strategy in strategies]


@router.post("/strategies/ai-generate", response_model=TestStrategyResponse)
async def ai_generate_strategy(
    payload: AIGenerateStrategyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return row(await generate_ai_strategy(db, payload, current_user), TestStrategyResponse)


@router.post("/strategies/ai-generate/async", response_model=AITaskEnqueueResponse)
async def ai_generate_strategy_async(
    payload: AIGenerateStrategyRequest,
    current_user: User = Depends(get_current_active_user),
):
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
    return row(await update_strategy_model(db, strategy_id, payload, current_user), TestStrategyResponse)
