"""Re-audit N25: an allow-listed mail relay no longer delivers to anyone.

``OFFLINE_NOTIFICATION_ALLOWED_HOSTS`` lets an operator admit a hosted relay
while offline. The gate judged the relay only, so mail through it went to any
address -- including one a user typed into their own notification
preferences. Every test records what reached the SMTP library.

Also pins the documented N8 limit for SMTP: the relay is dialed by NAME
(aiosmtplib resolves it again at connect time); SECURITY.md s5 records it as
accepted.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.notification import egress, email_service
from app.services.notification.egress import OfflineEgressBlocked, assert_recipients_allowed

HOSTED = "smtp.office365.com"
ON_BOX = "relay.corp.internal"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", HOSTED)
    monkeypatch.setattr(settings, "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS", "")
    monkeypatch.setattr(egress, "host_is_local", lambda host: host == ON_BOX)
    sent: list[dict] = []

    async def fake_send(message, **kwargs):
        sent.append({"to": message["To"], **kwargs})

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    return sent


async def _notify(to: str, relay: str) -> None:
    await email_service.send_notification(
        to, "t", "b", "test_failed", smtp_cfg={"enabled": True, "host": relay, "port": 587},
    )


@pytest.mark.asyncio
async def test_a_hosted_relay_without_a_recipient_list_is_refused(world):
    with pytest.raises(OfflineEgressBlocked, match="OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS"):
        await _notify("qa@corp.example", HOSTED)
    assert world == []


@pytest.mark.asyncio
async def test_a_listed_domain_is_delivered_and_an_outside_one_is_not(world, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS", "corp.example")
    await _notify("qa@corp.example", HOSTED)
    assert [m["to"] for m in world] == ["qa@corp.example"]
    with pytest.raises(OfflineEgressBlocked, match="gmail.com"):
        await _notify("someone@gmail.com", HOSTED)
    # One outside address among listed ones refuses the whole message.
    with pytest.raises(OfflineEgressBlocked):
        await _notify("qa@corp.example, leak@gmail.com", HOSTED)
    assert len(world) == 1


def test_domain_matching_is_exact_or_a_dotted_suffix(world, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS", "corp.example,.eu.corp.example")
    assert_recipients_allowed(HOSTED, "a@corp.example")
    assert_recipients_allowed(HOSTED, "a@team.eu.corp.example")
    for outside in ("a@evilcorp.example", "a@sub.corp.example", "a@eu.corp.example.attacker.io", "no-at-sign"):
        with pytest.raises(OfflineEgressBlocked):
            assert_recipients_allowed(HOSTED, outside)


@pytest.mark.asyncio
async def test_an_on_box_relay_without_a_list_still_delivers(world):
    await _notify("anyone@gmail.com", ON_BOX)
    assert [m["hostname"] for m in world] == [ON_BOX]


@pytest.mark.asyncio
async def test_the_list_also_fences_an_on_box_relay(world, monkeypatch):
    monkeypatch.setattr(settings, "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS", "corp.example")
    with pytest.raises(OfflineEgressBlocked):
        await _notify("anyone@gmail.com", ON_BOX)
    assert world == []


@pytest.mark.asyncio
async def test_online_there_is_no_recipient_rule(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    await _notify("anyone@gmail.com", HOSTED)
    assert len(world) == 1


@pytest.mark.asyncio
async def test_every_email_sender_applies_the_rule(world):
    cfg = {"enabled": True, "host": HOSTED, "port": 587}
    with pytest.raises(OfflineEgressBlocked):
        await email_service.send_html_email("x@gmail.com", "s", "<p>b</p>", smtp_cfg=cfg)
    with pytest.raises(OfflineEgressBlocked):
        await email_service.send_html_email_with_attachments(
            "x@gmail.com", "s", "<p>b</p>", attachments=[("a.txt", "x", "text/plain")], smtp_cfg=cfg,
        )
    assert world == []


def test_the_trends_report_email_applies_the_rule(world, monkeypatch):
    from app.services import report_service

    opened: list = []
    monkeypatch.setattr(settings, "SMTP_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", HOSTED)
    monkeypatch.setattr(report_service.smtplib, "SMTP", lambda *a, **k: opened.append(a))
    with pytest.raises(HTTPException) as refused:
        report_service.send_email("exec@gmail.com", "s", "<p>b</p>")
    assert refused.value.status_code == 503
    assert "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS" in refused.value.detail
    assert opened == []


@pytest.mark.asyncio
async def test_the_smtp_test_button_applies_the_rule(world, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.routers import app_settings

    monkeypatch.setattr(
        app_settings, "_load_smtp_row",
        AsyncMock(return_value={"enabled": True, "host": HOSTED, "port": 587}),
    )
    result = await app_settings.test_smtp_config(
        current_user=SimpleNamespace(id="u", email="lead@gmail.com"), db=None,
    )
    assert result.success is False
    assert "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS" in result.message
    assert world == []


@pytest.mark.asyncio
async def test_documented_n8_limit_smtp_dials_the_relay_by_name(world, monkeypatch):
    """SECURITY.md s5: SMTP is not pinned; the relay name reaches aiosmtplib."""
    monkeypatch.setattr(settings, "OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS", "corp.example")
    await _notify("qa@corp.example", ON_BOX)
    assert world[0]["hostname"] == ON_BOX
