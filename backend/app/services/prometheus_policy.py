"""Shared egress policy for every Prometheus integration."""
from typing import Optional

from app.core.config import settings


def prometheus_offline_reason() -> Optional[str]:
    """Return the live offline block reason, or ``None`` when lookups are allowed."""
    return "offline_mode" if settings.AI_OFFLINE_MODE else None
