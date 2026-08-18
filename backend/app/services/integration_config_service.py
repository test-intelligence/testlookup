"""Authoritative runtime resolution for global integration credentials."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import AppSetting
from app.services.secret_service import read_secret

_SCOPE = "integrations_config"


async def resolve_global_notification_webhooks(
    db: AsyncSession,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve Slack/Teams global notification settings from one authority.

    Encrypted ``secret_refs`` values win, followed by legacy inline values and
    environment defaults. Callers that already loaded the AppSetting can pass
    ``overrides`` to avoid a duplicate query.
    """
    if overrides is None:
        result = await db.execute(select(AppSetting).where(AppSetting.key == _SCOPE))
        row = result.scalar_one_or_none()
        overrides = dict(row.value) if row is not None and row.value else {}

    slack_webhook = await read_secret(db, _SCOPE, "slack_webhook_url")
    teams_webhook = await read_secret(db, _SCOPE, "teams_webhook_url")
    return {
        "slack_enabled": overrides.get("slack_enabled", settings.SLACK_ENABLED),
        "slack_webhook_url": (
            slack_webhook or overrides.get("slack_webhook_url") or settings.SLACK_WEBHOOK_URL
        ),
        "teams_enabled": overrides.get("teams_enabled", settings.TEAMS_ENABLED),
        "teams_webhook_url": (
            teams_webhook or overrides.get("teams_webhook_url") or settings.TEAMS_WEBHOOK_URL
        ),
    }
