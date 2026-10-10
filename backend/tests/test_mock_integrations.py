"""Jira and Slack, exercised against a contract fake instead of a patched call.

Owner request 2026-10-10: the real Jira and Slack are not available, so test
the integrations with mock services. ``tests/fakes/fake_integrations.py`` is a
fake Jira Cloud and a fake Slack that refuse what the real services refuse; the
real TestLookup code sends to it through ``httpx.ASGITransport``. The same fake
runs on the homelab for the end-to-end pass.

What these pin, beyond "a request was made":

* Jira: the probe tells healthy from bad credentials from a server error; the
  defect dialog's pickers come from the real endpoints; fix-version sync uses
  the deployment's TLS policy; knowledge sources read the Jira configured in
  Settings, and an epic's child issues come from ``/search/jql`` -- the old
  ``/search`` answers 410 Gone on Jira Cloud (Atlassian CHANGE-2046).
* Slack: every event's message is valid Block Kit, a long AI summary or a long
  title stays inside Slack's limits instead of the whole message being
  refused, and the delivery id reaches Slack.
* ``WEBHOOK_PRIVATE_ALLOWED_HOSTS``: a private host is reachable only when the
  operator names it, exactly.

The defect-filing flows that need PostgreSQL (advisory lock, exactly-once
ledger, status sync) are in
``tests/integration/test_jira_fake_contract_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from tests.fakes.fake_integrations import FakeState, adf_problems, create_app, slack_problems

DOMAIN = "fake-jira.example.test"
WEBHOOK = "https://hooks.fake.test/services/T000/B000/XXXX"


@pytest.fixture
def fake(monkeypatch):
    """The fake, and a client that routes every request to it."""
    from app.core.config import settings

    state = FakeState(base_url=f"https://{DOMAIN}")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    return SimpleNamespace(state=state, client=client)


def _cfg(state: FakeState, **overrides):
    cfg = {
        "enabled": True, "domain": DOMAIN, "email": state.jira_email,
        "api_token": state.jira_token, "default_project_key": "QA",
    }
    cfg.update(overrides)
    return cfg


# ── The fake itself refuses what the real services refuse ──────────────────


def test_the_fake_refuses_documents_and_messages_the_real_services_refuse():
    assert adf_problems({"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "ok"}]}]}) == []
    assert adf_problems({"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]})
    assert adf_problems({"type": "doc", "version": 1, "content": [{"type": "text", "text": "inline at top"}]})
    assert adf_problems("plain text is not a document")
    assert slack_problems({"text": "ok"}) == []
    assert slack_problems({}) == ["no_text"]
    assert slack_problems({"blocks": [{"type": "divider"}] * 51})
    assert slack_problems({"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3001}}]})
    assert slack_problems({"blocks": [{"type": "header", "text": {"type": "plain_text", "text": "x" * 151}}]})
    assert slack_problems({"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": ""}}]})


# ── Jira: Test connection ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jira_probe_tells_healthy_from_bad_credentials_from_a_server_error(fake, monkeypatch):
    from app.services import integration_probe_service as probe

    monkeypatch.setattr(probe, "get_http_client", lambda: fake.client)
    config = {"jira_enabled": True, "jira_domain": DOMAIN, "jira_email": fake.state.jira_email}

    healthy = await probe.probe_jira({**config, "jira_api_token": fake.state.jira_token})
    assert healthy.status == "healthy" and "Fake Jira" in healthy.message

    wrong = await probe.probe_jira({**config, "jira_api_token": "not-the-token"})
    assert wrong.status == "auth_error"

    fake.state.faults.append({"method": "GET", "path": "/rest/api/3/serverInfo", "status": 503})
    down = await probe.probe_jira({**config, "jira_api_token": fake.state.jira_token})
    assert down.status == "degraded" and "503" in down.message


# ── Jira: the defect dialog's pickers ───────────────────────────────────────


@pytest.mark.asyncio
async def test_defect_dialog_lists_jira_projects_and_issue_types(fake, monkeypatch):
    from app.services import defect_jira_service as svc

    async def resolved(db):
        return _cfg(fake.state)

    async def no_webhook(db, pid):
        return False

    monkeypatch.setattr(svc, "get_http_client", lambda: fake.client)
    monkeypatch.setattr(svc, "resolve_jira_config", resolved)
    monkeypatch.setattr(svc, "_webhook_target_available", no_webhook)
    svc._metadata_cache_clear()
    try:
        meta = await svc.get_metadata(None, str(uuid.uuid4()))
    finally:
        svc._metadata_cache_clear()
    assert meta["available"] is True
    assert [p["key"] for p in meta["projects"]] == ["QA", "OPS"]
    assert meta["issue_types"] == ["Bug", "Task", "Story"]  # the sub-task type is left out
    assert all(r["authorized"] for r in fake.state.requests)


@pytest.mark.asyncio
async def test_defect_dialog_reports_rejected_credentials_instead_of_empty_pickers(fake, monkeypatch):
    from app.services import defect_jira_service as svc

    async def resolved(db):
        return _cfg(fake.state, api_token="revoked")

    async def no_webhook(db, pid):
        return False

    monkeypatch.setattr(svc, "get_http_client", lambda: fake.client)
    monkeypatch.setattr(svc, "resolve_jira_config", resolved)
    monkeypatch.setattr(svc, "_webhook_target_available", no_webhook)
    svc._metadata_cache_clear()
    meta = await svc.get_metadata(None, str(uuid.uuid4()))
    assert meta["available"] is False and meta["reason"] == "jira_http_401"


def test_the_issue_description_is_a_valid_atlassian_document():
    from datetime import datetime, timezone

    from app.services import defect_jira_service as svc

    prefill = {
        "summary": "[TestLookup] test_checkout_total",
        "occurrences": {"first_seen": datetime.now(timezone.utc), "last_seen": None, "failing_runs": 3},
        "context": {"branch": "main", "build_number": "142", "ci_run_url": "https://ci.example.com/1"},
        "error_message": "AssertionError: 41 != 40",
        "ai_analysis": {"root_cause": "Tax applied twice", "confidence": 82},
        "deep_link": "https://tl.example.com/runs/1",
    }
    assert adf_problems(svc._adf_from_prefill(prefill, "Seen on staging only")) == []


# ── Jira: fix-version sync ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fix_version_sync_uses_the_deployments_tls_policy(fake, monkeypatch):
    """An on-prem Jira behind an internal CA is trusted through HTTP_CA_BUNDLE
    for defect filing; the version sync built its own client and ignored it."""
    from app.core.config import settings
    from app.services import jira_release_sync as sync

    built: list[dict] = []
    transport = httpx.ASGITransport(app=create_app(fake.state))
    real_client = httpx.AsyncClient  # sync.httpx IS httpx: patching it patches this name too

    def client_factory(*args, **kwargs):
        built.append(kwargs)
        return real_client(transport=transport, timeout=kwargs.get("timeout"))

    async def resolved(db):
        return _cfg(fake.state)

    async def not_blocked(url):
        return None

    monkeypatch.setattr(settings, "HTTP_CA_BUNDLE", "/etc/testlookup-ca/bundle.pem")
    monkeypatch.setattr(sync, "resolve_jira_config", resolved)
    monkeypatch.setattr(sync, "_ssrf_block_reason", not_blocked)
    monkeypatch.setattr(sync.httpx, "AsyncClient", client_factory)
    fake.state.add_version("QA", "2026.10")

    versions = await sync._authorized_get(None, "/project/QA/versions")

    assert [v["name"] for v in versions] == ["2026.10"]
    assert built and built[0]["verify"] == "/etc/testlookup-ca/bundle.pem"


@pytest.mark.asyncio
async def test_fix_version_sync_names_a_rejected_credential(fake, monkeypatch):
    from app.services import jira_release_sync as sync

    transport = httpx.ASGITransport(app=create_app(fake.state))
    real_client = httpx.AsyncClient

    async def resolved(db):
        return _cfg(fake.state, api_token="revoked")

    async def not_blocked(url):
        return None

    monkeypatch.setattr(sync, "resolve_jira_config", resolved)
    monkeypatch.setattr(sync, "_ssrf_block_reason", not_blocked)
    monkeypatch.setattr(sync.httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport))
    with pytest.raises(sync.JiraSyncUnavailable, match="401"):
        await sync._authorized_get(None, "/project/QA/versions")


# ── Jira: knowledge sources ─────────────────────────────────────────────────


def _epic(state: FakeState) -> None:
    state.issues["QA-10"] = {
        "id": "1", "key": "QA-10", "comments": [],
        "fields": {"summary": "Checkout epic", "issuetype": {"name": "Epic"},
                   "status": {"name": "In Progress"}, "labels": [], "project": {"key": "QA"},
                   "description": {"type": "doc", "version": 1, "content": [
                       {"type": "paragraph", "content": [{"type": "text", "text": "All checkout work."}]}]},
                   "created": "2026-10-01T00:00:00.000+0000"},
    }
    state.issues["QA-11"] = {
        "id": "2", "key": "QA-11", "comments": [],
        "fields": {"summary": "Totals off by tax", "issuetype": {"name": "Bug"},
                   "status": {"name": "To Do"}, "labels": [], "project": {"key": "QA"},
                   "parent": {"key": "QA-10"}, "created": "2026-10-02T00:00:00.000+0000"},
    }


@pytest.mark.asyncio
async def test_a_knowledge_source_reads_the_jira_configured_in_settings(fake, monkeypatch):
    """Configured under Settings -> Integrations (AppSetting + secret service),
    not in the environment: the connector used to answer "JIRA_DOMAIN is not
    configured" while defects filed fine."""
    from app.core.config import settings
    from app.services import defect_jira_service as svc
    from app.services.connectors import jira_connector, registry

    async def resolved(db):
        return _cfg(fake.state)

    monkeypatch.setattr(settings, "JIRA_DOMAIN", "")
    monkeypatch.setattr(settings, "JIRA_EMAIL", "")
    monkeypatch.setattr(settings, "JIRA_API_TOKEN", "")
    monkeypatch.setattr(svc, "resolve_jira_config", resolved)
    monkeypatch.setattr(jira_connector, "get_http_client", lambda: fake.client)
    _epic(fake.state)

    connector = await registry.bind_to_deployment(registry.get_connector("jira_epic"), db=None)
    content = await connector.fetch_content(f"https://{DOMAIN}/browse/QA-10")

    assert "Checkout epic" in content.raw_text and "All checkout work." in content.raw_text
    # The epic's children come from /search/jql; /search answers 410 Gone.
    assert "QA-11: Totals off by tax" in content.raw_text
    assert not fake.state.requests_to("GET", "/rest/api/3/search?")
    assert all(r["path"] != "/rest/api/3/search" for r in fake.state.requests)
    assert (await connector.test_connection())["success"] is True


@pytest.mark.asyncio
async def test_other_knowledge_connectors_are_not_rebound():
    from app.services.connectors import registry

    url = registry.get_connector("internal_url")
    assert await registry.bind_to_deployment(url, db=None) is url


# ── Slack ────────────────────────────────────────────────────────────────────


def _route_slack(monkeypatch, fake):
    from app.services.notification import egress

    monkeypatch.setattr(egress, "delivery_http_client", lambda destination, deployment_wide=False: fake.client)


_EVENTS = [
    "run_failed", "run_passed", "high_failure_rate", "ai_analysis_complete",
    "flaky_test_detected", "test.newly_failing", "test.recovered", "test.quarantined",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("event", _EVENTS)
async def test_every_event_posts_a_message_slack_accepts(fake, monkeypatch, event):
    from app.services.notification import slack_service

    _route_slack(monkeypatch, fake)
    await slack_service.send_notification(
        webhook_url=WEBHOOK, title="Build #142 failed — Checkout Web", body="3 of 120 tests failed.",
        event_type=event, delivery_id="d" * 64,
        metadata={"project_name": "Checkout Web", "build_number": "142", "pass_rate": 97.5,
                  "total_tests": 120, "failed_tests": 3, "dashboard_url": "https://tl.example.com/runs/1"},
    )
    [message] = fake.state.slack_messages
    assert message["path"] == "/services/T000/B000/XXXX"
    assert message["delivery_id"] == "d" * 64
    assert message["payload"]["text"] == "Build #142 failed — Checkout Web"


@pytest.mark.asyncio
async def test_a_long_ai_summary_and_title_stay_inside_slacks_limits(fake, monkeypatch):
    """Slack refuses the WHOLE message when one block is over its limit, so a
    3,000-character AI summary used to cost the notification entirely."""
    from app.services.notification import slack_service

    _route_slack(monkeypatch, fake)
    summary = "\n".join(f"Paragraph {i}: " + "the checkout totals regressed. " * 12 for i in range(30))
    assert len(summary) > 9000
    await slack_service.send_notification(
        webhook_url=WEBHOOK, title="AI summary — " + "Very Long Project Name " * 10,
        body=summary, event_type="ai_analysis_complete", metadata={},
    )
    [message] = fake.state.slack_messages
    blocks = message["payload"]["attachments"][0]["blocks"]
    sections = [b["text"]["text"] for b in blocks if b["type"] == "section" and b.get("text")]
    # Nothing silently lost below the cut: the text that went out is the start of the summary.
    assert "".join(sections).replace("\n", "").startswith(summary[:2000].replace("\n", ""))


@pytest.mark.asyncio
async def test_an_empty_body_still_posts(fake, monkeypatch):
    from app.services.notification import slack_service

    _route_slack(monkeypatch, fake)
    await slack_service.send_notification(webhook_url=WEBHOOK, title="Recovered", body="",
                                          event_type="test.recovered", metadata={})
    assert len(fake.state.slack_messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status, retry_after", [(404, None), (429, 30), (500, None)])
async def test_a_refused_webhook_raises_for_the_relay_to_record(fake, monkeypatch, status, retry_after):
    from app.services.notification import slack_service

    _route_slack(monkeypatch, fake)
    if status == 404:
        fake.state.revoked_webhooks.add("/services/T000/B000/XXXX")
    else:
        fake.state.faults.append({"path": "/services/.*", "status": status, "retry_after": retry_after})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await slack_service.send_notification(webhook_url=WEBHOOK, title="t", body="b",
                                              event_type="run_failed", metadata={})
    assert exc.value.response.status_code == status
    assert fake.state.slack_messages == []


# ── WEBHOOK_PRIVATE_ALLOWED_HOSTS ───────────────────────────────────────────


def test_a_private_webhook_host_is_reachable_only_when_named_exactly(monkeypatch):
    from app.core import http_client
    from app.core.config import settings
    from app.services.notification import egress

    shared, public = object(), object()
    monkeypatch.setattr(http_client, "get_http_client", lambda: shared)
    monkeypatch.setattr(http_client, "get_public_http_client", lambda: public)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    monkeypatch.setattr(settings, "WEBHOOK_PRIVATE_ALLOWED_HOSTS", "")
    assert egress.delivery_http_client("https://chat.corp.internal/hooks/x") is public

    monkeypatch.setattr(settings, "WEBHOOK_PRIVATE_ALLOWED_HOSTS", " Chat.Corp.Internal. , other.internal")
    assert egress.delivery_http_client("https://chat.corp.internal/hooks/x") is shared
    assert egress.delivery_http_client("https://CHAT.corp.internal:8443/hooks/x") is shared
    # Exact names only: no subdomains, no look-alikes, nothing else.
    assert egress.delivery_http_client("https://evil.chat.corp.internal/x") is public
    assert egress.delivery_http_client("https://chat.corp.internal.evil.test/x") is public
    assert egress.delivery_http_client("https://hooks.slack.com/services/x") is public


def test_the_private_allow_list_does_not_widen_offline_mode(monkeypatch):
    from app.core.config import settings
    from app.services import llm_egress
    from app.services.notification import egress

    local = object()
    monkeypatch.setattr(llm_egress, "get_local_only_http_client", lambda: local)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "WEBHOOK_PRIVATE_ALLOWED_HOSTS", "chat.corp.internal")
    assert egress.delivery_http_client("https://chat.corp.internal/hooks/x") is local
