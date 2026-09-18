"""Settings-stored integration tokens remain authoritative after reload."""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings as router  # noqa: E402
from app.models.schemas import IntegrationsConfigUpdate  # noqa: E402
from app.services.secret_service import is_secret_field, strip_secrets_from_config  # noqa: E402


def test_notification_webhooks_are_stripped_from_plaintext_settings():
    payload = {
        "slack_webhook_url": "https://hooks.slack.test/secret",
        "teams_webhook_url": "https://teams.test/secret",
        "slack_enabled": True,
    }
    assert is_secret_field("integrations_config", "slack_webhook_url")
    assert is_secret_field("integrations_config", "teams_webhook_url")
    assert strip_secrets_from_config("integrations_config", payload) == {"slack_enabled": True}


@pytest.mark.asyncio
async def test_integrations_loader_reads_encrypted_secret_refs():
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(value={})
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    secrets = {
        "jira_api_token": "jira-db-token",
        "splunk_api_token": "splunk-db-token",
        "ocp_sa_token": "ocp-db-token",
        "github_token": "github-db-token",
        "slack_webhook_url": "slack-db-webhook",
        "teams_webhook_url": "teams-db-webhook",
    }

    async def read_secret(db_arg, scope, key):
        assert db_arg is db
        assert scope == "integrations_config"
        return secrets[key]

    with patch("app.services.secret_service.read_secret", new=read_secret), patch(
        "app.services.integration_config_service.read_secret", new=read_secret,
    ):
        config = await router._load_integrations_config(db)

    for key, value in secrets.items():
        assert config[key] == value


@pytest.mark.asyncio
async def test_integrations_get_reports_database_tokens_as_set():
    config = {
        "jira_enabled": False, "jira_domain": None, "jira_email": None,
        "jira_api_token": "jira-db-token", "jira_default_project_key": "",
        "splunk_enabled": False, "splunk_base_url": None,
        "splunk_api_token": "splunk-db-token", "ocp_enabled": False,
        "ocp_api_url": None, "ocp_sa_token": "ocp-db-token",
        "ocp_default_namespace": "default", "slack_enabled": False,
        "slack_webhook_url": "slack-db-webhook", "slack_default_channel": "",
        "teams_enabled": False, "teams_webhook_url": "teams-db-webhook",
        "github_repo": None, "github_token": "github-db-token",
    }
    with patch.object(router, "_load_integrations_config", new=AsyncMock(return_value=config)):
        response = await router.get_integrations_config(
            db=SimpleNamespace(), _=SimpleNamespace(),
        )

    assert response.jira_token_set is True
    assert response.splunk_token_set is True
    assert response.ocp_token_set is True
    assert response.github_token_set is True
    assert response.slack_webhook_set is True
    assert response.teams_webhook_set is True
    assert response.slack_webhook_url is None
    assert response.teams_webhook_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "jira_api_token",
        "splunk_api_token",
        "ocp_sa_token",
        "slack_webhook_url",
        "teams_webhook_url",
        "github_token",
    ],
)
async def test_integrations_empty_secret_expires_stored_value(field):
    base = {
        "jira_enabled": False, "jira_domain": None, "jira_email": None,
        "jira_api_token": None, "jira_default_project_key": "",
        "splunk_enabled": False, "splunk_base_url": None,
        "splunk_api_token": None, "ocp_enabled": False,
        "ocp_api_url": None, "ocp_sa_token": None,
        "ocp_default_namespace": "default", "slack_enabled": False,
        "slack_webhook_url": None, "slack_default_channel": "",
        "teams_enabled": False, "teams_webhook_url": None,
        "github_repo": None, "github_token": None,
    }
    existing = dict(base)
    existing[field] = "stored-secret"
    row = SimpleNamespace(value={}, updated_by=None, is_secret_backed=True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=MagicMock(),
    )
    expire = AsyncMock(return_value=True)

    with patch.object(
        router,
        "_load_integrations_config",
        new=AsyncMock(side_effect=[existing, base]),
    ), patch("app.services.secret_service.expire_secret", new=expire), patch(
        "app.services.secret_service.has_secret", new=AsyncMock(return_value=False)
    ), patch.object(router, "log_settings_change", new=AsyncMock()):
        response = await router.update_integrations_config(
            payload=IntegrationsConfigUpdate(**{field: ""}),
            current_user=SimpleNamespace(id=None),
            db=db,
        )

    expire.assert_awaited_once_with(db, "integrations_config", field)
    db.flush.assert_awaited_once()
    assert field not in row.value
    assert response.slack_webhook_url is None
    assert response.teams_webhook_url is None
