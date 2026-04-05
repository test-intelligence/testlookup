from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import NotificationLog, NotificationPreference, User
from app.models.schemas import NotificationPreferenceCreate


async def get_preference_or_404(db: AsyncSession, pref_id: uuid.UUID, user_id: uuid.UUID) -> NotificationPreference:
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.id == pref_id,
            NotificationPreference.user_id == user_id,
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        raise HTTPException(status_code=404, detail="Preference not found")
    return pref


async def list_preferences(db: AsyncSession, current_user: User):
    result = await db.execute(
        select(NotificationPreference)
        .where(NotificationPreference.user_id == current_user.id)
        .order_by(NotificationPreference.channel, NotificationPreference.project_id)
    )
    return result.scalars().all()


async def upsert_preference(
    db: AsyncSession,
    payload: NotificationPreferenceCreate,
    current_user: User,
) -> NotificationPreference:
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == current_user.id,
            NotificationPreference.project_id == payload.project_id,
            NotificationPreference.channel == payload.channel,
        )
    )
    pref = result.scalar_one_or_none()

    values = payload.model_dump()
    if pref:
        pref.enabled = values["enabled"]
        pref.events = values["events"]
        pref.failure_rate_threshold = values["failure_rate_threshold"]
        pref.email_override = values["email_override"]
        pref.slack_webhook_url = values["slack_webhook_url"]
        pref.teams_webhook_url = values["teams_webhook_url"]
    else:
        pref = NotificationPreference(
            user_id=current_user.id,
            project_id=payload.project_id,
            channel=payload.channel,
            enabled=values["enabled"],
            events=values["events"],
            failure_rate_threshold=values["failure_rate_threshold"],
            email_override=values["email_override"],
            slack_webhook_url=values["slack_webhook_url"],
            teams_webhook_url=values["teams_webhook_url"],
        )
        db.add(pref)

    await db.commit()
    await db.refresh(pref)
    return pref


async def update_preference(
    db: AsyncSession,
    pref_id: uuid.UUID,
    payload: NotificationPreferenceCreate,
    current_user: User,
) -> NotificationPreference:
    pref = await get_preference_or_404(db, pref_id, current_user.id)
    values = payload.model_dump()
    pref.enabled = values["enabled"]
    pref.events = values["events"]
    pref.failure_rate_threshold = values["failure_rate_threshold"]
    pref.email_override = values["email_override"]
    pref.slack_webhook_url = values["slack_webhook_url"]
    pref.teams_webhook_url = values["teams_webhook_url"]
    await db.commit()
    await db.refresh(pref)
    return pref


async def delete_preference(db: AsyncSession, pref_id: uuid.UUID, current_user: User) -> None:
    pref = await get_preference_or_404(db, pref_id, current_user.id)
    await db.delete(pref)
    await db.commit()


async def list_notification_history(
    db: AsyncSession,
    current_user: User,
    unread_only: bool = False,
    limit: int = 50,
):
    query = (
        select(NotificationLog)
        .where(NotificationLog.user_id == current_user.id)
        .order_by(NotificationLog.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        query = query.where(NotificationLog.is_read.is_(False))
    return (await db.execute(query)).scalars().all()


async def unread_notification_count(db: AsyncSession, current_user: User) -> int:
    result = await db.execute(
        select(func.count(NotificationLog.id)).where(
            NotificationLog.user_id == current_user.id,
            NotificationLog.is_read.is_(False),
        )
    )
    return result.scalar() or 0


async def mark_notification_read(db: AsyncSession, log_id: uuid.UUID, current_user: User) -> None:
    await db.execute(
        update(NotificationLog)
        .where(
            NotificationLog.id == log_id,
            NotificationLog.user_id == current_user.id,
        )
        .values(is_read=True)
    )
    await db.commit()


async def mark_all_notifications_read(db: AsyncSession, current_user: User) -> None:
    await db.execute(
        update(NotificationLog)
        .where(
            NotificationLog.user_id == current_user.id,
            NotificationLog.is_read.is_(False),
        )
        .values(is_read=True)
    )
    await db.commit()


async def resolve_notification_overrides(
    db: AsyncSession,
    current_user: User,
    preference_id: uuid.UUID | None,
):
    if not preference_id:
        return None, None, None

    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.id == preference_id,
            NotificationPreference.user_id == current_user.id,
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        return None, None, None
    return pref.email_override, pref.slack_webhook_url, pref.teams_webhook_url
