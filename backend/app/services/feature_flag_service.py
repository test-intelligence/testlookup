"""
Legacy feature flag facade — kept for backward compatibility with the
``app_settings`` router. New code should use ``services/feature_flags`` which
supports per-project, per-role, and percentage rollout gating.

The Tier 0A rewrite collapsed the ``FeatureFlag`` model onto a single
(key, enabled_global, enabled_projects, enabled_roles, rollout_percent)
schema. This module bridges the legacy call sites which only cared about
the global on/off toggle.

Legacy semantics preserved:
  - ``is_enabled`` is fail-open (unknown flags default to enabled).
  - ``set_flag`` / ``delete_flag`` / ``get_all_flags`` operate on the global
    toggle only. ``scope`` and ``config`` args are accepted but ignored —
    callers that need scoped gating must migrate to ``services/feature_flags``.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import FeatureFlag

logger = logging.getLogger("services.feature_flags")

# Well-known flag keys
SECURE_SETTINGS = "secure_settings"
ONBOARDING_WIZARD = "onboarding_wizard"
DEMO_MODE = "demo_mode"
EVIDENCE_PROVENANCE = "evidence_provenance"
SNAPSHOT_CACHING = "snapshot_caching"
CLUSTER_RANKING = "cluster_ranking"
ACCESS_AUDIT = "access_audit"


async def is_enabled(
    db: AsyncSession,
    flag_key: str,
    default: bool = True,
) -> bool:
    """Fail-open global-toggle check. Unknown flags return ``default``."""
    try:
        result = await db.execute(
            select(FeatureFlag.enabled_global).where(FeatureFlag.key == flag_key)
        )
        value = result.scalar_one_or_none()
        if value is None:
            return default
        return bool(value)
    except Exception as exc:
        logger.warning("Failed to check flag %s, using default %s: %s", flag_key, default, exc)
        return default


async def get_all_flags(db: AsyncSession) -> list[dict]:
    """Return all feature flags for the admin UI."""
    try:
        result = await db.execute(
            select(FeatureFlag).order_by(FeatureFlag.key)
        )
        return [
            {
                "flag_key": f.key,
                "scope": "global",
                "enabled": f.enabled_global,
                "config": None,
                "description": f.description,
                "updated_at": f.updated_at.isoformat() if f.updated_at else None,
            }
            for f in result.scalars().all()
        ]
    except Exception as exc:
        logger.warning("Failed to list flags: %s", exc)
        return []


async def set_flag(
    db: AsyncSession,
    flag_key: str,
    enabled: bool,
    scope: str = "global",  # accepted for compat, ignored
    config: Optional[dict] = None,  # accepted for compat, ignored
    description: Optional[str] = None,
) -> dict:
    """Create or toggle the global on/off state of a flag. Handler commits."""
    result = await db.execute(
        select(FeatureFlag).where(FeatureFlag.key == flag_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.enabled_global = enabled
        if description is not None:
            existing.description = description
    else:
        db.add(FeatureFlag(
            key=flag_key,
            enabled_global=enabled,
            description=description,
            rollout_percent=100,
        ))

    return {"flag_key": flag_key, "enabled": enabled, "scope": "global"}


async def delete_flag(db: AsyncSession, flag_key: str) -> bool:
    """Stage deletion of a feature flag. Returns True if a row was found."""
    result = await db.execute(
        select(FeatureFlag).where(FeatureFlag.key == flag_key)
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        return True
    return False
