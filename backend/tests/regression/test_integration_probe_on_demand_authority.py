"""On-demand Slack/Teams probes must use DB-authoritative configuration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import integration_health
from app.services import integration_config_service, integration_probe_service


@pytest.mark.asyncio
async def test_on_demand_slack_probe_uses_resolved_runtime_config(monkeypatch):
    db = SimpleNamespace()
    config = {
        "slack_enabled": True,
        "slack_webhook_url": "encrypted-db-webhook",
        "teams_enabled": False,
        "teams_webhook_url": None,
    }
    probe = AsyncMock(
        return_value=integration_probe_service.ProbeResult("slack", "healthy")
    )
    resolve = AsyncMock(return_value=config)
    persist = AsyncMock()

    monkeypatch.setattr(integration_probe_service, "ALL_PROBES", {"slack": probe})
    monkeypatch.setattr(integration_probe_service, "persist_probe_results", persist)
    monkeypatch.setattr(
        integration_config_service,
        "resolve_global_notification_webhooks",
        resolve,
    )

    result = await integration_health.trigger_probe(
        provider="slack",
        current_user=SimpleNamespace(),
        db=db,
    )

    resolve.assert_awaited_once_with(db)
    probe.assert_awaited_once_with(config)
    persist.assert_awaited_once()
    assert result[0]["provider"] == "slack"


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
