from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import http_client
from app.services.notification import slack_service, teams_service


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [slack_service.send_notification, teams_service.send_notification])
async def test_notification_webhooks_use_public_only_client(monkeypatch, sender):
    protected = SimpleNamespace(
        post=AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None))
    )
    ordinary = AsyncMock(side_effect=AssertionError("ordinary client bypasses SSRF policy"))
    monkeypatch.setattr(http_client, "get_public_http_client", lambda: protected)
    monkeypatch.setattr(http_client, "get_http_client", ordinary)

    await sender(
        "https://hooks.example/notify",
        "title",
        "body",
        "run_failed",
        delivery_id="delivery-1",
    )

    ordinary.assert_not_called()
    protected.post.assert_awaited_once()
    assert protected.post.await_args.args[0] == "https://hooks.example/notify"
    assert protected.post.await_args.kwargs["follow_redirects"] is False
