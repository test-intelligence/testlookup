"""Offline mode must stop notifications leaving the box.

Re-audit finding H10. ``THREAT_MODEL.md`` promises that an air-gapped
deployment "will see zero application-level egress" with
``AI_OFFLINE_MODE=true``, and lists the outbound paths that honour it. Slack,
Teams and SMTP were in neither the list nor the code: each posted notification
content -- failure text, test names, build metadata -- to a caller-configured
destination without checking anything.

The gate is residency, not channel name, for the same reason as C3: a
destination is remote because of where it resolves. A self-hosted webhook on
the LAN is a legitimate offline destination and must keep working; an SMTP
relay at a public address is egress however ordinary "email" sounds.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.core.config import settings
from app.services.notification.egress import (
    OfflineEgressBlocked,
    assert_delivery_allowed,
)

NOTIFICATION_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "services" / "notification"

LAN_WEBHOOK = "http://mattermost.internal/hooks/abc"
PUBLIC_WEBHOOK = "https://hooks.slack.com/services/T000/B000/xxxx"


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)


@pytest.fixture
def resolves(monkeypatch):
    """Control residency without touching DNS."""

    def _set(mapping):
        monkeypatch.setattr(
            "app.services.notification.egress.host_is_local",
            lambda host: mapping.get(host, False),
        )

    return _set


# ── The refusal ──────────────────────────────────────────────────────────


def test_a_public_destination_is_refused_offline(offline, resolves):
    resolves({})
    with pytest.raises(OfflineEgressBlocked) as exc:
        assert_delivery_allowed("Slack", PUBLIC_WEBHOOK)
    assert "hooks.slack.com" in str(exc.value)


def test_an_internal_destination_is_allowed_offline(offline, resolves):
    """An air-gapped deployment's own alerting must keep working."""
    resolves({"mattermost.internal": True})
    assert_delivery_allowed("Slack", LAN_WEBHOOK) is None


def test_an_unset_destination_is_refused_offline(offline, resolves):
    """Nothing to check means nothing to trust — fail closed."""
    resolves({})
    with pytest.raises(OfflineEgressBlocked):
        assert_delivery_allowed("SMTP", None)
    with pytest.raises(OfflineEgressBlocked):
        assert_delivery_allowed("SMTP", "")


def test_nothing_is_blocked_when_offline_mode_is_off(online, resolves):
    resolves({})
    assert assert_delivery_allowed("Slack", PUBLIC_WEBHOOK) is None


def test_a_bare_smtp_host_is_understood(offline, resolves):
    """SMTP config carries a host, not a URL."""
    resolves({"smtp.internal": True})
    assert_delivery_allowed("SMTP", "smtp.internal") is None
    assert_delivery_allowed("SMTP", "smtp.internal:587") is None

    resolves({})
    with pytest.raises(OfflineEgressBlocked):
        assert_delivery_allowed("SMTP", "smtp.gmail.com:587")


def test_an_unresolvable_host_is_refused(offline):
    """Fails closed: the real residency check denies what it cannot resolve."""
    with pytest.raises(OfflineEgressBlocked):
        assert_delivery_allowed("Slack", "https://nonexistent.invalid/hook")


# ── Each sender must actually call it ────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_refuses_a_public_webhook_offline(offline, resolves):
    from app.services.notification import slack_service

    resolves({})
    with pytest.raises(OfflineEgressBlocked):
        await slack_service.send_notification(
            webhook_url=PUBLIC_WEBHOOK,
            title="t",
            body="b",
            event_type="test_failed",
        )


@pytest.mark.asyncio
async def test_teams_refuses_a_public_webhook_offline(offline, resolves):
    from app.services.notification import teams_service

    resolves({})
    with pytest.raises(OfflineEgressBlocked):
        await teams_service.send_notification(
            webhook_url="https://outlook.office.com/webhook/xxx",
            title="t",
            body="b",
            event_type="test_failed",
        )


@pytest.mark.asyncio
async def test_email_refuses_a_public_relay_offline(offline, resolves, monkeypatch):
    from app.services.notification import email_service

    resolves({})
    sent = []
    monkeypatch.setattr(
        email_service.aiosmtplib,
        "send",
        lambda *a, **k: sent.append(k) or None,
    )

    with pytest.raises(OfflineEgressBlocked):
        await email_service.send_notification(
            to="qa@example.com",
            title="t",
            body="b",
            event_type="test_failed",
            smtp_cfg={"enabled": True, "host": "smtp.gmail.com", "port": 587},
        )
    assert sent == [], "mail was handed to the relay before the gate ran"


# ── The gate must not be skippable by adding a sender ────────────────────


def _send_sites(path: pathlib.Path) -> list[str]:
    """Every outbound call in a module, by the AST rather than a grep."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name in {"send", "post"}:
            owner = getattr(getattr(func, "value", None), "id", "")
            if owner in {"aiosmtplib", "client", "httpx"} or name == "post":
                found.append(f"{owner}.{name}")
    return found


def test_the_send_site_scan_finds_something():
    """A scan that matched nothing would pass forever."""
    total = sum(
        len(_send_sites(p))
        for p in NOTIFICATION_DIR.glob("*.py")
        if p.name != "egress.py"
    )
    assert total >= 3, (
        f"found {total} outbound call sites under {NOTIFICATION_DIR.name}/ — "
        "the extractor is probably broken"
    )


def test_every_notification_sender_consults_the_egress_gate():
    """A new channel must not be able to skip the ceiling silently.

    Checks the module, not each function: the gate is applied per outbound
    call site in email_service (three of them) and at the entry point in the
    webhook senders, and either shape is fine so long as the module cannot
    reach the network without asking.
    """
    offenders = []
    for path in sorted(NOTIFICATION_DIR.glob("*.py")):
        if path.name == "egress.py":
            continue
        if not _send_sites(path):
            continue
        source = path.read_text(encoding="utf-8")
        if "assert_delivery_allowed" not in source:
            offenders.append(path.name)

    assert not offenders, (
        "these notification senders reach the network without consulting the "
        "offline egress gate, so AI_OFFLINE_MODE does not apply to them and "
        "THREAT_MODEL.md's 'zero application-level egress' claim is false: "
        + ", ".join(offenders)
    )


def test_the_threat_model_documents_these_channels():
    """The gap was invisible partly because the table did not mention them."""
    root = pathlib.Path(__file__).resolve().parents[3]
    text = (root / "THREAT_MODEL.md").read_text(encoding="utf-8").lower()
    for channel in ("slack", "teams", "smtp"):
        assert channel in text, (
            f"THREAT_MODEL.md does not mention {channel}, so a reader cannot "
            "tell whether offline mode covers it"
        )
