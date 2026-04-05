"""Identity events router — audit trail for SSO, SCIM, and identity lifecycle."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/identity", tags=["Identity & Audit"])


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
