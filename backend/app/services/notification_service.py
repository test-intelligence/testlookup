"""
Notification preference and history service.

Transaction model (item #2): mutation functions stage changes only; the
router handler owns ``db.commit()``. Read-only helpers are side-effect
free and never touch the transaction.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import NotificationLog, NotificationPreference, Project, User
from app.models.schemas import NotificationPreferenceCreate

#: What a project-bound API key is told when it reaches for a preference
#: outside its project, including an "all projects" (``project_id=None``) one.
PREFERENCE_BINDING_DETAIL = (
    "This API key is restricted to a different project; it can only manage "
    "notification preferences for its own project"
)


async def authorize_preference_project(
    db: AsyncSession,
    current_user: User,
    project_id: uuid.UUID | None,
) -> None:
    """403 unless the caller may subscribe to ``project_id``'s notifications (QA-R3-1).

    A preference is a subscription: a row for project P makes the notification
    manager send P's run and transition notifications to the row's
    ``email_override`` or personal webhook. Writing one for a project the
    caller cannot read was a cross-tenant read by another name: any user,
    and any key bound to another project, subscribed to P's "run failed"
    notifications at an address of its choosing.

    * a project-bound API key: only its own project. ``None`` ("all
      projects") is refused too, since it spans every project;
    * a named project: the caller's membership, resolved the canonical way
      (``resolve_project_scope``: ADMIN passes, a member passes, anyone else
      is 403), then 404 for a project that does not exist;
    * ``None`` for anyone else: allowed. It means "every project I can see",
      and the manager confines each delivery to projects the recipient can
      access at send time.
    """
    from app.core.deps import _enforce_api_key_project_binding, resolve_project_scope

    _enforce_api_key_project_binding(current_user, project_id, detail=PREFERENCE_BINDING_DETAIL)
    if project_id is None:
        return
    await resolve_project_scope(db, current_user, str(project_id))
    exists = (
        await db.execute(select(Project.id).where(Project.id == project_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Project not found")


def _bound_project(current_user: User) -> uuid.UUID | None:
    from app.core.deps import _api_key_bound_project

    return _api_key_bound_project(current_user)


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
    stmt = (
        select(NotificationPreference)
        .where(NotificationPreference.user_id == current_user.id)
        .order_by(NotificationPreference.channel, NotificationPreference.project_id)
    )
    bound = _bound_project(current_user)
    if bound is not None:
        # The owner's preferences carry personal webhook URLs (credentials in
        # themselves) and name the owner's other projects. A key bound to one
        # project sees only that project's rows.
        stmt = stmt.where(NotificationPreference.project_id == bound)
    result = await db.execute(stmt)
    return result.scalars().all()


async def upsert_preference(
    db: AsyncSession,
    payload: NotificationPreferenceCreate,
    current_user: User,
) -> NotificationPreference:
    await authorize_preference_project(db, current_user, payload.project_id)
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
        await db.flush()  # materialize pref.id for the handler response
    return pref


async def update_preference(
    db: AsyncSession,
    pref_id: uuid.UUID,
    payload: NotificationPreferenceCreate,
    current_user: User,
) -> NotificationPreference:
    pref = await get_preference_or_404(db, pref_id, current_user.id)
    # ``project_id`` is never rewritten here, so the row being written is the
    # STORED project's subscription: that is the one to authorize. A caller
    # who has since lost access must not re-arm it with a new address.
    await authorize_preference_project(db, current_user, pref.project_id)
    values = payload.model_dump()
    pref.enabled = values["enabled"]
    pref.events = values["events"]
    pref.failure_rate_threshold = values["failure_rate_threshold"]
    pref.email_override = values["email_override"]
    pref.slack_webhook_url = values["slack_webhook_url"]
    pref.teams_webhook_url = values["teams_webhook_url"]
    return pref


async def delete_preference(db: AsyncSession, pref_id: uuid.UUID, current_user: User) -> None:
    pref = await get_preference_or_404(db, pref_id, current_user.id)
    # Deleting one's own row needs no membership (an ex-member cleaning up a
    # stale subscription is fine), but a key bound to one project must not
    # silence its owner's other projects.
    from app.core.deps import _enforce_api_key_project_binding

    _enforce_api_key_project_binding(current_user, pref.project_id, detail=PREFERENCE_BINDING_DETAIL)
    await db.delete(pref)


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


async def mark_all_notifications_read(db: AsyncSession, current_user: User) -> None:
    await db.execute(
        update(NotificationLog)
        .where(
            NotificationLog.user_id == current_user.id,
            NotificationLog.is_read.is_(False),
        )
        .values(is_read=True)
    )


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
    bound = _bound_project(current_user)
    if bound is not None and pref.project_id != bound:
        # Same as a missing preference: a bound key does not learn, or use,
        # the owner's overrides for another project.
        return None, None, None
    return pref.email_override, pref.slack_webhook_url, pref.teams_webhook_url
