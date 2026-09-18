"""On-demand connector probes must use DB-authoritative configuration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import integration_health
from app.services import integration_config_service, integration_probe_service


class _Response:
    status_code = 200

    def json(self):
        return {"serverTitle": "DB Jira", "rate": {"remaining": 7}}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["jira", "splunk", "github", "ocp", "slack", "teams"])
async def test_on_demand_probe_uses_resolved_runtime_config(monkeypatch, provider):
    db = SimpleNamespace()
    config = {
        "jira_enabled": True,
        "jira_api_token": "encrypted-db-jira",
        "splunk_enabled": True,
        "splunk_api_token": "encrypted-db-splunk",
        "github_token": "encrypted-db-github",
        "ocp_enabled": True,
        "ocp_sa_token": "encrypted-db-ocp",
        "slack_enabled": True,
        "slack_webhook_url": "encrypted-db-webhook",
        "teams_enabled": False,
        "teams_webhook_url": None,
    }
    probe = AsyncMock(
        return_value=integration_probe_service.ProbeResult(provider, "healthy")
    )
    resolve = AsyncMock(return_value=config)
    persist = AsyncMock()

    monkeypatch.setattr(integration_probe_service, "ALL_PROBES", {provider: probe})
    monkeypatch.setattr(integration_probe_service, "persist_probe_results", persist)
    monkeypatch.setattr(
        integration_config_service,
        "resolve_global_integrations",
        resolve,
    )

    result = await integration_health.trigger_probe(
        provider=provider,
        current_user=SimpleNamespace(),
        db=db,
    )

    resolve.assert_awaited_once_with(db)
    probe.assert_awaited_once_with(config)
    persist.assert_awaited_once()
    assert result[0]["provider"] == provider


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected_without_running_all_probes(monkeypatch):
    run_all = AsyncMock(return_value=[])
    persist = AsyncMock()
    monkeypatch.setattr(integration_probe_service, "run_all_probes", run_all)
    monkeypatch.setattr(integration_probe_service, "persist_probe_results", persist)

    with pytest.raises(HTTPException) as exc_info:
        await integration_health.trigger_probe(
            provider="slcak",
            current_user=SimpleNamespace(),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 422
    assert "slcak" in exc_info.value.detail
    run_all.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_jira_probe_uses_resolved_endpoint_and_credentials(monkeypatch):
    client = SimpleNamespace(get=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(integration_probe_service, "get_http_client", lambda: client)
    monkeypatch.setattr(integration_probe_service, "_offline_hard_gate", lambda _provider: None)

    result = await integration_probe_service.probe_jira({
        "jira_enabled": True,
        "jira_domain": "db-jira.example",
        "jira_email": "db-user@example.test",
        "jira_api_token": "db-token",
    })

    assert result.status == "healthy"
    client.get.assert_awaited_once_with(
        "https://db-jira.example/rest/api/3/serverInfo",
        auth=("db-user@example.test", "db-token"),
        timeout=10.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe_name", "config", "expected_url", "expected_header"),
    [
        (
            "probe_splunk",
            {"splunk_enabled": True, "splunk_base_url": "https://db-splunk", "splunk_api_token": "db-token"},
            "https://db-splunk/services/server/info",
            "Bearer db-token",
        ),
        (
            "probe_ocp",
            {"ocp_enabled": True, "ocp_api_url": "https://db-ocp", "ocp_sa_token": "db-token", "ocp_default_namespace": "qa"},
            "https://db-ocp/api/v1/namespaces/qa",
            "Bearer db-token",
        ),
        (
            "probe_github",
            {"github_token": "db-token"},
            "https://api.github.com/rate_limit",
            "token db-token",
        ),
    ],
)
async def test_bearer_probes_use_resolved_config(
    monkeypatch, probe_name, config, expected_url, expected_header,
):
    client = SimpleNamespace(get=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(integration_probe_service, "get_http_client", lambda: client)
    monkeypatch.setattr(integration_probe_service, "_offline_hard_gate", lambda _provider: None)

    result = await getattr(integration_probe_service, probe_name)(config)

    assert result.status == "healthy"
    call = client.get.await_args
    assert call.args[0] == expected_url
    assert call.kwargs["headers"]["Authorization"] == expected_header
