"""Notification preference management and history endpoints."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import User
from app.models.schemas import (
    NotificationLogResponse,
    NotificationPreferenceCreate,
    NotificationPreferenceResponse,
    TestNotificationRequest,
)
from app.services.notification.manager import send_test_notification
from app.services.notification_service import (
    delete_preference as delete_notification_preference,
    list_notification_history,
    list_preferences as list_notification_preferences,
    mark_all_notifications_read,
    mark_notification_read,
    resolve_notification_overrides,
    unread_notification_count,
    update_preference as update_notification_preference,
    upsert_preference as upsert_notification_preference,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get("/preferences", response_model=list[NotificationPreferenceResponse])
async def list_preferences(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_notification_preferences(db, current_user)


@router.post("/preferences", response_model=NotificationPreferenceResponse, status_code=status.HTTP_201_CREATED)
async def upsert_preference(
    payload: NotificationPreferenceCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return await upsert_notification_preference(db, payload, current_user)


@router.put("/preferences/{pref_id}", response_model=NotificationPreferenceResponse)
async def update_preference(
    pref_id: uuid.UUID,
    payload: NotificationPreferenceCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_notification_preference(db, pref_id, payload, current_user)


@router.delete("/preferences/{pref_id}", status_code=204)
async def delete_preference(
    pref_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await delete_notification_preference(db, pref_id, current_user)


@router.get("/history", response_model=list[NotificationLogResponse])
async def list_history(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_notification_history(db, current_user, unread_only=unread_only, limit=limit)


@router.get("/history/unread-count")
async def unread_count(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return {"unread": await unread_notification_count(db, current_user)}


@router.post("/history/{log_id}/read", status_code=204)
async def mark_read(
    log_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await mark_notification_read(db, log_id, current_user)


@router.post("/history/read-all", status_code=204)
async def mark_all_read(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await mark_all_notifications_read(db, current_user)


@router.post("/test")
async def send_test(
    payload: TestNotificationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    email_override, slack_webhook_url, teams_webhook_url = await resolve_notification_overrides(
        db, current_user, payload.preference_id
    )

    status_str, error = await send_test_notification(
        user_id=current_user.id,
        channel=payload.channel,
        email_override=email_override,
        slack_webhook_url=slack_webhook_url,
        teams_webhook_url=teams_webhook_url,
    )

    if status_str == "failed":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Test notification failed: {error}",
        )

    return {"status": "sent", "channel": payload.channel}
