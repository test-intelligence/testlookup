"""DB-backed global webhooks drive notification delivery and health probes."""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("aiosmtplib", MagicMock())

from app.services.integration_config_service import resolve_global_notification_webhooks  # noqa: E402
from app.services.integration_probe_service import probe_slack, probe_teams  # noqa: E402
from app.services.notification import manager  # noqa: E402


@pytest.mark.asyncio
async def test_resolver_prefers_encrypted_webhooks_over_inline_and_env():
    db = SimpleNamespace()
    overrides = {
        "slack_enabled": True,
        "slack_webhook_url": "inline-slack",
        "teams_enabled": True,
        "teams_webhook_url": "inline-teams",
    }

    async def read_secret(db_arg, scope, key):
        assert db_arg is db
        return {
            "slack_webhook_url": "db-slack",
            "teams_webhook_url": "db-teams",
        }.get(key)

    with patch("app.services.integration_config_service.read_secret", new=read_secret):
        config = await resolve_global_notification_webhooks(db, overrides=overrides)

    assert config["slack_webhook_url"] == "db-slack"
    assert config["teams_webhook_url"] == "db-teams"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "key", "sender_name"),
    [
        (manager.NotificationChannel.SLACK, "slack_webhook_url", "slack_service"),
        (manager.NotificationChannel.TEAMS, "teams_webhook_url", "teams_service"),
    ],
)
async def test_dispatch_uses_db_global_webhook(channel, key, sender_name):
    pref = SimpleNamespace(channel=channel, slack_webhook_url=None, teams_webhook_url=None)
    sender = AsyncMock()
    service = getattr(manager, sender_name)
    with patch.object(service, "send_notification", new=sender):
        status, error = await manager._dispatch_to_channel(
            pref,
            None,
            "title",
            "body",
            SimpleNamespace(value="run_failed"),
            {},
            global_webhooks={key: "db-webhook", f"{channel.value}_enabled": True},
        )

    assert (status, error) == ("sent", None)
    assert sender.await_args.kwargs["webhook_url"] == "db-webhook"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "key", "sender_name"),
    [
        (manager.NotificationChannel.SLACK, "slack_webhook_url", "slack_service"),
        (manager.NotificationChannel.TEAMS, "teams_webhook_url", "teams_service"),
    ],
)
async def test_disabled_global_channel_does_not_send(channel, key, sender_name):
    pref = SimpleNamespace(channel=channel, slack_webhook_url=None, teams_webhook_url=None)
    sender = AsyncMock()
    with patch.object(getattr(manager, sender_name), "send_notification", new=sender):
        status, error = await manager._dispatch_to_channel(
            pref,
            None,
            "title",
            "body",
            SimpleNamespace(value="run_failed"),
            {},
            global_webhooks={key: "https://db-webhook", f"{channel.value}_enabled": False},
        )

    assert status == "failed"
    assert "No " in error
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_health_probes_use_db_global_webhooks():
    config = {
        "slack_enabled": True,
        "slack_webhook_url": "db-slack",
        "teams_enabled": True,
        "teams_webhook_url": "db-teams",
    }
    slack, teams = await probe_slack(config), await probe_teams(config)

    assert slack.status == "healthy"
    assert teams.status == "healthy"
