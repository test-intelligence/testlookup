"""Identity events router — audit trail for SSO, SCIM, and identity lifecycle."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import (
    IdentityEvent,
    IdentityEventType,
    SSOConfiguration,
    User,
    UserRole,
)
from app.models.schemas import (
    IdentityEventListResponse,
    IdentityEventResponse,
    IdentitySyncStatus,
)
from app.services.sso_service import get_sync_status
from app.models.identity_retention import (
    IdentityRetentionApproval,
    IdentityRetentionHold,
    IdentityRetentionHoldCreate,
    IdentityRetentionHoldRelease,
    IdentityRetentionPreview,
    IdentityRetentionPurge,
    IdentityRetentionPurgeResult,
    IdentityRetentionRead,
    IdentityRetentionReason,
)
from app.services import identity_retention_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/identity", tags=["Identity & Audit"])


def _retention_error(exc: identity_retention_service.IdentityRetentionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})


@router.get("/retention/policy", response_model=IdentityRetentionRead)
async def read_identity_retention_policy(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Read the separate, default-off instance identity policy and its holds."""
    try:
        return await identity_retention_service.read_policy(db, now=datetime.now(timezone.utc))
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.get("/retention/preview", response_model=IdentityRetentionPreview)
async def preview_identity_retention(
    retention_days: Optional[int] = Query(default=None, ge=365, le=3650),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Count aged record classes without reading event content or deleting rows."""
    try:
        return await identity_retention_service.preview(
            db, now=datetime.now(timezone.utc), retention_days=retention_days,
        )
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.post("/retention/policy", response_model=IdentityRetentionRead)
async def approve_identity_retention_policy(
    payload: IdentityRetentionApproval,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Approve routine-event expiry with a fresh preview and typed confirmation.

    Requires an interactive instance administrator. Existing project retention
    enablement never authorizes this separate policy.
    """
    try:
        await identity_retention_service.approve_policy(
            db, actor=current_user, **payload.model_dump(), now=datetime.now(timezone.utc),
        )
        await db.commit()
        return await identity_retention_service.read_policy(db, now=datetime.now(timezone.utc))
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.post("/retention/disable", response_model=IdentityRetentionRead)
async def disable_identity_retention_policy(
    payload: IdentityRetentionReason,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Disable future identity expiry without releasing any hold."""
    try:
        await identity_retention_service.disable_policy(db, actor=current_user, reason=payload.reason)
        await db.commit()
        return await identity_retention_service.read_policy(db, now=datetime.now(timezone.utc))
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.post("/retention/holds", response_model=IdentityRetentionHold, status_code=201)
async def place_identity_retention_hold(
    payload: IdentityRetentionHoldCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Place an independent instance identity hold; all live holds block expiry."""
    try:
        hold = await identity_retention_service.place_hold(
            db, actor=current_user, **payload.model_dump(), now=datetime.now(timezone.utc),
        )
        await db.commit()
        return hold
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.post("/retention/holds/{hold_id}/release", response_model=IdentityRetentionRead)
async def release_identity_retention_hold(
    hold_id: uuid.UUID,
    payload: IdentityRetentionHoldRelease,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Release only this hold with a reason; overlapping holds remain absolute."""
    try:
        await identity_retention_service.release_hold(
            db, actor=current_user, hold_id=hold_id, **payload.model_dump(), now=datetime.now(timezone.utc),
        )
        await db.commit()
        return await identity_retention_service.read_policy(db, now=datetime.now(timezone.utc))
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.post("/retention/purge", response_model=IdentityRetentionPurgeResult)
async def purge_identity_retention_batch(
    payload: IdentityRetentionPurge,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Execute one approved, unheld batch of at most 500 routine events."""
    from app.services.retention_service import purge_identity_events_batch

    try:
        result = await purge_identity_events_batch(
            db, actor=current_user, **payload.model_dump(), now=datetime.now(timezone.utc),
        )
        await db.commit()
        return result
    except identity_retention_service.IdentityRetentionError as exc:
        raise _retention_error(exc) from exc


@router.get("/events", response_model=IdentityEventListResponse)
async def list_identity_events(
    event_type: Optional[IdentityEventType] = None,
    user_id: Optional[uuid.UUID] = None,
    sso_config_id: Optional[uuid.UUID] = None,
    success: Optional[bool] = None,
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """List identity events with filtering (ADMIN only)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(IdentityEvent).where(IdentityEvent.created_at >= cutoff)

    if event_type:
        query = query.where(IdentityEvent.event_type == event_type)
    if user_id:
        query = query.where(IdentityEvent.user_id == user_id)
    if sso_config_id:
        query = query.where(IdentityEvent.sso_config_id == sso_config_id)
    if success is not None:
        query = query.where(IdentityEvent.success == success)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(IdentityEvent.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    events = result.scalars().all()

    return IdentityEventListResponse(
        total=total,
        items=[IdentityEventResponse.model_validate(e) for e in events],
    )


@router.get("/sync-status", response_model=IdentitySyncStatus)
async def get_identity_sync_status(
    sso_config_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated identity sync health (ADMIN only)."""
    status_data = await get_sync_status(db, sso_config_id)

    # Get SSO config display name
    sso_display_name = None
    if sso_config_id:
        cfg_result = await db.execute(
            select(SSOConfiguration.display_name).where(SSOConfiguration.id == sso_config_id)
        )
        sso_display_name = cfg_result.scalar_one_or_none()

    # Get recent events
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    events_query = (
        select(IdentityEvent)
        .where(IdentityEvent.created_at >= cutoff)
        .order_by(IdentityEvent.created_at.desc())
        .limit(10)
    )
    if sso_config_id:
        events_query = events_query.where(IdentityEvent.sso_config_id == sso_config_id)
    events_result = await db.execute(events_query)
    recent_events = [
        IdentityEventResponse.model_validate(e)
        for e in events_result.scalars().all()
    ]

    return IdentitySyncStatus(
        sso_config_id=sso_config_id,
        sso_display_name=sso_display_name,
        total_federated_users=status_data["total_federated_users"],
        last_sso_login_at=status_data["last_sso_login_at"],
        last_scim_sync_at=status_data["last_scim_sync_at"],
        recent_failures=status_data["recent_failures"],
        recent_events=recent_events,
    )
