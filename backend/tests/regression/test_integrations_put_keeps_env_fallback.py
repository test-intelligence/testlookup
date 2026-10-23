"""W10 INT-01 / INT-02: ``PUT /api/v1/settings/integrations`` stores what was SAVED, not the env view.

The PUT used to load the environment-merged config first and persist
``{**that, **updates}``: every environment value present at the first save was
frozen into the ``integrations_config`` row as if an admin had typed it, so a
later change of the environment never reached the runtime again (contract
§0.3). It now persists ``{**saved_row, **updates}``. Also pinned here:

* INT-02 (D21): ``slack_default_channel`` is accepted and ignored -- never
  stored, never in the audit row's ``changed_fields``;
* the egress guard at save: a loopback / link-local Splunk, OpenShift or Jira
  host is refused with ONE 422 naming each field, before anything (a secret
  included) is written; RFC1918 and a non-resolving host are allowed;
* the field shapes (OpenShift namespace, Jira domain) answer 422 with the
  exact sentences, no "Value error," prefix.

Driven through the real FastAPI routing (Pydantic validation, the handler,
the resolver) over an in-memory ``app_settings`` row; DNS is answered by a
patched ``getaddrinfo`` in ``url_safety``.
"""
from __future__ import annotations

import ipaddress
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.integration_values import JIRA_DOMAIN_SENTENCE, OCP_NAMESPACE_SENTENCE, TARGET_NOT_ALLOWED
from app.db.postgres import get_db
from app.routers import app_settings as router
from app.services import integration_config_service, secret_service, url_safety
from app.services.integration_config_service import clear_runtime_integration_cache

PATH = "/api/v1/settings/integrations"

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _Db:
    """The ``integrations_config`` AppSetting row, in memory."""

    def __init__(self, value: dict | None = None):
        self.row = SimpleNamespace(value=dict(value), updated_by=None, is_secret_backed=False) if value is not None else None
        self.commits = 0

    async def execute(self, _stmt):
        return _Result(self.row)

    def add(self, obj):
        self.row = obj

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


def _resolver(mapping: dict[str, str]):
    """getaddrinfo: IP literals answer themselves, ``mapping`` names its hosts,
    anything else does not resolve."""

    def getaddrinfo(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
            address = host
        except ValueError:
            if host not in mapping:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            address = mapping[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return getaddrinfo


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


@pytest.fixture
def world(monkeypatch):
    """Env values for every key the tests read, secrets in memory, DNS answered."""
    for name, value in {
        "SPLUNK_ENABLED": False, "SPLUNK_BASE_URL": "https://splunk-a.corp.example:8089",
        "SPLUNK_API_TOKEN": None, "OCP_ENABLED": False, "OCP_API_URL": None, "OCP_SA_TOKEN": None,
        "OCP_DEFAULT_NAMESPACE": "qa-testing", "JIRA_ENABLED": False, "JIRA_DOMAIN": None,
        "JIRA_EMAIL": None, "JIRA_API_TOKEN": None, "SLACK_ENABLED": False, "SLACK_WEBHOOK_URL": None,
        "SLACK_DEFAULT_CHANNEL": "#qa-alerts", "TEAMS_ENABLED": False, "TEAMS_WEBHOOK_URL": None,
        "GITHUB_TOKEN": None, "GITHUB_REPO": None,
    }.items():
        monkeypatch.setattr(settings, name, value)

    secrets: dict[str, str] = {}

    async def read_secret(_db, _scope, key):
        return secrets.get(key)

    async def store_secret(_db, _scope, key, value, **_kw):
        secrets[key] = value

    async def expire_secret(_db, _scope, key):
        secrets.pop(key, None)
        return True

    async def has_secret(_db, _scope, key):
        return key in secrets

    store = AsyncMock(side_effect=store_secret)
    audit = AsyncMock()
    monkeypatch.setattr(integration_config_service, "read_secret", read_secret)
    monkeypatch.setattr(router, "store_secret", store)
    monkeypatch.setattr(secret_service, "expire_secret", expire_secret)
    monkeypatch.setattr(secret_service, "has_secret", has_secret)
    monkeypatch.setattr(router, "log_settings_change", audit)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _resolver({"localhost": "127.0.0.1"}))
    return SimpleNamespace(secrets=secrets, store=store, audit=audit)


def _client(db: _Db) -> AsyncClient:
    app = FastAPI()
    app.include_router(router.router)
    admin = SimpleNamespace(id=None, role="ADMIN")

    async def _db():
        yield db

    for route in app.routes:
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
            if dependency.call is not get_db:
                app.dependency_overrides[dependency.call] = lambda: admin
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_a_save_stores_only_what_was_sent_so_the_environment_stays_the_fallback(world, monkeypatch):
    db = _Db()
    async with _client(db) as client:
        response = await client.put(PATH, json={"splunk_enabled": True})
        assert response.status_code == 200, response.text
        # The env base URL (and every other env value) was NOT copied in.
        assert db.row.value == {"splunk_enabled": True}
        assert response.json()["splunk_base_url"] == "https://splunk-a.corp.example:8089"
        assert "splunk_base_url" in response.json()["environment_fields"]

        # The operator changes the environment: the next read follows it.
        monkeypatch.setattr(settings, "SPLUNK_BASE_URL", "https://splunk-b.corp.example:8089")
        after = (await client.get(PATH)).json()
    assert after["splunk_base_url"] == "https://splunk-b.corp.example:8089"
    assert after["splunk_enabled"] is True
    assert "splunk_base_url" in after["environment_fields"]
    assert "splunk_enabled" not in after["environment_fields"]  # booleans are never env-sourced names

    resolved = await integration_config_service.resolve_runtime_integrations(db, None)
    assert resolved.splunk_base_url == "https://splunk-b.corp.example:8089"
    assert resolved.splunk_enabled is True


@pytest.mark.asyncio
async def test_a_later_save_keeps_the_earlier_saved_keys(world):
    db = _Db({"splunk_enabled": True, "splunk_base_url": "https://splunk.corp.example:8089"})
    async with _client(db) as client:
        response = await client.put(PATH, json={"ocp_enabled": True})
    assert response.status_code == 200, response.text
    assert db.row.value == {
        "splunk_enabled": True, "splunk_base_url": "https://splunk.corp.example:8089", "ocp_enabled": True,
    }


@pytest.mark.asyncio
async def test_a_cleared_text_hands_the_key_back_to_the_environment(world):
    db = _Db({"splunk_base_url": "https://splunk-saved.corp.example:8089"})
    async with _client(db) as client:
        response = await client.put(PATH, json={"splunk_base_url": "   "})
    assert response.status_code == 200, response.text
    assert response.json()["splunk_base_url"] == "https://splunk-a.corp.example:8089"
    assert "splunk_base_url" in response.json()["environment_fields"]


@pytest.mark.asyncio
async def test_the_slack_default_channel_is_accepted_and_ignored(world):
    db = _Db()
    async with _client(db) as client:
        response = await client.put(PATH, json={"slack_default_channel": "#elsewhere", "slack_enabled": True})
    assert response.status_code == 200, response.text
    assert "slack_default_channel" not in db.row.value
    assert db.row.value == {"slack_enabled": True}
    changed = world.audit.await_args.kwargs["changed_fields"]
    assert changed == ["slack_enabled"]
    # The read keeps answering the (deprecated) field with the environment's value.
    assert response.json()["slack_default_channel"] == "#qa-alerts"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("splunk_base_url", "http://127.0.0.1:6379"),
    ("splunk_base_url", "http://169.254.169.254/"),
    ("ocp_api_url", "https://[::1]:6443"),
    ("jira_domain", "localhost"),
])
async def test_a_loopback_or_link_local_host_is_refused_before_anything_is_written(world, field, value):
    db = _Db({"splunk_enabled": False})
    async with _client(db) as client:
        response = await client.put(PATH, json={field: value, "splunk_api_token": "would-be-secret"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == [
        {"loc": ["body", field], "msg": TARGET_NOT_ALLOWED, "type": "target_not_allowed"},
    ]
    assert db.row.value == {"splunk_enabled": False}, "nothing is stored"
    assert db.commits == 0
    world.store.assert_not_awaited()
    assert world.secrets == {}, "no secret is written"
    assert "127.0.0.1" not in response.text and "169.254" not in response.text


@pytest.mark.asyncio
async def test_every_refused_host_is_named_in_one_answer(world):
    db = _Db()
    async with _client(db) as client:
        response = await client.put(PATH, json={
            "splunk_base_url": "http://127.0.0.1:8089", "ocp_api_url": "http://169.254.169.254", "jira_domain": "localhost",
        })
    assert response.status_code == 422
    assert [item["loc"] for item in response.json()["detail"]] == [
        ["body", "splunk_base_url"], ["body", "ocp_api_url"], ["body", "jira_domain"],
    ]
    assert db.row is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["http://10.20.30.40:8089", "https://splunk.nowhere.invalid:8089"])
async def test_an_rfc1918_or_non_resolving_host_is_allowed(world, value):
    db = _Db()
    async with _client(db) as client:
        response = await client.put(PATH, json={"splunk_base_url": value})
    assert response.status_code == 200, response.text
    assert db.row.value == {"splunk_base_url": value}


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,sentence", [
    ("ocp_default_namespace", "QA Testing", OCP_NAMESPACE_SENTENCE),
    ("jira_domain", "https://x.atlassian.net/", JIRA_DOMAIN_SENTENCE),
])
async def test_a_malformed_value_answers_its_sentence(world, field, value, sentence):
    db = _Db()
    async with _client(db) as client:
        response = await client.put(PATH, json={field: value})
    assert response.status_code == 422
    (item,) = response.json()["detail"]
    assert item["loc"] == ["body", field]
    assert item["msg"] == sentence
    assert db.row is None
