"""
Feature flags API — Tier 0A.

Admin-only CRUD over the ``feature_flags`` table plus a tenant-scoped
``/status`` endpoint that any authenticated user can call to check whether
a flag applies to them for their current project.

All write operations land a ``settings_audit_log`` row via
``services/feature_flags._write_audit_entry``.
"""
from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import User, UserRole
from app.models.schemas import (
    FeatureFlagCreate,
    FeatureFlagHistoryEntry,
    FeatureFlagResponse,
    FeatureFlagUpdate,
)
from app.services import feature_flags as ff_service

router = APIRouter(prefix="/api/v1/feature-flags", tags=["Feature Flags"])
logger = structlog.get_logger("routers.feature_flags")


@router.get(
    "",
    response_model=list[FeatureFlagResponse],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def list_feature_flags(db: AsyncSession = Depends(get_db)):
    """List every flag in the store. ADMIN only."""
    rows = await ff_service.list_flags(db)
    return await ff_service.attach_last_change(db, rows)


@router.post(
    "",
    response_model=FeatureFlagResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_feature_flag(
    payload: FeatureFlagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a new feature flag. ADMIN only."""
    flag = await ff_service.create_flag(
        db,
        key=payload.key,
        description=payload.description,
        enabled_global=payload.enabled_global,
        enabled_projects=payload.enabled_projects,
        enabled_roles=payload.enabled_roles,
        rollout_percent=payload.rollout_percent,
        actor=current_user,
        reason=payload.reason,
    )
    await db.commit()
    await ff_service.invalidate_flag_cache(payload.key)
    (flag,) = await ff_service.attach_last_change(db, [flag])
    return flag


@router.get("/{key}", response_model=FeatureFlagResponse)
async def get_feature_flag(
    key: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get a single flag by key. ADMIN only."""
    flag = await ff_service.get_flag(db, key)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature flag '{key}' not found",
        )
    (flag,) = await ff_service.attach_last_change(db, [flag])
    return flag


@router.get(
    "/{key}/history",
    response_model=list[FeatureFlagHistoryEntry],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def get_feature_flag_history(
    key: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """A flag's changes, newest first: action, who, when, the changed field
    names and the reason given (W17 ADM-07). ADMIN only; never the values."""
    if await ff_service.get_flag(db, key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature flag '{key}' not found",
        )
    return await ff_service.flag_history(db, key, limit=limit)


@router.patch("/{key}", response_model=FeatureFlagResponse)
async def update_feature_flag(
    key: str,
    payload: FeatureFlagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Partial-update a feature flag. ADMIN only. Any unset field is kept."""
    updates = payload.model_dump(exclude_unset=True)
    reason = updates.pop("reason", None)
    flag = await ff_service.update_flag(
        db, key=key, updates=updates, actor=current_user, reason=reason,
    )
    await db.commit()
    await ff_service.invalidate_flag_cache(key)
    (flag,) = await ff_service.attach_last_change(db, [flag])
    return flag


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feature_flag(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Hard-delete a feature flag. ADMIN only."""
    await ff_service.delete_flag(db, key=key, actor=current_user)
    await db.commit()
    await ff_service.invalidate_flag_cache(key)
    return None


@router.get("/{key}/status")
async def get_feature_flag_status(
    key: str,
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Check whether this flag is enabled for the current caller.

    Any authenticated user can call this so frontend code can
    conditionally render UI without needing ADMIN access. Returns
    ``{key, enabled}`` only — no flag configuration detail is leaked.
    """
    parsed_project = None
    if project_id:
        parsed_project, _ = await resolve_project_scope(
            db,
            current_user,
            project_id,
        )

    enabled = await ff_service.is_enabled(
        key,
        db=db,
        project_id=parsed_project,
        user=current_user,
    )
    return {"key": key, "enabled": enabled}
