"""Authoritative runtime resolution for global integration credentials."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import AppSetting
from app.services.secret_service import read_secret

_SCOPE = "integrations_config"


async def resolve_global_integrations(
    db: AsyncSession,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the persisted global integration configuration used at runtime.

    The settings screen writes metadata to ``app_settings`` and credentials to
    ``secret_refs``.  Every health probe must resolve that same pair; otherwise
    an operator can successfully save a connector while the diagnostic keeps
    testing stale process-environment values.
    """
    if overrides is None:
        result = await db.execute(select(AppSetting).where(AppSetting.key == _SCOPE))
        row = result.scalar_one_or_none()
        overrides = dict(row.value) if row is not None and row.value else {}

    secret_defaults = {
        "jira_api_token": settings.JIRA_API_TOKEN,
        "splunk_api_token": settings.SPLUNK_API_TOKEN,
        "ocp_sa_token": settings.OCP_SA_TOKEN,
        "github_token": settings.GITHUB_TOKEN,
        "slack_webhook_url": settings.SLACK_WEBHOOK_URL,
        "teams_webhook_url": settings.TEAMS_WEBHOOK_URL,
    }
    secrets: dict[str, Any] = {}
    for key, default in secret_defaults.items():
        secrets[key] = await read_secret(db, _SCOPE, key) or overrides.get(key) or default

    return {
        "jira_enabled": overrides.get("jira_enabled", settings.JIRA_ENABLED),
        "jira_domain": overrides.get("jira_domain", settings.JIRA_DOMAIN),
        "jira_email": overrides.get("jira_email", settings.JIRA_EMAIL),
        "jira_default_project_key": overrides.get(
            "jira_default_project_key", settings.JIRA_DEFAULT_PROJECT_KEY
        ),
        "splunk_enabled": overrides.get("splunk_enabled", settings.SPLUNK_ENABLED),
        "splunk_base_url": overrides.get("splunk_base_url", settings.SPLUNK_BASE_URL),
        "ocp_enabled": overrides.get("ocp_enabled", settings.OCP_ENABLED),
        "ocp_api_url": overrides.get("ocp_api_url", settings.OCP_API_URL),
        "ocp_default_namespace": overrides.get(
            "ocp_default_namespace", settings.OCP_DEFAULT_NAMESPACE
        ),
        "slack_enabled": overrides.get("slack_enabled", settings.SLACK_ENABLED),
        "slack_default_channel": overrides.get(
            "slack_default_channel", settings.SLACK_DEFAULT_CHANNEL
        ),
        "teams_enabled": overrides.get("teams_enabled", settings.TEAMS_ENABLED),
        "github_repo": overrides.get("github_repo", settings.GITHUB_REPO),
        **secrets,
    }


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
    config = await resolve_global_integrations(db, overrides=overrides)
    return {
        "slack_enabled": config["slack_enabled"],
        "slack_webhook_url": config["slack_webhook_url"],
        "teams_enabled": config["teams_enabled"],
        "teams_webhook_url": config["teams_webhook_url"],
    }
