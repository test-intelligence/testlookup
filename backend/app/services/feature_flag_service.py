"""
Feature Flag Service — controlled rollout of new features.

Provides:
  - is_enabled: check if a flag is on (with optional scope filtering)
  - get_all_flags: list all flags for admin UI
  - set_flag: create or update a flag
  - Flag keys are simple strings like "secure_settings", "onboarding_wizard", "demo_mode"

Default behavior: unknown flags are treated as ENABLED (fail-open for safety).
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
    """
    Check if a feature flag is enabled.

    Default is True (fail-open) — unknown flags are treated as enabled.
    This means new code paths work immediately; flags are used to DISABLE features.
    """
    try:
        result = await db.execute(
            select(FeatureFlag.enabled).where(FeatureFlag.flag_key == flag_key)
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
            select(FeatureFlag).order_by(FeatureFlag.flag_key)
        )
        return [
            {
                "flag_key": f.flag_key,
                "scope": f.scope,
                "enabled": f.enabled,
                "config": f.config,
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
    scope: str = "global",
    config: Optional[dict] = None,
    description: Optional[str] = None,
) -> dict:
    """Create or update a feature flag."""
    result = await db.execute(
        select(FeatureFlag).where(FeatureFlag.flag_key == flag_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.enabled = enabled
        existing.scope = scope
        if config is not None:
            existing.config = config
        if description is not None:
            existing.description = description
    else:
        db.add(FeatureFlag(
            flag_key=flag_key,
            scope=scope,
            enabled=enabled,
            config=config,
            description=description,
        ))

    await db.commit()
    return {"flag_key": flag_key, "enabled": enabled, "scope": scope}


async def delete_flag(db: AsyncSession, flag_key: str) -> bool:
    """Delete a feature flag. Returns True if deleted."""
    result = await db.execute(
        select(FeatureFlag).where(FeatureFlag.flag_key == flag_key)
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()
        return True
    return False
