"""Default-off, instance-scoped routine identity retention and independent holds.

SCIM creation events remain live authorization evidence, not disposable logs.
The policy follows the existing MFA/SCIM setting pattern in app_settings.
Every mutation and purge locks that single row; holds serialize with deletion.
Released/expired hold history stays in the never-purged settings audit trail.
This module stages writes only. The caller owns commit/rollback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from pydantic import ValidationError
from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_retention import (
    AUTHORITY_EVENT_TYPES, MAX_HOLDS, RETENTION_MAX_DAYS, RETENTION_MIN_DAYS,
    ROUTINE_EVENT_TYPES, IdentityRetentionDocument, IdentityRetentionHold,
)
from app.models.postgres import AppSetting, IdentityEvent, SettingsAuditLog
from app.services.canonical_json import stable_json_sha256

POLICY_KEY = "identity_event_retention_policy"
PURGE_AUDIT_KEY = "identity_event_retention_purge"
RELEASE_CONFIRMATION = "release this identity retention hold"
PREVIEW_TTL = timedelta(minutes=10)
ERRORS = {
    "interactive_admin_required": "Identity retention changes require a signed-in instance administrator.",
    "policy_unreadable": "Identity retention policy is unreadable; nothing can be purged.",
    "invalid_days": "Choose an identity retention period between 365 and 3650 days.",
    "invalid_reason": "A reason of 3 to 500 characters is required.",
    "preview_stale": "Identity retention changed or the preview expired; preview again.",
    "confirmation_mismatch": "Type the confirmation exactly as shown.",
    "retention_disabled": "Routine identity retention is disabled.",
    "retention_held": "An instance identity hold prevents this purge.",
    "invalid_expiry": "Hold expiry must be in the future and at most 3650 days ahead.",
    "hold_not_found": "Identity retention hold not found.",
    "hold_limit": "The active identity hold limit has been reached; retention remains held.",
    "invalid_batch": "An identity retention batch must contain between 1 and 500 events.",
}


class IdentityRetentionError(Exception):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(ERRORS[code])


def aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("identity retention requires an aware UTC timestamp")
    return now.astimezone(timezone.utc)


def require_interactive_admin(actor) -> None:
    from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind

    role = getattr(actor, "role", None)
    if getattr(role, "value", role) != "ADMIN" or credential_kind(actor) != CREDENTIAL_KIND_JWT:
        raise IdentityRetentionError("interactive_admin_required", 403)


def clean_reason(reason: str) -> str:
    value = reason.strip()
    if not 3 <= len(value) <= 500:
        raise IdentityRetentionError("invalid_reason", 422)
    return value


def live_holds(policy: IdentityRetentionDocument, now: datetime) -> list[IdentityRetentionHold]:
    now = aware(now)
    return [hold for hold in policy.holds if hold.expires_at is None or hold.expires_at > now]


def document(row: AppSetting | None) -> IdentityRetentionDocument:
    if row is None:
        return IdentityRetentionDocument()
    if row.is_secret_backed or row.secret_ref_id is not None or not isinstance(row.value, dict):
        raise IdentityRetentionError("policy_unreadable")
    try:
        return IdentityRetentionDocument.model_validate(row.value)
    except ValidationError as exc:
        raise IdentityRetentionError("policy_unreadable") from exc


async def policy_row(db: AsyncSession, *, lock: bool = False, create: bool = False) -> AppSetting | None:
    statement = select(AppSetting).where(AppSetting.key == POLICY_KEY)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None and create:
        await db.execute(insert(AppSetting).values(
            key=POLICY_KEY, value=IdentityRetentionDocument().model_dump(mode="json"), is_secret_backed=False,
        ).on_conflict_do_nothing(index_elements=[AppSetting.key]))
        row = (await db.execute(statement)).scalar_one()
    return row


async def read_policy(db, *, now: datetime) -> dict:
    policy = document(await policy_row(db))
    return {**policy.model_dump(mode="json"), "active_hold_count": len(live_holds(policy, now))}


def enable_confirmation(days: int) -> str:
    return f"enable routine identity retention ({days} days)"


def purge_confirmation(days: int) -> str:
    return f"purge routine identity events older than {days} days"


def routine_event_clause():
    # A malformed/legacy failure recorded under a routine event name is still
    # security evidence. Positive success, not merely its type, is required.
    return and_(IdentityEvent.event_type.in_(ROUTINE_EVENT_TYPES), IdentityEvent.success.is_(True))


async def preview(db, *, now: datetime, retention_days: int | None = None, policy=None) -> dict:
    observed = aware(now)
    policy = policy if policy is not None else document(await policy_row(db))
    days = policy.retention_days if retention_days is None else retention_days
    if type(days) is not int or not RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS:
        raise IdentityRetentionError("invalid_days", 422)
    cutoff = observed - timedelta(days=days)
    record_class = case(
        (routine_event_clause(), "routine_authentication"),
        (IdentityEvent.event_type.in_(AUTHORITY_EVENT_TYPES), "scim_authority"),
        else_="protected_identity",
    )
    rows = (await db.execute(select(record_class, func.count()).where(
        IdentityEvent.created_at < cutoff,
    ).group_by(record_class))).all()
    counts = dict.fromkeys(("routine_authentication", "scim_authority", "protected_identity"), 0)
    counts.update({name: int(count) for name, count in rows})
    payload = {
        "observed_at": observed, "cutoff": cutoff, "retention_days": days,
        "enabled": policy.enabled, "active_hold_count": len(live_holds(policy, observed)), "counts": counts,
        "enable_confirmation": enable_confirmation(days), "purge_confirmation": purge_confirmation(days),
    }
    payload["preview_hash"] = stable_json_sha256({
        "observed_at": observed.isoformat(), "days": days, "counts": counts,
        "policy": policy.model_dump(mode="json"),
    })
    return payload


async def verify_preview(db, *, policy, observed_at, now, retention_days, preview_hash):
    observed, current = aware(observed_at), aware(now)
    if observed > current + timedelta(seconds=5) or current - observed > PREVIEW_TTL:
        raise IdentityRetentionError("preview_stale")
    snapshot = await preview(db, now=observed, retention_days=retention_days, policy=policy)
    if snapshot["preview_hash"] != preview_hash:
        raise IdentityRetentionError("preview_stale")
    return snapshot


def audit(db, *, actor, action, policy, reason, details=None, audit_id=None, setting_key=POLICY_KEY):
    row = SettingsAuditLog(
        id=audit_id or uuid.uuid4(), setting_key=setting_key, action=action,
        actor_id=getattr(actor, "id", None),
        actor_name=(getattr(actor, "full_name", None) or getattr(actor, "username", None)) if actor else None,
        reason=reason,
        changed_fields={"policy_revision": policy.revision, "record_class": "routine_authentication",
                        "reason": reason, **(details or {})},
    )
    db.add(row)
    return row


async def approve_policy(db, *, actor, retention_days, observed_at, preview_hash, confirmation, reason, now):
    require_interactive_admin(actor)
    reason, now = clean_reason(reason), aware(now)
    row = await policy_row(db, lock=True, create=True)
    policy = document(row)
    await verify_preview(db, policy=policy, observed_at=observed_at, now=now,
                         retention_days=retention_days, preview_hash=preview_hash)
    if confirmation != enable_confirmation(retention_days):
        raise IdentityRetentionError("confirmation_mismatch", 422)
    updated = policy.model_copy(update={
        "enabled": True, "retention_days": retention_days,
        "approved_at": now, "revision": policy.revision + 1,
    })
    row.value, row.updated_by = updated.model_dump(mode="json"), actor.id
    audit(db, actor=actor, action="enabled", policy=updated, reason=reason, details={
        "before": {"enabled": policy.enabled, "retention_days": policy.retention_days},
        "after": {"enabled": True, "retention_days": retention_days},
    })
    await db.flush()
    return updated


async def disable_policy(db, *, actor, reason):
    require_interactive_admin(actor)
    reason = clean_reason(reason)
    row = await policy_row(db, lock=True, create=True)
    policy = document(row)
    updated = policy.model_copy(update={"enabled": False, "revision": policy.revision + 1})
    row.value, row.updated_by = updated.model_dump(mode="json"), actor.id
    audit(db, actor=actor, action="disabled", policy=updated, reason=reason)
    await db.flush()
    return updated


async def place_hold(db, *, actor, reason, expires_at, now):
    require_interactive_admin(actor)
    reason, now = clean_reason(reason), aware(now)
    expires = None if expires_at is None else aware(expires_at)
    if expires is not None and not now < expires <= now + timedelta(days=3650):
        raise IdentityRetentionError("invalid_expiry", 422)
    row = await policy_row(db, lock=True, create=True)
    policy = document(row)
    active = live_holds(policy, now)
    if len(active) >= MAX_HOLDS:
        raise IdentityRetentionError("hold_limit")
    hold = IdentityRetentionHold(id=uuid.uuid4(), reason=reason, placed_at=now, expires_at=expires, audit_id=uuid.uuid4())
    updated = policy.model_copy(update={"holds": [*active, hold], "revision": policy.revision + 1})
    row.value, row.updated_by = updated.model_dump(mode="json"), actor.id
    audit(db, actor=actor, action="hold_placed", policy=updated, reason=reason,
          details={"hold": hold.model_dump(mode="json")}, audit_id=hold.audit_id)
    await db.flush()
    return hold


async def release_hold(db, *, actor, hold_id, reason, confirmation, now):
    require_interactive_admin(actor)
    reason, now = clean_reason(reason), aware(now)
    if confirmation != RELEASE_CONFIRMATION:
        raise IdentityRetentionError("confirmation_mismatch", 422)
    row = await policy_row(db, lock=True)
    policy = document(row)
    hold = next((item for item in policy.holds if item.id == hold_id), None)
    if hold is None:
        raise IdentityRetentionError("hold_not_found", 404)
    updated = policy.model_copy(update={
        "holds": [item for item in live_holds(policy, now) if item.id != hold_id], "revision": policy.revision + 1,
    })
    row.value, row.updated_by = updated.model_dump(mode="json"), actor.id
    audit(db, actor=actor, action="hold_released", policy=updated, reason=reason,
          details={"hold": hold.model_dump(mode="json")})
    await db.flush()
    return updated


async def run_scheduled_batch(*, now: datetime | None = None) -> dict:
    """One approved instance batch, only in the existing instance UTC window.

    Default-off is independent of project retention enablement. Never call
    this from an explicit project purge: identity events are instance scoped.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.services.retention_instance_service import get_instance_policy
    from app.services.retention_service import purge_identity_events_batch

    observed = aware(now or datetime.now(timezone.utc))
    async with AsyncSessionLocal() as db:
        instance = await get_instance_policy(db)
        hour = instance.purge_window_start_utc if instance.purge_window_start_utc is not None else 2
        if observed.hour != hour:
            return {"status": "outside_window", "deleted": 0}
        result = await purge_identity_events_batch(db, now=observed, scheduled=True)
        await db.commit()
    return result
