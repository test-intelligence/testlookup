"""
Feature flag service — Tier 0A.

Replaces the hand-rolled ``KNOWLEDGE_RAG_ENABLED`` Redis→DB→env fallback with
a generic capability-toggle store that supports:

- Global kill switch (``enabled_global``).
- Project allow-list (``enabled_projects``).
- Role allow-list (``enabled_roles``).
- Deterministic percentage rollout (``rollout_percent``) hashed by user_id
  or project_id so the same caller gets the same answer every time.

Resolution order when ``is_enabled`` is called:

1. **In-process cache** — 30s TTL, avoids touching Redis on every request
   in a tight loop.
2. **Redis cache** — 30s TTL, shared across workers so an ops toggle
   propagates within half a minute.
3. **Postgres row** — authoritative source.
4. **Legacy env var fallback** — consulted only for flags whose key appears
   in ``LEGACY_ENV_VAR_MAP``. This is a one-release compatibility shim so
   the migration of ``KNOWLEDGE_RAG_ENABLED`` doesn't need a big-bang flip.

All create/update/delete operations write a ``SettingsAuditLog`` row so
enterprise customers have a full flag change history.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import FeatureFlag, User

logger = structlog.get_logger("services.feature_flags")

# Legacy env vars consulted during the one-release cutover from hand-rolled
# RAG flag to the new service. Remove the entry once the knowledge_rag row
# is universally provisioned in production.
LEGACY_ENV_VAR_MAP: dict[str, str] = {
    "knowledge_rag": "KNOWLEDGE_RAG_ENABLED",
    "contract_validation": "AIQ_CONTRACT_VALIDATION_ENABLED",
    "change_ownership": "AIQ_CHANGE_OWNERSHIP_ENABLED",
    "async_decision_report_supersession": "AIQ_ASYNC_DECISION_REPORT_SUPERSESSION_ENABLED",
}

_IN_PROCESS_TTL_SECONDS = 30
_REDIS_KEY_PREFIX = "feature_flag:"
_REDIS_TTL_SECONDS = 30

# In-process cache: key → (expires_at_monotonic, flag_row_dict | None)
_cache: dict[str, tuple[float, Optional[dict]]] = {}


def _normalize_role(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role or "").strip()


def _serialize_flag(flag: FeatureFlag) -> dict[str, Any]:
    """Convert an ORM row to a plain dict suitable for caching."""
    return {
        "id": str(flag.id),
        "key": flag.key,
        "description": flag.description,
        "enabled_global": bool(flag.enabled_global),
        "enabled_projects": list(flag.enabled_projects or []),
        "enabled_roles": list(flag.enabled_roles or []),
        "rollout_percent": int(flag.rollout_percent or 0),
    }


def _deserialize_flag(payload: dict[str, Any]) -> dict[str, Any]:
    """Lenient dict normalization — handles values from Redis/cache."""
    return {
        "id": payload.get("id"),
        "key": payload.get("key"),
        "description": payload.get("description"),
        "enabled_global": bool(payload.get("enabled_global", False)),
        "enabled_projects": list(payload.get("enabled_projects") or []),
        "enabled_roles": list(payload.get("enabled_roles") or []),
        "rollout_percent": int(payload.get("rollout_percent") or 0),
    }


async def _load_from_redis(key: str) -> Optional[dict[str, Any]]:
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        raw = await redis.get(f"{_REDIS_KEY_PREFIX}{key}")
        if raw is None:
            return None
        # Distinguish "cached None" from "not cached".
        if raw == "__none__":
            return {"__none__": True}
        return _deserialize_flag(json.loads(raw))
    except Exception as exc:
        logger.debug("feature_flag redis read failed", key=key, error=str(exc))
        return None


async def _store_in_redis(key: str, flag_dict: Optional[dict[str, Any]]) -> None:
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        value = json.dumps(flag_dict) if flag_dict else "__none__"
        await redis.set(f"{_REDIS_KEY_PREFIX}{key}", value, ex=_REDIS_TTL_SECONDS)
    except Exception as exc:
        logger.debug("feature_flag redis write failed", key=key, error=str(exc))


async def _load_from_db(db: AsyncSession, key: str) -> Optional[dict[str, Any]]:
    result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    row = result.scalar_one_or_none()
    return _serialize_flag(row) if row else None


def _legacy_env_fallback(key: str) -> Optional[bool]:
    env_var = LEGACY_ENV_VAR_MAP.get(key)
    if not env_var:
        return None
    import os
    raw = os.getenv(env_var)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _rollout_bucket(key: str, bucket_input: str) -> int:
    """Deterministic 0-99 bucket for rollout_percent gating."""
    digest = hashlib.sha1(f"{key}:{bucket_input}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def _evaluate(
    flag: dict[str, Any],
    *,
    project_id: Optional[uuid.UUID],
    user: Optional[User],
) -> bool:
    """Pure evaluation — no I/O. Returns the gated enabled state."""
    if not flag.get("enabled_global"):
        return False

    # Project allow-list
    enabled_projects = flag.get("enabled_projects") or []
    if enabled_projects:
        if project_id is None or str(project_id) not in {str(p) for p in enabled_projects}:
            return False

    # Role allow-list
    enabled_roles = flag.get("enabled_roles") or []
    if enabled_roles:
        if user is None:
            return False
        user_role = _normalize_role(user.role)
        if user_role not in {str(r) for r in enabled_roles}:
            return False

    # Rollout percentage — deterministic by user or project to avoid
    # flap-flopping between requests.
    rollout = int(flag.get("rollout_percent", 100) or 0)
    if rollout >= 100:
        return True
    if rollout <= 0:
        return False
    bucket_input = str(user.id) if user is not None else (str(project_id) if project_id else "global")
    return _rollout_bucket(flag["key"], bucket_input) < rollout


async def is_enabled(
    key: str,
    *,
    db: Optional[AsyncSession] = None,
    project_id: Optional[uuid.UUID] = None,
    user: Optional[User] = None,
) -> bool:
    """Resolve a feature flag — the single public entry point.

    Args:
        key: The flag key (snake_case, e.g. ``"cypress_ingest"``).
        db: Optional session. If omitted, opens a short-lived session.
        project_id: Current request's project scope, if any.
        user: Current user, if any.
    """
    now = time.monotonic()

    # 1. In-process cache.
    cached = _cache.get(key)
    if cached and cached[0] > now:
        flag = cached[1]
        if flag is None:
            fallback = _legacy_env_fallback(key)
            return bool(fallback) if fallback is not None else False
        return _evaluate(flag, project_id=project_id, user=user)

    # 2. Redis cache.
    redis_flag = await _load_from_redis(key)
    if redis_flag is not None:
        if redis_flag.get("__none__"):
            _cache[key] = (now + _IN_PROCESS_TTL_SECONDS, None)
            fallback = _legacy_env_fallback(key)
            return bool(fallback) if fallback is not None else False
        _cache[key] = (now + _IN_PROCESS_TTL_SECONDS, redis_flag)
        return _evaluate(redis_flag, project_id=project_id, user=user)

    # 3. Postgres row.
    if db is None:
        from app.db.postgres import AsyncSessionLocal
        async with AsyncSessionLocal() as short_db:
            flag_dict = await _load_from_db(short_db, key)
    else:
        flag_dict = await _load_from_db(db, key)

    _cache[key] = (now + _IN_PROCESS_TTL_SECONDS, flag_dict)
    await _store_in_redis(key, flag_dict)

    if flag_dict is None:
        fallback = _legacy_env_fallback(key)
        if fallback is not None:
            logger.debug(
                "feature_flag legacy env fallback",
                key=key,
                env_var=LEGACY_ENV_VAR_MAP.get(key),
                value=fallback,
            )
            return fallback
        return False

    return _evaluate(flag_dict, project_id=project_id, user=user)


async def invalidate_flag_cache(key: str) -> None:
    """Drop the caches for one flag — **call this after the commit lands**.

    Mutation services only stage their database and audit writes. Their router
    callers commit first and invalidate second: invalidating mid-transaction
    lets a concurrent reader re-cache the old committed row for the full TTL.

    Measured on the live deployment right after this module's own toggle was
    wired up: ``PUT /settings/ai {knowledge_rag_enabled: true}`` returned 200,
    the flag row read ``true`` (that endpoint queries Postgres directly), and
    both ``GET /settings/ai`` and the gate — which go through ``is_enabled`` —
    still answered ``false``.
    """
    await _invalidate(key)


async def _invalidate(key: str) -> None:
    """Drop both in-process and Redis caches for a single key."""
    _cache.pop(key, None)
    try:
        from app.db.redis_client import get_redis
        redis = get_redis()
        await redis.delete(f"{_REDIS_KEY_PREFIX}{key}")
    except Exception as exc:
        logger.debug("feature_flag redis invalidate failed", key=key, error=str(exc))


async def list_flags(db: AsyncSession) -> list[FeatureFlag]:
    result = await db.execute(select(FeatureFlag).order_by(FeatureFlag.key))
    return list(result.scalars().all())


async def get_flag(db: AsyncSession, key: str) -> Optional[FeatureFlag]:
    result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    return result.scalar_one_or_none()


async def create_flag(
    db: AsyncSession,
    *,
    key: str,
    description: Optional[str],
    enabled_global: bool,
    enabled_projects: Optional[list[uuid.UUID]],
    enabled_roles: Optional[list[str]],
    rollout_percent: int,
    actor: User,
) -> FeatureFlag:
    existing = await get_flag(db, key)
    if existing is not None:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Feature flag '{key}' already exists",
        )

    flag = FeatureFlag(
        key=key,
        description=description,
        enabled_global=enabled_global,
        enabled_projects=[str(p) for p in enabled_projects] if enabled_projects else None,
        enabled_roles=enabled_roles,
        rollout_percent=rollout_percent,
        updated_by_user_id=actor.id,
    )
    db.add(flag)
    # flush populates id + server defaults (created_at, updated_at) so the
    # audit row below can reference them and the response serialization
    # below sees the fully-hydrated row. Router owns the commit.
    await db.flush()
    _write_audit_entry(db, actor, action="create", key=key, after=_serialize_flag(flag))
    return flag


async def update_flag(
    db: AsyncSession,
    *,
    key: str,
    updates: dict[str, Any],
    actor: User,
) -> FeatureFlag:
    from fastapi import HTTPException, status
    flag = await get_flag(db, key)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature flag '{key}' not found",
        )

    before = _serialize_flag(flag)
    if "description" in updates and updates["description"] is not None:
        flag.description = updates["description"]
    if "enabled_global" in updates and updates["enabled_global"] is not None:
        flag.enabled_global = bool(updates["enabled_global"])
    if "enabled_projects" in updates and updates["enabled_projects"] is not None:
        flag.enabled_projects = [str(p) for p in updates["enabled_projects"]] or None
    if "enabled_roles" in updates and updates["enabled_roles"] is not None:
        flag.enabled_roles = list(updates["enabled_roles"]) or None
    if "rollout_percent" in updates and updates["rollout_percent"] is not None:
        flag.rollout_percent = int(updates["rollout_percent"])
    flag.updated_by_user_id = actor.id
    flag.updated_at = datetime.now(timezone.utc)
    await db.flush()
    _write_audit_entry(
        db, actor, action="update", key=key, before=before, after=_serialize_flag(flag),
    )
    return flag


async def delete_flag(db: AsyncSession, *, key: str, actor: User) -> None:
    from fastapi import HTTPException, status
    flag = await get_flag(db, key)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Feature flag '{key}' not found",
        )
    before = _serialize_flag(flag)
    await db.delete(flag)
    await db.flush()
    _write_audit_entry(db, actor, action="delete", key=key, before=before)


def _write_audit_entry(
    db: AsyncSession,
    actor: User,
    *,
    action: str,
    key: str,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
) -> None:
    """Stage a SettingsAuditLog row in the caller's transaction.

    Stage-only: the row is added via ``db.add`` and lands when the router's
    unit-of-work commits. This guarantees the audit row is atomic with the
    primary mutation — either both land or neither does — without the
    service owning its own commit.

    Uses the existing ``settings_audit_log`` schema so enterprise customers
    can filter the audit dashboard on ``setting_key LIKE 'feature_flag:%'``
    to see the full history of flag changes.
    """
    try:
        from app.models.postgres import SettingsAuditLog
        # Changed fields: when updating, list which keys differed; when
        # creating/deleting, list all keys. ``SettingsAuditLog`` stores
        # field names only — we never persist the old/new values in this
        # table because other settings audits follow the same no-secrets
        # convention. The before/after payloads live in structlog only.
        if action == "update" and before is not None and after is not None:
            changed = sorted([k for k in set(before) | set(after) if before.get(k) != after.get(k)])
        elif after is not None:
            changed = sorted(after.keys())
        elif before is not None:
            changed = sorted(before.keys())
        else:
            changed = []

        entry = SettingsAuditLog(
            setting_key=f"feature_flag:{key}",
            action=action,
            actor_id=actor.id,
            actor_name=getattr(actor, "username", None) or getattr(actor, "email", None),
            changed_fields=changed,
        )
        db.add(entry)

        # Structlog receives the full before/after for forensic reconstruction.
        logger.info(
            "feature_flag_change",
            key=key,
            action=action,
            actor_id=str(actor.id),
            before=before,
            after=after,
        )
    except Exception as exc:
        logger.warning(
            "feature_flag audit log write failed",
            key=key,
            action=action,
            error=str(exc),
        )
