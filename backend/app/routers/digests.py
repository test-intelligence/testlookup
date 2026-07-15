"""Digest Subscriptions router — scheduled digest CRUD and preview (ENT-05)."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import DigestSubscription, User
from app.models.schemas import (
    DigestContentResponse,
    DigestSubscriptionCreate,
    DigestSubscriptionResponse,
    DigestSubscriptionUpdate,
)

logger = logging.getLogger("routers.digests")

router = APIRouter(prefix="/api/v1/digests", tags=["Digests"])


@router.get("/subscriptions", response_model=list[DigestSubscriptionResponse])
async def list_subscriptions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List current user's digest subscriptions."""
    result = await db.execute(
        select(DigestSubscription)
        .where(DigestSubscription.user_id == current_user.id)
        .order_by(DigestSubscription.created_at.desc())
    )
    return result.scalars().all()


@router.post("/subscriptions", response_model=DigestSubscriptionResponse, status_code=201)
async def create_subscription(
    payload: DigestSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new digest subscription."""
    now = datetime.now(timezone.utc)
    delta = timedelta(days=1) if payload.schedule == "DAILY" else timedelta(weeks=1)

    sub = DigestSubscription(
        user_id=current_user.id,
        project_id=payload.project_id,
        saved_view_id=payload.saved_view_id,
        name=payload.name,
        schedule=payload.schedule,
        channel=payload.channel,
        send_when_unchanged=payload.send_when_unchanged,
        # US-7.5: attached HTML analysis report (email digests only).
        report_attachment=payload.report_attachment,
        next_delivery_at=now + delta,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    logger.info("Digest subscription created: %s (%s) by %s", sub.name, sub.schedule, current_user.username)
    return sub


@router.get("/subscriptions/{sub_id}", response_model=DigestSubscriptionResponse)
async def get_subscription(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get a single subscription."""
    result = await db.execute(
        select(DigestSubscription).where(
            DigestSubscription.id == sub_id,
            DigestSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    return sub


@router.patch("/subscriptions/{sub_id}", response_model=DigestSubscriptionResponse)
async def update_subscription(
    sub_id: uuid.UUID,
    payload: DigestSubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update a digest subscription (own subscriptions only)."""
    result = await db.execute(
        select(DigestSubscription).where(
            DigestSubscription.id == sub_id,
            DigestSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    update_data = payload.model_dump(exclude_unset=True)

    # Recalculate next_delivery_at if schedule changes
    if "schedule" in update_data:
        delta = timedelta(days=1) if update_data["schedule"] == "DAILY" else timedelta(weeks=1)
        sub.next_delivery_at = datetime.now(timezone.utc) + delta

    for field, value in update_data.items():
        setattr(sub, field, value)

    await db.commit()
    await db.refresh(sub)
    return sub


@router.delete("/subscriptions/{sub_id}", status_code=204)
async def delete_subscription(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete (unsubscribe from) a digest subscription."""
    result = await db.execute(
        select(DigestSubscription).where(
            DigestSubscription.id == sub_id,
            DigestSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    await db.delete(sub)
    await db.commit()
    return None


@router.post("/subscriptions/{sub_id}/pause", response_model=DigestSubscriptionResponse)
async def pause_subscription(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Pause digest delivery without deleting."""
    result = await db.execute(
        select(DigestSubscription).where(
            DigestSubscription.id == sub_id,
            DigestSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    sub.is_paused = True
    await db.commit()
    await db.refresh(sub)
    return sub


@router.post("/subscriptions/{sub_id}/resume", response_model=DigestSubscriptionResponse)
async def resume_subscription(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Resume a paused subscription."""
    result = await db.execute(
        select(DigestSubscription).where(
            DigestSubscription.id == sub_id,
            DigestSubscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    sub.is_paused = False
    delta = timedelta(days=1) if sub.schedule == "DAILY" else timedelta(weeks=1)
    sub.next_delivery_at = datetime.now(timezone.utc) + delta
    await db.commit()
    await db.refresh(sub)
    return sub


@router.get("/preview", response_model=DigestContentResponse)
async def preview_digest(
    project_id: uuid.UUID | None = None,
    period: str = "weekly",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Preview digest content without sending."""
    if not project_id:
        from app.core.deps import get_accessible_project_ids
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            from datetime import datetime, timezone
            return DigestContentResponse(period=period, generated_at=datetime.now(timezone.utc).isoformat())
    from app.services.digest_content_service import generate_digest

    digest = await generate_digest(db, project_id, period)
    return DigestContentResponse(**digest)
