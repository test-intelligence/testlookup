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


def _aiosmtplib_modules() -> list:
    """Every module object a sender may reach aiosmtplib through.

    Several suites put a stub in ``sys.modules["aiosmtplib"]`` when they are
    collected, so the module a sender bound at import and the one a later
    ``import aiosmtplib`` returns can differ with collection order. Patching
    only one of them fails on the stub, or lets a send through the other
    really be attempted.
    """
    import sys

    from app.routers import app_settings
    from app.services.notification import email_service

    candidates = (email_service.aiosmtplib, app_settings.aiosmtplib, sys.modules.get("aiosmtplib"))
    return list({id(module): module for module in candidates if module is not None}.values())


@pytest.fixture
def smtp_sends(monkeypatch):
    """Every aiosmtplib.send, recorded instead of sent."""
    sent: list[dict] = []

    async def _fake_send(*_args, **kwargs):
        sent.append(kwargs)

    for module in _aiosmtplib_modules():
        monkeypatch.setattr(module, "send", _fake_send, raising=False)
    return sent


def _patch_probe_smtp(monkeypatch, fake) -> None:
    """The SMTP probe's ``aiosmtplib.SMTP``, on every module object it may use."""
    for module in _aiosmtplib_modules():
        monkeypatch.setattr(module, "SMTP", fake, raising=False)


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
    _patch_probe_smtp(monkeypatch, lambda *a, **k: opened.append(k))

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
    assert assert_delivery_allowed("Slack", PUBLIC_WEBHOOK, deployment_wide=True) is None
    with pytest.raises(OfflineEgressBlocked):
        assert_delivery_allowed("SMTP", "smtp.gmail.com", deployment_wide=True)


def test_the_allow_list_covers_only_the_deployments_own_destinations(offline, resolves, monkeypatch):
    """Second review of H10: Slack and Teams put every workspace on the same
    hosts, so an allow-listed hooks.slack.com admits a webhook to any
    workspace. A destination the operator did not configure gets residency
    alone, and that stricter rule is the default."""
    resolves({})
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "hooks.slack.com")
    with pytest.raises(OfflineEgressBlocked) as exc:
        assert_delivery_allowed("Slack", PUBLIC_WEBHOOK)
    assert "OFFLINE_NOTIFICATION_ALLOWED_HOSTS" in str(exc.value), (
        "the refusal must say why the operator's exception did not apply"
    )
    resolves({"mattermost.internal": True})
    assert assert_delivery_allowed("Slack", LAN_WEBHOOK) is None, "residency still applies"


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
        assert assert_delivery_allowed("SMTP", host, deployment_wide=True) is None
    else:
        with pytest.raises(OfflineEgressBlocked):
            assert_delivery_allowed("SMTP", host, deployment_wide=True)


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


# ── Second review of H10: the allow-list is the deployment's, not a user's ──
#
# The list works per host, and Slack and Teams put every workspace on the same
# hosts. With the documented example allow-listed, any authenticated user could
# set a personal webhook (POST/PUT /api/v1/notifications/preferences) to a
# workspace of their own and receive failure text there. The list now covers
# the deployment's own destinations only: the global webhooks and the relay.

ORG_SLACK = "https://hooks.slack.com/services/TORG/BORG/org-alerts"
OWN_SLACK = "https://hooks.slack.com/services/TMINE/BMINE/my-own-workspace"
ORG_TEAMS = "https://contoso.webhook.office.com/webhookb2/org-alerts"
OWN_TEAMS = "https://mine.webhook.office.com/webhookb2/my-own-tenant"
_WEBHOOKS = {"slack": (ORG_SLACK, OWN_SLACK), "teams": (ORG_TEAMS, OWN_TEAMS)}
_HOSTED_RELAY = {
    "enabled": True,
    "host": "smtp.sendgrid.net",
    "port": 587,
    "from_address": "noreply@example.com",
}


class _Response:
    def raise_for_status(self):
        return None


class _PostRecorder:
    """The public HTTP client the webhook senders use, recording each post."""

    def __init__(self):
        self.urls: list[str] = []

    async def post(self, url, **_kwargs):
        self.urls.append(url)
        return _Response()


@pytest.fixture
def posted(monkeypatch):
    from app.core import http_client

    recorder = _PostRecorder()
    monkeypatch.setattr(http_client, "get_public_http_client", lambda: recorder)
    return recorder


@pytest.fixture
def docs_example_allow_listed(offline, resolves, monkeypatch):
    """The CHANGELOG's own example; nothing resolves on-box."""
    resolves({})
    monkeypatch.setattr(
        settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "hooks.slack.com,.webhook.office.com"
    )


@pytest.fixture
def relay_allow_listed(offline, resolves, monkeypatch):
    resolves({})
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "smtp.sendgrid.net")


def _preference(channel: str, own_webhook: str | None):
    from types import SimpleNamespace

    from app.services.notification import manager

    return SimpleNamespace(
        channel=manager.NotificationChannel(channel),
        email_override=None,
        slack_webhook_url=own_webhook if channel == "slack" else None,
        teams_webhook_url=own_webhook if channel == "teams" else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["slack", "teams"])
async def test_the_global_webhook_is_delivered_and_a_personal_one_refused(
    docs_example_allow_listed, posted, channel
):
    from app.services.notification import manager

    org_hook, own_hook = _WEBHOOKS[channel]
    global_webhooks = {f"{channel}_enabled": True, f"{channel}_webhook_url": org_hook}
    event = manager.NotificationEventType.RUN_FAILED

    delivered = await manager._dispatch_to_channel(
        _preference(channel, None), None, "t", "b", event, {}, global_webhooks=global_webhooks
    )
    assert delivered == ("sent", None)
    assert posted.urls == [org_hook]

    status, error = await manager._dispatch_to_channel(
        _preference(channel, own_hook), None, "t", "b", event, {}, global_webhooks=global_webhooks
    )
    assert status == "failed"
    assert "OFFLINE_NOTIFICATION_ALLOWED_HOSTS" in error, error
    assert posted.urls == [org_hook], "the personal webhook was posted to"


class _Answer:
    """One query result, in whichever shape the dispatcher reads it."""

    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self


class _ScriptedSession:
    """Every AsyncSessionLocal() the digest task opens; answers in order."""

    def __init__(self, answers: list, added: list):
        self._answers = answers
        self._added = added

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _statement):
        return _Answer(self._answers.pop(0))

    async def commit(self):
        return None

    def add(self, row):
        self._added.append(row)


@pytest.mark.parametrize("channel", ["slack", "teams"])
@pytest.mark.parametrize("own_webhook", [True, False], ids=["personal", "global"])
def test_a_digest_uses_the_allow_list_for_the_global_webhook_only(
    docs_example_allow_listed, posted, monkeypatch, channel, own_webhook
):
    """The scheduled digest takes the subscriber's own webhook first, then the
    global one: the same two kinds of destination, so the same rule."""
    import uuid
    from types import SimpleNamespace

    import app.db.postgres as postgres
    from app.services import digest_content_service, integration_config_service
    from app.worker import tasks

    org_hook, own_hook = _WEBHOOKS[channel]
    user_id, project_id = uuid.uuid4(), uuid.uuid4()
    prefs = [
        SimpleNamespace(
            slack_webhook_url=own_hook if channel == "slack" else None,
            teams_webhook_url=own_hook if channel == "teams" else None,
        )
    ]
    added: list = []
    answers = [
        [(uuid.uuid4(), "DAILY", None, True, False)],  # the due subscription
        (user_id, project_id, channel),  # the claim
        SimpleNamespace(id=user_id, role="QA_ENGINEER", email="qa@example.com"),
        prefs if own_webhook else [],
    ]
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _ScriptedSession(answers, added))

    async def _digest(*_args, **_kwargs):
        return {"project_name": "Acme", "is_zero_change": False}

    async def _global(_db):
        return {f"{channel}_enabled": True, f"{channel}_webhook_url": org_hook}

    monkeypatch.setattr(digest_content_service, "generate_digest", _digest)
    monkeypatch.setattr(digest_content_service, "render_digest_text", lambda _digest: "digest")
    monkeypatch.setattr(
        digest_content_service, "digest_text_with_attachment_note", lambda text, _flag: text
    )
    monkeypatch.setattr(integration_config_service, "resolve_global_notification_webhooks", _global)

    tasks.dispatch_scheduled_digests.run()

    assert answers == [], f"the dispatcher stopped early; unanswered: {answers}"
    [log] = added
    if own_webhook:
        assert posted.urls == [], "the digest went to the subscriber's own workspace"
        assert log.status == "failed"
        assert "OFFLINE_NOTIFICATION_ALLOWED_HOSTS" in log.error_detail
    else:
        assert log.status == "sent", log.error_detail
        assert posted.urls == [org_hook]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["slack", "teams"])
async def test_a_team_channel_is_not_covered_by_the_allow_list(
    docs_example_allow_listed, posted, channel
):
    """A QA lead sets a team's webhook per project on the Ownership page. That
    is not the operator's decision either, so it gets residency alone."""
    from app.services import notification_routing as routing

    _org_hook, team_hook = _WEBHOOKS[channel]
    status, error = await routing.send_to_team_channel(
        routing.TeamChannelInfo(team_name="Identity", channel_type=channel, target=team_hook),
        "t", "b", "test.newly_failing", {},
    )
    assert status == "failed"
    assert "OFFLINE_NOTIFICATION_ALLOWED_HOSTS" in (error or "")
    assert posted.urls == []


@pytest.mark.asyncio
async def test_startup_does_not_warn_about_an_allow_listed_global_webhook(
    docs_example_allow_listed, monkeypatch
):
    from app.services.notification import egress

    async def _configured():
        return [("Slack", ORG_SLACK), ("Teams", ORG_TEAMS)]

    monkeypatch.setattr(egress, "_configured_destinations", _configured)
    assert await egress.offline_destination_warnings() == []


# The relay is the deployment's own. Narrowing the list must not take the escape
# hatch away from mail: one test per SMTP entry point.


@pytest.mark.asyncio
async def test_an_allow_listed_relay_still_takes_email(relay_allow_listed, smtp_sends):
    from app.services.notification import email_service

    await email_service.send_html_email(
        "qa@example.com", "Digest", "<p>b</p>", smtp_cfg=dict(_HOSTED_RELAY)
    )
    assert [call["hostname"] for call in smtp_sends] == ["smtp.sendgrid.net"]


@pytest.mark.asyncio
async def test_an_allow_listed_relay_still_takes_the_smtp_test_email(
    relay_allow_listed, smtp_sends, monkeypatch
):
    from types import SimpleNamespace

    from app.routers import app_settings

    async def _stored(_db):
        return dict(_HOSTED_RELAY)

    monkeypatch.setattr(app_settings, "_load_smtp_row", _stored)
    result = await app_settings.test_smtp_config(
        current_user=SimpleNamespace(id=1, email="qa@example.com"), db=None
    )
    assert result.success is True, result.message
    assert [call["hostname"] for call in smtp_sends] == ["smtp.sendgrid.net"]


def test_an_allow_listed_relay_still_takes_the_report_email(relay_allow_listed, monkeypatch):
    from app.services import report_service

    monkeypatch.setattr(settings, "SMTP_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.sendgrid.net")
    opened: list[str] = []

    class _Server:
        def __init__(self, host, *_args, **_kwargs):
            opened.append(host)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def starttls(self, **_kwargs):
            return None

        def login(self, *_args):
            return None

        def sendmail(self, *_args):
            return None

    monkeypatch.setattr(report_service.smtplib, "SMTP", _Server)
    report_service.send_email("qa@example.com", "Trends", "<p>x</p>")
    assert opened == ["smtp.sendgrid.net"]


@pytest.mark.asyncio
async def test_an_allow_listed_relay_is_still_probed(relay_allow_listed, monkeypatch):
    from app.services import integration_probe_service as probes

    monkeypatch.setattr(settings, "SMTP_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.sendgrid.net")
    connected: list[str] = []

    class _Smtp:
        def __init__(self, hostname, **_kwargs):
            connected.append(hostname)

        async def connect(self):
            return None

        async def login(self, *_args):
            return None

        async def quit(self):
            return None

    _patch_probe_smtp(monkeypatch, _Smtp)
    result = await probes.probe_smtp()
    assert connected == ["smtp.sendgrid.net"]
    assert result.status == "healthy", result.message


def _callee(call: ast.Call) -> str | None:
    return getattr(call.func, "id", None) or getattr(call.func, "attr", None)


def _webhook_send_sites() -> list[tuple[str, ast.Call, ast.AST]]:
    """Every call to the Slack or Teams sender in app/, with its enclosing function."""
    sites = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_notification"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"slack_service", "teams_service"}
            ):
                continue
            scope = parents.get(node)
            while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = parents.get(scope)
            sites.append((f"{path.relative_to(APP_DIR).as_posix()}:{node.lineno}", node, scope))
    return sites


def test_every_slack_and_teams_send_says_whose_webhook_it_is():
    """By the AST, per call site. The senders default to the stricter rule, so
    a caller that says nothing is safe; this makes every caller decide, and
    admits True only from preference_webhook, which returns it for the
    admin-configured global webhook alone."""
    sites = _webhook_send_sites()
    modules = {site.split(":")[0] for site, _call, _scope in sites}
    assert {"services/notification/manager.py", "services/notification_routing.py", "worker/tasks.py"} <= modules, (
        f"the scan no longer finds the known senders: {sorted(modules)}"
    )
    offenders = []
    for site, call, scope in sites:
        stated = {keyword.arg: keyword.value for keyword in call.keywords}.get("deployment_wide")
        from_helper = {
            target.elts[1].id
            for node in ast.walk(scope)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _callee(node.value) == "preference_webhook"
            for target in node.targets
            if isinstance(target, ast.Tuple)
            and len(target.elts) == 2
            and isinstance(target.elts[1], ast.Name)
        } if scope is not None else set()
        if isinstance(stated, ast.Constant) and stated.value is False:
            continue
        if isinstance(stated, ast.Name) and stated.id in from_helper:
            continue
        offenders.append(site)
    assert not offenders, (
        "these Slack/Teams sends do not say whether their webhook is the "
        "deployment's own (deployment_wide=False, or the flag preference_webhook "
        "returns): " + ", ".join(offenders)
    )
