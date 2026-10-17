"""Notification preference management and history endpoints."""
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    require_project_access,
    require_role,
)
from app.db.postgres import get_db
from app.models.postgres import User, UserRole
from app.models.schemas import (
    TRANSITION_EVENT_VALUES,
    NotificationLogResponse,
    NotificationPreferenceCreate,
    NotificationPreferenceResponse,
    NotificationTransitionPolicyResponse,
    NotificationTransitionPolicyUpdate,
    TestNotificationRequest,
)
from app.services.activity.service import ActorRef, record as record_activity
from app.services.notification import gate_notifications
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

#: The policy's four fields and the words the activity feed uses for each, in
#: the order a ``changed`` summary names them (W09 NTF-02).
_POLICY_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("per_run_events_enabled", "every run"),
    ("transitions_enabled", "state changes"),
    ("enabled_events", "state-change events"),
    ("consecutive_failure_threshold", "failure threshold"),
)
#: ``changed`` for a first save that changes no value (it creates the row).
_POLICY_DEFAULTS_SAVED = "saved the defaults as this project's policy"


def _policy_values(source: Any) -> dict[str, Any]:
    """The four policy fields of a row or an ``EffectivePolicy``, as plain values.

    ``enabled_events`` is a list in ``TRANSITION_EVENT_VALUES`` order, so two
    policies holding the same events compare equal whatever order they were
    stored in. A fresh dict: a snapshot taken before an upsert stays as it was.
    """
    chosen = set(getattr(source, "enabled_events", None) or ())
    return {
        "per_run_events_enabled": bool(source.per_run_events_enabled),
        "transitions_enabled": bool(source.transitions_enabled),
        "enabled_events": [event for event in TRANSITION_EVENT_VALUES if event in chosen],
        "consecutive_failure_threshold": int(source.consecutive_failure_threshold),
    }


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
    pref = await upsert_notification_preference(db, payload, current_user)
    await db.commit()
    await db.refresh(pref)
    return pref


@router.put("/preferences/{pref_id}", response_model=NotificationPreferenceResponse)
async def update_preference(
    pref_id: uuid.UUID,
    payload: NotificationPreferenceCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    pref = await update_notification_preference(db, pref_id, payload, current_user)
    await db.commit()
    await db.refresh(pref)
    return pref


@router.delete("/preferences/{pref_id}", status_code=204)
async def delete_preference(
    pref_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await delete_notification_preference(db, pref_id, current_user)
    await db.commit()


@router.get("/history", response_model=list[NotificationLogResponse])
async def list_history(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await list_notification_history(db, current_user, unread_only=unread_only, limit=limit)
    # W09 NTF-01: ``link`` is one of the two allow-listed in-app paths a
    # notification may open (``gate_notifications.APP_PATH_RE``), else None.
    return [
        NotificationLogResponse.model_validate(row).model_copy(
            update={"link": gate_notifications.app_link(getattr(row, "delivery_metadata", None))}
        )
        for row in rows
    ]


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
    await db.commit()


@router.post("/history/read-all", status_code=204)
async def mark_all_read(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await mark_all_notifications_read(db, current_user)
    await db.commit()


# ── Per-project transition policy (PMF US-7.1) ────────────────────────────

@router.get(
    "/projects/{project_id}/transition-policy",
    response_model=NotificationTransitionPolicyResponse,
)
async def get_transition_policy(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    _: User = Depends(require_project_access()),
):
    """The project's transition-notification policy. A missing row means
    the new-project defaults apply (transitions ON, per-run spam OFF)."""
    from app.services.notification_transitions import (
        get_effective_policy,
        get_policy_row,
    )

    row = await get_policy_row(db, project_id)
    if row is not None:
        return NotificationTransitionPolicyResponse(
            project_id=project_id,
            transitions_enabled=row.transitions_enabled,
            per_run_events_enabled=row.per_run_events_enabled,
            enabled_events=list(row.enabled_events or []),
            consecutive_failure_threshold=row.consecutive_failure_threshold,
            is_default=False,
        )
    policy = await get_effective_policy(db, project_id)
    return NotificationTransitionPolicyResponse(
        project_id=project_id,
        transitions_enabled=policy.transitions_enabled,
        per_run_events_enabled=policy.per_run_events_enabled,
        enabled_events=list(policy.enabled_events),
        consecutive_failure_threshold=policy.consecutive_failure_threshold,
        is_default=True,
    )


@router.put(
    "/projects/{project_id}/transition-policy",
    response_model=NotificationTransitionPolicyResponse,
)
async def update_transition_policy(
    project_id: uuid.UUID,
    payload: NotificationTransitionPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Create or replace the project's transition-notification policy.
    QA_LEAD+.

    W09 NTF-02: records ``notification_policy.updated`` (outcome mode, the same
    transaction as the upsert) when a field changed or the PUT created the
    project's row; a PUT that changes nothing on an existing row records nothing.
    """
    from app.services.notification_transitions import (
        get_effective_policy,
        get_policy_row,
        upsert_policy,
    )

    existing = await get_policy_row(db, project_id)
    created = existing is None
    # The stored values, or the effective defaults when there is no row yet.
    before_all = _policy_values(
        existing if existing is not None else await get_effective_policy(db, project_id)
    )
    row = await upsert_policy(
        db,
        project_id,
        transitions_enabled=payload.transitions_enabled,
        per_run_events_enabled=payload.per_run_events_enabled,
        enabled_events=payload.enabled_events,
        consecutive_failure_threshold=payload.consecutive_failure_threshold,
    )
    after_all = _policy_values(row)
    changed_fields = [
        field for field, _label in _POLICY_FIELD_LABELS if before_all[field] != after_all[field]
    ]
    if changed_fields or created:
        changed = ", ".join(
            label for field, label in _POLICY_FIELD_LABELS if field in changed_fields
        ) or _POLICY_DEFAULTS_SAVED
        await record_activity(
            db,
            project_id=project_id,
            event_type="notification_policy.updated",
            actor=ActorRef.from_user(current_user),
            entity_id=project_id,
            entity_label="Notification policy",
            context={"changed": changed},
            before={field: before_all[field] for field in changed_fields},
            after={field: after_all[field] for field in changed_fields},
            changed_fields=changed_fields,
        )
    await db.commit()
    await db.refresh(row)
    return NotificationTransitionPolicyResponse(
        project_id=project_id,
        transitions_enabled=row.transitions_enabled,
        per_run_events_enabled=row.per_run_events_enabled,
        enabled_events=list(row.enabled_events or []),
        consecutive_failure_threshold=row.consecutive_failure_threshold,
        is_default=False,
    )


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
