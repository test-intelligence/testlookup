"""Settings-stored integration tokens remain authoritative after reload."""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings as router  # noqa: E402


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
    }

    async def read_secret(db_arg, scope, key):
        assert db_arg is db
        assert scope == "integrations_config"
        return secrets[key]

    with patch("app.services.secret_service.read_secret", new=read_secret):
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
        "slack_webhook_url": None, "slack_default_channel": "",
        "teams_enabled": False, "teams_webhook_url": None,
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
