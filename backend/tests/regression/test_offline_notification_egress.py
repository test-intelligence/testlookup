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


# ── Code review + QA of H10: every path, an allow-list, off the loop ──────

APP_DIR = NOTIFICATION_DIR.parents[1]
_SMTP_CALLS = {
    ("aiosmtplib", "send"),
    ("aiosmtplib", "SMTP"),
    ("smtplib", "SMTP"),
    ("smtplib", "SMTP_SSL"),
}
_PUBLIC_RELAY = {"enabled": True, "host": "smtp.gmail.com", "port": 587}


def _smtp_connection_sites(path: pathlib.Path) -> list[int]:
    """Lines where a module opens an SMTP connection, by the AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and (node.func.value.id, node.func.attr) in _SMTP_CALLS
    ]


def _modules_opening_smtp() -> dict[str, list[int]]:
    found = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        sites = _smtp_connection_sites(path)
        if sites:
            found[path.relative_to(APP_DIR).as_posix()] = sites
    return found


def test_the_smtp_scan_sees_every_known_connection():
    """A scan that matched nothing would pass forever."""
    found = _modules_opening_smtp()
    for module in (
        "services/notification/email_service.py",
        "services/report_service.py",
        "routers/app_settings.py",
        "services/integration_probe_service.py",
    ):
        assert module in found, f"the scan no longer finds {module}: {sorted(found)}"


def test_every_smtp_connection_in_the_app_consults_the_egress_gate():
    """The first ratchet walked one package. The report email, the SMTP test
    email and the health probe were outside it, and none of them asked."""
    offenders = [
        f"{module} (lines {sites})"
        for module, sites in _modules_opening_smtp().items()
        if "assert_delivery_allowed" not in (APP_DIR / module).read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "these modules open an SMTP connection without consulting the offline "
        "egress gate: " + ", ".join(offenders)
    )


@pytest.fixture
def smtp_sends(monkeypatch):
    """Every aiosmtplib.send, recorded instead of sent."""
    sent: list[dict] = []

    async def _fake_send(*_args, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("aiosmtplib.send", _fake_send)
    return sent


@pytest.mark.asyncio
async def test_a_digest_email_refuses_a_public_relay_offline(offline, resolves, smtp_sends):
    from app.services.notification import email_service

    resolves({})
    with pytest.raises(OfflineEgressBlocked):
        await email_service.send_html_email(
            "qa@example.com", "Digest", "<p>b</p>", smtp_cfg=dict(_PUBLIC_RELAY)
        )
    assert smtp_sends == []


@pytest.mark.asyncio
async def test_an_email_with_attachments_refuses_a_public_relay_offline(offline, resolves, smtp_sends):
    from app.services.notification import email_service

    resolves({})
    with pytest.raises(OfflineEgressBlocked):
        await email_service.send_html_email_with_attachments(
            "qa@example.com", "Report", "<p>b</p>",
            attachments=[("report.html", "<p>r</p>", "text/html")],
            smtp_cfg=dict(_PUBLIC_RELAY),
        )
    assert smtp_sends == []


@pytest.mark.asyncio
async def test_the_gate_judges_the_relay_the_send_dials(offline, resolves, smtp_sends, monkeypatch):
    """The gate and the send used different host expressions: with an empty
    stored host the gate judged SMTP_HOST while the send dialled an empty host."""
    from app.services.notification import email_service

    resolves({"relay.internal": True})
    monkeypatch.setattr(settings, "SMTP_HOST", "relay.internal")
    await email_service.send_html_email(
        "qa@example.com", "Digest", "<p>b</p>", smtp_cfg={"enabled": True, "host": ""}
    )
    assert [call["hostname"] for call in smtp_sends] == ["relay.internal"]


def test_the_report_email_refuses_a_public_relay_offline(offline, resolves, monkeypatch):
    from fastapi import HTTPException

    from app.services import report_service

    resolves({})
    monkeypatch.setattr(settings, "SMTP_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    opened: list[tuple] = []
    monkeypatch.setattr(report_service.smtplib, "SMTP", lambda *a, **k: opened.append(a))

    with pytest.raises(HTTPException) as exc:
        report_service.send_email("qa@example.com", "Trends", "<p>x</p>")
    assert exc.value.status_code == 503
    assert "smtp.gmail.com" in str(exc.value.detail)
    assert opened == [], "the trends report opened a connection to the public relay"


@pytest.mark.asyncio
async def test_the_smtp_test_button_refuses_a_public_relay_offline(
    offline, resolves, smtp_sends, monkeypatch
):
    from types import SimpleNamespace

    from app.routers import app_settings

    resolves({})

    async def _stored(_db):
        return dict(_PUBLIC_RELAY, from_address="noreply@example.com")

    monkeypatch.setattr(app_settings, "_load_smtp_row", _stored)
    result = await app_settings.test_smtp_config(
        current_user=SimpleNamespace(id=1, email="qa@example.com"), db=None
    )
    assert result.success is False
    assert "smtp.gmail.com" in result.message
    assert smtp_sends == [], "the test email reached the public relay"


_NO_STORED_HOST = {"enabled": True, "host": "", "port": 587, "from_address": "noreply@example.com"}


@pytest.mark.asyncio
async def test_the_smtp_test_button_judges_the_relay_the_senders_dial(
    offline, resolves, smtp_sends, monkeypatch
):
    """With no stored host every sender dials SMTP_HOST. The button fell back
    to localhost, so offline it approved a relay no real send uses and
    reported success while every real send was refused (review + QA of H10)."""
    from types import SimpleNamespace

    from app.routers import app_settings

    resolves({"localhost": True})
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")

    async def _stored(_db):
        return dict(_NO_STORED_HOST)

    monkeypatch.setattr(app_settings, "_load_smtp_row", _stored)
    result = await app_settings.test_smtp_config(
        current_user=SimpleNamespace(id=1, email="qa@example.com"), db=None
    )
    assert result.success is False, result.message
    assert "smtp.gmail.com" in result.message
    assert smtp_sends == [], "the test email went to a relay no real send uses"


@pytest.mark.asyncio
async def test_the_smtp_test_button_dials_the_relay_the_senders_dial(online, smtp_sends, monkeypatch):
    from types import SimpleNamespace

    from app.routers import app_settings
    from app.services.notification import email_service

    async def _stored(_db):
        return dict(_NO_STORED_HOST)

    monkeypatch.setattr(app_settings, "_load_smtp_row", _stored)
    monkeypatch.setattr(settings, "SMTP_HOST", "relay.example.org")

    result = await app_settings.test_smtp_config(
        current_user=SimpleNamespace(id=1, email="qa@example.com"), db=None
    )
    await email_service.send_html_email(
        "qa@example.com", "Digest", "<p>b</p>", smtp_cfg=dict(_NO_STORED_HOST)
    )
    assert result.success is True, result.message
    assert [call["hostname"] for call in smtp_sends] == ["relay.example.org", "relay.example.org"]


@pytest.mark.asyncio
async def test_the_smtp_probe_does_not_log_in_to_a_public_relay_offline(offline, resolves, monkeypatch):
    from app.services import integration_probe_service as probes

    resolves({})
    monkeypatch.setattr(settings, "SMTP_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    opened: list[dict] = []
    monkeypatch.setattr("aiosmtplib.SMTP", lambda *a, **k: opened.append(k))

    result = await probes.probe_smtp()
    assert result.status == "skipped"
    assert "smtp.gmail.com" in result.message
    assert opened == [], "the health probe connected to the public relay"


class _RecordingClient:
    """Stands in for the shared httpx client; a probe that dials fails loudly."""

    def __init__(self):
        self.calls: list[str] = []

    async def get(self, url, **_kwargs):
        self.calls.append(url)
        raise AssertionError(f"probe dialled {url}")

    async def post(self, url, **_kwargs):
        self.calls.append(url)
        raise AssertionError(f"probe dialled {url}")


@pytest.mark.asyncio
async def test_the_slack_probe_does_not_call_slack_offline(offline, resolves, monkeypatch):
    from app.services import integration_probe_service as probes

    resolves({})
    client = _RecordingClient()
    monkeypatch.setattr(probes, "get_http_client", lambda: client)
    monkeypatch.setattr(settings, "SLACK_ENABLED", True)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test-token")

    result = await probes.probe_slack()
    assert result.status == "skipped"
    assert client.calls == [], "the health probe called slack.com with the bot token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_name, enable",
    [
        ("probe_jira", {"JIRA_ENABLED": True, "JIRA_DOMAIN": "acme.atlassian.net"}),
        ("probe_github", {"GITHUB_TOKEN": "ghp_test"}),
        (
            "probe_splunk",
            {
                "SPLUNK_ENABLED": True,
                "SPLUNK_BASE_URL": "https://splunk.example.com:8089",
                "SPLUNK_API_TOKEN": "splunk-test-token",
            },
        ),
        (
            "probe_ocp",
            {
                "OCP_ENABLED": True,
                "OCP_API_URL": "https://api.ocp.example.com:6443",
                "OCP_SA_TOKEN": "sa-test-token",
            },
        ),
    ],
)
async def test_integrations_offline_mode_switches_off_are_not_probed(
    offline, monkeypatch, probe_name, enable
):
    """Jira and GitHub refuse every outbound call offline; a probe carries the
    same credentials, so it must not make one either.

    Splunk and OpenShift (code review of H10): their probes sent a bearer token
    out every 15 minutes while SECURITY.md said every outbound integration
    short-circuits offline. They now report exactly what Jira and GitHub report.
    """
    from app.services import integration_probe_service as probes

    client = _RecordingClient()
    monkeypatch.setattr(probes, "get_http_client", lambda: client)
    for key, value in enable.items():
        monkeypatch.setattr(settings, key, value)

    provider = probe_name.removeprefix("probe_")
    result = await getattr(probes, probe_name)()
    assert result == probes.ProbeResult(
        provider, "skipped", message=f"AI_OFFLINE_MODE=true -- outbound {provider} calls are disabled"
    )
    assert client.calls == []


def test_an_allow_listed_public_host_is_delivered_to_offline(offline, resolves, monkeypatch):
    """The escape hatch the review asked for: keep the LLM ceiling, keep Slack."""
    resolves({})
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "hooks.slack.com")
    assert assert_delivery_allowed("Slack", PUBLIC_WEBHOOK) is None
    with pytest.raises(OfflineEgressBlocked):
        assert_delivery_allowed("SMTP", "smtp.gmail.com")


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("hooks.example.com", True),
        ("a.b.example.com", True),
        ("example.com", False),
        ("evilexample.com", False),
        ("example.com.evil.net", False),
        ("other.org", True),
        ("sub.other.org", False),
    ],
)
def test_a_leading_dot_matches_subdomains_on_a_dot_boundary(
    offline, resolves, monkeypatch, host, allowed
):
    resolves({})
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", " .Example.com , other.org")
    if allowed:
        assert assert_delivery_allowed("SMTP", host) is None
    else:
        with pytest.raises(OfflineEgressBlocked):
            assert_delivery_allowed("SMTP", host)


@pytest.mark.asyncio
async def test_the_residency_lookup_runs_off_the_event_loop(offline, monkeypatch):
    """getaddrinfo blocks, and on the loop it stalls every request the API serves."""
    import threading

    from app.services.notification import egress

    on_main_thread: list[bool] = []

    def _record(_host):
        on_main_thread.append(threading.current_thread() is threading.main_thread())
        return True

    monkeypatch.setattr(egress, "host_is_local", _record)
    await egress.assert_delivery_allowed_async("Slack", LAN_WEBHOOK)
    assert on_main_thread == [False], "the residency lookup ran on the event loop's thread"


@pytest.mark.asyncio
async def test_startup_names_every_destination_offline_mode_will_refuse(offline, resolves, monkeypatch):
    from app.services.notification import egress

    resolves({"relay.internal": True})

    async def _configured():
        return [
            ("Slack", PUBLIC_WEBHOOK),
            ("Teams", "http://relay.internal/hooks/abc"),
            ("SMTP", "smtp.gmail.com"),
        ]

    monkeypatch.setattr(egress, "_configured_destinations", _configured)
    warnings = await egress.offline_destination_warnings()
    assert len(warnings) == 2, warnings
    assert any("hooks.slack.com" in w for w in warnings)
    assert any("smtp.gmail.com" in w for w in warnings)


@pytest.mark.asyncio
async def test_startup_checks_nothing_when_offline_mode_is_off(online, monkeypatch):
    from app.services.notification import egress

    async def _configured():
        raise AssertionError("destinations were looked up with offline mode off")

    monkeypatch.setattr(egress, "_configured_destinations", _configured)
    assert await egress.offline_destination_warnings() == []


@pytest.mark.asyncio
async def test_startup_judges_the_destinations_the_senders_use(monkeypatch):
    from app.services import integration_config_service
    from app.services.notification import egress, email_service

    async def _hooks(_db):
        return {
            "slack_enabled": True,
            "slack_webhook_url": PUBLIC_WEBHOOK,
            "teams_enabled": False,
            "teams_webhook_url": "https://example.webhook.office.com/x",
        }

    async def _smtp():
        return {"enabled": True, "host": ""}

    monkeypatch.setattr(integration_config_service, "resolve_global_notification_webhooks", _hooks)
    monkeypatch.setattr(email_service, "_get_smtp_cfg", _smtp)
    monkeypatch.setattr(settings, "SMTP_HOST", "relay.example.org")

    assert await egress._configured_destinations() == [
        ("Slack", PUBLIC_WEBHOOK),
        ("SMTP", "relay.example.org"),
    ]


def _calls_in(function: ast.AST) -> set[str]:
    return {
        getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }


def test_the_api_runs_the_startup_check():
    """By the AST: a string that survives in a comment proves nothing."""
    tree = ast.parse((APP_DIR / "main.py").read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    }
    assert "_warn_about_refused_notification_destinations" in _calls_in(functions["lifespan"]), (
        "the API no longer runs the offline-destination check at startup"
    )
    assert "offline_destination_warnings" in _calls_in(
        functions["_warn_about_refused_notification_destinations"]
    )


def test_the_escape_hatch_is_documented_where_operators_look():
    root = pathlib.Path(__file__).resolve().parents[3]
    for doc in ("THREAT_MODEL.md", ".env.example", "k8s/base/configmap.yaml"):
        text = (root / doc).read_text(encoding="utf-8")
        assert "OFFLINE_NOTIFICATION_ALLOWED_HOSTS" in text, f"{doc} does not name the setting"
