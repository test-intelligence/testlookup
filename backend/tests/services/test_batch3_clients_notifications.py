from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import types

import pytest

from app.services import jira_client, ocp_client


def test_jira_auth_header_and_adf_contains_link():
    with patch("app.services.jira_client.settings") as s:
        s.JIRA_EMAIL = "a@b.com"
        s.JIRA_API_TOKEN = "tok"
        hdr = jira_client._auth_header()
        adf = jira_client._adf_description("sum", "trace", "act", "https://x")
    assert hdr.startswith("Basic ")
    assert adf["content"][-1]["content"][1]["marks"][0]["attrs"]["href"] == "https://x"


@pytest.mark.asyncio
async def test_jira_create_issue_success():
    resp = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"id": "1", "key": "QA-1"},
    )
    shared_client = SimpleNamespace(post=AsyncMock(return_value=resp))

    # jira_client now uses the pooled ``get_http_client`` singleton instead
    # of constructing per-request ``httpx.AsyncClient`` instances. Patch the
    # accessor so the test supplies a fake client with a stubbed ``post``.
    with (
        patch("app.services.jira_client.settings") as s,
        patch("app.services.jira_client.get_http_client", return_value=shared_client),
    ):
        s.JIRA_ENABLED = True
        s.JIRA_DOMAIN = "jira.example.com"
        s.JIRA_EMAIL = "a"
        s.JIRA_API_TOKEN = "b"
        out = await jira_client.create_jira_issue("PRJ", "test", "r1", "sum", "do")
    assert out["ticket_key"] == "QA-1"


@pytest.mark.asyncio
async def test_ocp_get_pod_metadata_disabled_returns_none():
    with patch("app.services.ocp_client.settings") as s:
        s.OCP_ENABLED = False
        s.OCP_API_URL = ""
        out = await ocp_client.get_pod_metadata("pod", "ns")
    assert out is None


@pytest.mark.asyncio
async def test_ocp_analyze_events_reports_critical():
    with patch(
        "app.services.ocp_client.get_pod_metadata",
        new=AsyncMock(
            return_value={
                "phase": "Running",
                "events": [{"reason": "OOMKilled", "message": "killed", "count": 2}],
                "resources": {"limits": {"cpu": "1", "memory": "1Gi"}},
            }
        ),
    ):
        out = await ocp_client.analyze_pod_events("pod", "ns", "ts")
    assert "critical event" in out
    assert "Resources" in out


def test_slack_and_teams_build_payload_blocks():
    from app.services.notification import slack_service, teams_service
    s_blocks = slack_service._build_blocks("t", "b", "run_failed", {"pass_rate": 50.0, "dashboard_url": "https://x"})
    t_card = teams_service._build_adaptive_card("t", "b", "run_failed", {"build_number": "12"})
    assert any(item.get("type") == "actions" for item in s_blocks)
    assert t_card["attachments"][0]["content"]["type"] == "AdaptiveCard"


def test_email_templates_contain_metadata():
    with patch.dict("sys.modules", {"aiosmtplib": types.SimpleNamespace(send=AsyncMock())}, clear=False):
        from app.services.notification import email_service
    html = email_service._build_html("Title", "Body", "run_failed", {"project_name": "P", "build_number": "5"})
    plain = email_service._build_plain("Title", "Body", {"failed_tests": 2})
    assert "Build" in html
    assert "Failed tests: 2" in plain


# The fake enum values MUST match the real ``NotificationChannel`` enum
# values (lowercase), because test isolation is not guaranteed — if another
# test imports ``app.services.notification.manager`` first, the patched
# ``sys.modules`` entry is a no-op and ``manager.NotificationChannel`` stays
# bound to the real str-Enum. Using lowercase ``"email"``/``"slack"``/``"teams"``
# works against **both** the fake and the real enum: the real enum inherits
# from ``str`` so comparing ``"slack" == NotificationChannel.SLACK`` returns
# True regardless of which one is bound.
_FAKE_CHANNELS = SimpleNamespace(EMAIL="email", SLACK="slack", TEAMS="teams")


@pytest.mark.asyncio
async def test_notification_dispatch_missing_email_fails():
    fake_models = SimpleNamespace(
        NotificationChannel=_FAKE_CHANNELS,
        NotificationEventType=SimpleNamespace(RUN_FAILED=SimpleNamespace(value="run_failed"), RUN_PASSED=SimpleNamespace(value="run_passed")),
        NotificationLog=object,
        NotificationPreference=object,
        User=object,
    )
    with patch.dict(
        "sys.modules",
        {
            "aiosmtplib": types.SimpleNamespace(send=AsyncMock()),
            "app.models.postgres": fake_models,
            "app.db.postgres": SimpleNamespace(AsyncSessionLocal=None),
        },
        clear=False,
    ):
        from app.services.notification import manager
    pref = SimpleNamespace(
        channel="email",
        email_override=None,
        slack_webhook_url=None,
        teams_webhook_url=None,
    )
    status, detail = await manager._dispatch_to_channel(pref, None, "t", "b", SimpleNamespace(value="run_failed"), {})
    assert status == "failed"
    assert "email" in detail.lower()


@pytest.mark.asyncio
async def test_notification_dispatch_slack_success():
    fake_models = SimpleNamespace(
        NotificationChannel=_FAKE_CHANNELS,
        NotificationEventType=SimpleNamespace(RUN_FAILED=SimpleNamespace(value="run_failed")),
        NotificationLog=object,
        NotificationPreference=object,
        User=object,
    )
    with patch.dict(
        "sys.modules",
        {
            "aiosmtplib": types.SimpleNamespace(send=AsyncMock()),
            "app.models.postgres": fake_models,
            "app.db.postgres": SimpleNamespace(AsyncSessionLocal=None),
        },
        clear=False,
    ):
        from app.services.notification import manager
    pref = SimpleNamespace(
        channel="slack",
        email_override=None,
        slack_webhook_url="https://hooks.slack",
        teams_webhook_url=None,
    )
    with (
        patch("app.services.notification.manager.slack_service.send_notification", new=AsyncMock()) as send_mock,
    ):
        status, detail = await manager._dispatch_to_channel(
            pref, "user@x.com", "t", "b", SimpleNamespace(value="run_failed"), {}
        )
    assert status == "sent"
    assert detail is None
    send_mock.assert_awaited_once()
