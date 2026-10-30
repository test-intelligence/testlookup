"""W23d AGT-15 (M-1 / M-2): the Prometheus block on ``/settings/integrations``, its secret, its probe.

Driven through the real FastAPI routing (Pydantic validation, the role dependency, the handlers, the resolver)
over an in-memory ``integrations_config`` row; secrets are in memory; DNS is answered by a patched
``getaddrinfo``; the probe talks to ``tests/fakes/fake_integrations.py`` over ``httpx.ASGITransport``.
"""
from __future__ import annotations

import socket
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import UserRole
from app.routers import app_settings as router
from app.routers import integration_health
from app.services import integration_config_service, integration_probe_service, secret_service, url_safety
from app.services import prometheus_vocab as pv
from app.services.integration_config_service import clear_runtime_integration_cache
from tests.fakes.fake_integrations import FakeState, create_app

PATH = "/api/v1/settings/integrations"
PROBE = "/api/v1/integration-health/probe"
TOKEN = "prom_w23dSettingsBearerSampleQx7"
FAKE_BASE = "http://prometheus.fake.test:9090"
CUSTOM = {"id": "checkout_errors", "label": "Checkout errors", "unit": "per_second",
          "promql": 'sum(rate(errors_total{app="{service}"}[1m]))'}


class _Result:
    def __init__(self, row=None, keys=None):
        self._row, self._keys = row, keys or []

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._keys))


class _Db:
    """The ``integrations_config`` AppSetting row and the Vault-reference listing, in memory."""

    def __init__(self, value: dict | None = None, vault: tuple[str, ...] = ()):
        self.row = SimpleNamespace(value=dict(value), updated_by=None, is_secret_backed=False) \
            if value is not None else None
        self.vault = list(vault)

    async def execute(self, stmt):
        if "secret_refs" in str(stmt):
            return _Result(keys=self.vault)
        return _Result(row=self.row)

    def add(self, obj):
        self.row = obj

    async def flush(self):
        return None

    async def commit(self):
        return None


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


@pytest.fixture
def world(monkeypatch):
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

    def getaddrinfo(host, *_a, **_k):
        if host in ("localhost", "127.0.0.1"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    state = FakeState(prometheus_token=TOKEN)
    fake = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    store = AsyncMock(side_effect=store_secret)
    expire = AsyncMock(side_effect=expire_secret)
    persist = AsyncMock()
    monkeypatch.setattr(integration_config_service, "read_secret", read_secret)
    monkeypatch.setattr(router, "store_secret", store)
    monkeypatch.setattr(secret_service, "expire_secret", expire)
    monkeypatch.setattr(secret_service, "has_secret", has_secret)
    monkeypatch.setattr(router, "log_settings_change", AsyncMock())
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(integration_probe_service, "get_http_client", lambda: fake)
    monkeypatch.setattr(integration_probe_service, "persist_probe_results", persist)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    return SimpleNamespace(secrets=secrets, store=store, expire=expire, state=state, persist=persist)


def _client(db: _Db, role: UserRole = UserRole.ADMIN) -> AsyncClient:
    app = FastAPI()
    app.include_router(router.router)
    app.include_router(integration_health.router)
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)

    async def _db():
        yield db

    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── GET / PUT ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unsaved_block_reads_off_with_the_default_templates(world):
    async with _client(_Db(), role=UserRole.QA_LEAD) as client:
        body = (await client.get(PATH)).json()
    assert (body["prometheus_enabled"], body["prometheus_base_url"], body["prometheus_token_set"]) == (
        False, None, False)
    assert body["prometheus_templates_source"] == "default"
    assert [t["id"] for t in body["prometheus_metric_templates"]] == [t["id"] for t in pv.DEFAULT_METRIC_TEMPLATES]
    assert "prometheus_bearer_token" not in body


@pytest.mark.asyncio
async def test_an_admin_save_keeps_the_token_in_secret_refs_never_in_the_row(world):
    db = _Db({"splunk_enabled": True})
    async with _client(db) as client:
        response = await client.put(PATH, json={
            "prometheus_enabled": True, "prometheus_base_url": FAKE_BASE,
            "prometheus_bearer_token": TOKEN, "prometheus_metric_templates": [CUSTOM],
        })
    assert response.status_code == 200, response.text
    world.store.assert_awaited_once()
    assert world.store.await_args.args[1:4] == ("integrations_config", "prometheus_bearer_token", TOKEN)
    assert db.row.value == {
        "splunk_enabled": True, "prometheus_enabled": True, "prometheus_base_url": FAKE_BASE,
        "prometheus_metric_templates": [CUSTOM],
    }
    body = response.json()
    assert body["prometheus_token_set"] is True
    assert (body["prometheus_templates_source"], body["prometheus_metric_templates"]) == ("saved", [CUSTOM])
    assert TOKEN not in response.text
    assert TOKEN not in repr(db.row.value)


@pytest.mark.asyncio
async def test_an_empty_list_restores_the_defaults_and_an_empty_token_expires_it(world):
    world.secrets["prometheus_bearer_token"] = TOKEN
    db = _Db({"prometheus_enabled": True, "prometheus_metric_templates": [CUSTOM]})
    async with _client(db) as client:
        response = await client.put(PATH, json={"prometheus_metric_templates": [], "prometheus_bearer_token": ""})
    assert response.status_code == 200, response.text
    assert "prometheus_metric_templates" not in db.row.value
    world.expire.assert_awaited_once()
    body = response.json()
    assert (body["prometheus_templates_source"], body["prometheus_token_set"]) == ("default", False)


@pytest.mark.asyncio
async def test_a_loopback_url_is_refused_before_anything_is_written(world):
    db = _Db({"splunk_enabled": True})
    async with _client(db) as client:
        response = await client.put(PATH, json={"prometheus_base_url": "http://localhost:9090",
                                                "prometheus_bearer_token": TOKEN})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "prometheus_base_url"]
    world.store.assert_not_awaited()
    assert db.row.value == {"splunk_enabled": True}


@pytest.mark.asyncio
async def test_a_template_without_the_placeholder_is_refused(world):
    bad = dict(CUSTOM, promql="sum(rate(errors_total[1m]))")
    async with _client(_Db()) as client:
        response = await client.put(PATH, json={"prometheus_metric_templates": [bad]})
    assert response.status_code == 422
    assert pv.TEMPLATE_ERRORS["placeholder"] in response.text
    world.store.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_qa_lead_reads_but_cannot_save(world):
    db = _Db()
    async with _client(db, role=UserRole.QA_LEAD) as client:
        assert (await client.get(PATH)).status_code == 200
        response = await client.put(PATH, json={"prometheus_enabled": True})
    assert response.status_code == 403
    assert db.row is None


@pytest.mark.asyncio
async def test_a_vault_reference_is_labelled(world):
    world.secrets["prometheus_bearer_token"] = "resolved-from-vault"
    async with _client(_Db({}, vault=("prometheus_bearer_token", "jira_api_token"))) as client:
        body = (await client.get(PATH)).json()
    assert "prometheus_bearer_token" in body["vault_fields"]
    assert body["prometheus_token_set"] is True


@pytest.mark.asyncio
async def test_a_hand_edited_row_never_sends_a_bad_template(world):
    no_placeholder = dict(CUSTOM, id="no_service", promql="sum(up)")
    control = dict(CUSTOM, id="with_control", promql='up{app="{service}"}\x07')
    db = _Db({"prometheus_metric_templates": [CUSTOM, dict(CUSTOM), no_placeholder, control, "junk"]})
    resolved = await integration_config_service.resolve_prometheus_settings(db)
    assert (resolved["prometheus_metric_templates"], resolved["prometheus_templates_source"]) == ([CUSTOM], "saved")
    only_bad = await integration_config_service.resolve_prometheus_settings(
        _Db(), overrides={"prometheus_metric_templates": [no_placeholder]})
    assert only_bad["prometheus_templates_source"] == "default"


# ── The probe ────────────────────────────────────────────────────────────────


def _probe_cfg(**overrides) -> dict:
    cfg = {"prometheus_enabled": True, "prometheus_base_url": FAKE_BASE, "prometheus_bearer_token": TOKEN}
    cfg.update(overrides)
    return cfg


@pytest.mark.asyncio
async def test_the_probe_answers_healthy_auth_error_down_and_skipped(world, monkeypatch):
    healthy = await integration_probe_service.probe_prometheus(_probe_cfg())
    assert (healthy.status, healthy.message) == ("healthy", "Prometheus API reachable")
    refused = await integration_probe_service.probe_prometheus(_probe_cfg(prometheus_bearer_token="prom_w23dWrongQz"))
    assert refused.status == "auth_error"
    blocked = await integration_probe_service.probe_prometheus(_probe_cfg(prometheus_base_url="http://127.0.0.1:9090"))
    assert (blocked.status, blocked.message) == ("down", "Target host is not allowed (blocked_target:127.0.0.1)")
    for cfg in (_probe_cfg(prometheus_enabled=False), _probe_cfg(prometheus_base_url=None), {}):
        skipped = await integration_probe_service.probe_prometheus(cfg)
        assert (skipped.status, skipped.message) == ("skipped", "Prometheus is switched off or has no URL")
    assert [q["auth"] for q in world.state.prometheus_queries] == ["ok", "bad"]

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    world.state.reset()
    offline = await integration_probe_service.probe_prometheus(_probe_cfg())
    assert offline.status == "skipped" and "AI_OFFLINE_MODE=true" in offline.message
    assert world.state.prometheus_queries == []


@pytest.mark.asyncio
async def test_a_probe_timeout_is_timeout(world, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(integration_probe_service, "get_http_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    result = await integration_probe_service.probe_prometheus(_probe_cfg())
    assert result.status == "timeout"


@pytest.mark.asyncio
async def test_the_on_demand_probe_resolves_the_saved_block(world):
    world.secrets["prometheus_bearer_token"] = TOKEN
    db = _Db({"prometheus_enabled": True, "prometheus_base_url": FAKE_BASE})
    async with _client(db, role=UserRole.QA_LEAD) as client:
        response = await client.post(PROBE, params={"provider": "prometheus"})
        unknown = await client.post(PROBE, params={"provider": "prometheuss"})
    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert (row["provider"], row["status"]) == ("prometheus", "healthy")
    world.persist.assert_awaited_once()
    assert unknown.status_code == 422
    assert "prometheus" in unknown.json()["detail"].split("Supported: ")[1].split(", ")


@pytest.mark.asyncio
async def test_the_fifteen_minute_beat_probes_prometheus_with_the_configurable_probes(world, monkeypatch):
    db = _Db({})

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    async def splunk(_cfg):
        return integration_probe_service.ProbeResult("splunk", "skipped")

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _Session, raising=False)
    monkeypatch.setattr(integration_probe_service, "ALL_PROBES", {"splunk": splunk})
    results = await integration_probe_service.run_all_probes()
    by = {r.provider: r for r in results}
    assert set(by) == {"splunk", "prometheus"}
    assert (by["prometheus"].status, by["prometheus"].message) == (
        "skipped", "Prometheus is switched off or has no URL")
