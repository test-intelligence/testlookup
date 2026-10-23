"""W10 INT-01 / INT-03: the four Splunk / OpenShift tools read the runtime config, through the fake.

Every outbound call goes to ``tests/fakes/fake_integrations.py`` over
``httpx.ASGITransport``; the fake records the decoded SPL (and its index) and
whether the right credential was sent (``auth``), never the credential.

* a base URL, a token and a project index SAVED in the UI (resolved by
  ``resolve_runtime_integrations`` from the ``integrations_config`` row, the
  secret store and the project's row) reach the request, over different
  environment values;
* with nothing bound the tools use the environment;
* offline mode sends nothing from any of the four tools;
* a loopback base URL, an index that is not SPL-safe in the environment and
  a pod name like ``../secrets`` send nothing and say why;
* ``detect_log_rate_anomaly`` makes at most 16 searches behind ONE egress check.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.services import integration_config_service, ocp_client, url_safety
from app.services.integration_config_service import (
    IntegrationRuntimeConfig,
    clear_runtime_integration_cache,
    resolve_runtime_integrations,
)
from app.tools import detect_log_anomaly, query_splunk, reconstruct_trace
from app.tools.analyze_ocp import analyze_openshift_pod_events
from app.tools.investigation_context import (
    InvestigationContext,
    bind_runtime_integrations,
    reset_investigation_context,
    reset_runtime_integrations,
    set_investigation_context,
)
from tests.fakes.fake_integrations import FakeState, create_app

TS = "2026-10-20T10:00:00Z"
PROJECT = str(uuid.uuid4())
SPLUNK_SAVED = "http://splunk.fake.test:8089"
OCP_SAVED = "http://ocp.fake.test:6443"
POD = "payment-runner-7d9f"


def _resolver(mapping: dict[str, str]):
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
def fake(monkeypatch):
    state = FakeState()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    for module in (query_splunk, detect_log_anomaly, reconstruct_trace, ocp_client):
        monkeypatch.setattr(module, "get_http_client", lambda: client)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _resolver({"loopback.fake.test": "127.0.0.1"}))
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    # The environment points elsewhere, with credentials the fake refuses.
    monkeypatch.setattr(settings, "SPLUNK_ENABLED", False)
    monkeypatch.setattr(settings, "SPLUNK_BASE_URL", "http://env-splunk.fake.test:8089")
    monkeypatch.setattr(settings, "SPLUNK_API_TOKEN", "env-splunk-token")
    monkeypatch.setattr(settings, "SPLUNK_INDEX", "main")
    monkeypatch.setattr(settings, "OCP_ENABLED", False)
    monkeypatch.setattr(settings, "OCP_API_URL", "http://env-ocp.fake.test:6443")
    monkeypatch.setattr(settings, "OCP_SA_TOKEN", "env-ocp-token")
    monkeypatch.setattr(settings, "OCP_DEFAULT_NAMESPACE", "qa-testing")
    return state


class _Result:
    def __init__(self, row=None, first=None):
        self._row, self._first = row, first

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        return self._first


class _SavedWorld:
    """The rows ``resolve_runtime_integrations`` reads: the saved settings and the project."""

    def __init__(self, saved: dict, project_row: tuple):
        self.saved, self.project_row = saved, project_row

    async def execute(self, stmt):
        if "app_settings" in str(stmt):
            return _Result(row=SimpleNamespace(value=dict(self.saved)))
        return _Result(first=self.project_row)


async def _saved_config(monkeypatch, state: FakeState, **saved_overrides) -> IntegrationRuntimeConfig:
    secrets = {"splunk_api_token": state.splunk_token, "ocp_sa_token": state.ocp_token}

    async def read_secret(_db, _scope, key):
        return secrets.get(key)

    monkeypatch.setattr(integration_config_service, "read_secret", read_secret)
    saved = {"splunk_enabled": True, "splunk_base_url": SPLUNK_SAVED, "ocp_enabled": True, "ocp_api_url": OCP_SAVED}
    saved.update(saved_overrides)
    return await resolve_runtime_integrations(_SavedWorld(saved, ("payments", "qa-payments")), PROJECT)


def _context(cfg=None, **overrides) -> InvestigationContext:
    values = dict(
        project_id=PROJECT, run_id=str(uuid.uuid4()), test_case_id=str(uuid.uuid4()), test_name="test_pay",
        service_name="checkout", timestamp=TS, ocp_pod_name=POD, ocp_namespace="qa-payments", integrations=cfg,
    )
    values.update(overrides)
    return InvestigationContext(**values)


async def _splunk_tool(context: InvestigationContext) -> str:
    token = set_investigation_context(context)
    try:
        return await query_splunk.query_splunk_logs.ainvoke(
            {"service_name": context.service_name, "timestamp_utc": context.timestamp}
        )
    finally:
        reset_investigation_context(token)


async def _ocp_tool(context: InvestigationContext) -> str:
    token = set_investigation_context(context)
    try:
        return await analyze_openshift_pod_events.ainvoke(
            {"pod_name": context.ocp_pod_name, "namespace": context.ocp_namespace, "timestamp_utc": context.timestamp}
        )
    finally:
        reset_investigation_context(token)


async def _log_stage_tools(cfg) -> tuple[dict, dict]:
    """The two tools the log stage calls server-side, under its standalone binding."""
    token = bind_runtime_integrations(cfg)
    try:
        anomaly = await detect_log_anomaly.detect_log_rate_anomaly.ainvoke({"params_json": json.dumps(
            {"service_name": "checkout", "timestamp_utc": TS, "window_minutes": 10, "baseline_days": 7}
        )})
        trace = await reconstruct_trace.reconstruct_distributed_trace.ainvoke({"params_json": json.dumps(
            {"timestamp_utc": TS, "services": ["checkout"], "correlation_id": "", "window_seconds": 45}
        )})
    finally:
        reset_runtime_integrations(token)
    return json.loads(anomaly), json.loads(trace)


# ── The saved settings and the project's index reach the request ───────────


@pytest.mark.asyncio
async def test_the_ui_saved_splunk_settings_and_the_project_index_reach_the_search(fake, monkeypatch):
    cfg = await _saved_config(monkeypatch, fake)
    assert cfg.splunk_index == "payments" and cfg.splunk_index_source == "project"

    result = await _splunk_tool(_context(cfg))

    search = fake.splunk_searches[-1]
    assert search["index"] == "payments"
    assert search["auth"] == "ok", "the saved token, not the environment's"
    assert search["services"] == ["checkout"]
    (request,) = [r for r in fake.requests if r["service"] == "splunk"]
    assert request["path"] == "/services/search/jobs"
    assert result.startswith("=== Splunk Log Correlation (3 entries) ===")
    assert "db pool exhausted after 30000 ms" in result


@pytest.mark.asyncio
async def test_the_log_stage_tools_search_the_project_index_with_the_saved_token(fake, monkeypatch):
    cfg = await _saved_config(monkeypatch, fake)
    anomaly, trace = await _log_stage_tools(cfg)

    assert fake.splunk_searches, "the stage tools made no search"
    assert {s["index"] for s in fake.splunk_searches} == {"payments"}
    assert {s["auth"] for s in fake.splunk_searches} == {"ok"}
    assert "error" not in anomaly and "levels" in anomaly
    assert anomaly["levels"]["ERROR"]["current_count"] == 3
    assert any("db pool exhausted" in step["message"] for step in trace["trace_steps"])


@pytest.mark.asyncio
async def test_the_ui_saved_openshift_settings_reach_the_pod_lookup(fake, monkeypatch):
    cfg = await _saved_config(monkeypatch, fake)
    result = await _ocp_tool(_context(cfg))

    ocp = [r for r in fake.requests if r["service"] == "ocp"]
    assert [r["path"] for r in ocp] == [
        f"/api/v1/namespaces/qa-payments/pods/{POD}", "/api/v1/namespaces/qa-payments/events",
    ]
    assert {r["auth"] for r in ocp} == {"ok"}
    assert result.startswith(f"Pod: {POD} | Namespace: qa-payments | Phase: Running")
    assert "OOMKilled" in result


@pytest.mark.asyncio
async def test_with_nothing_bound_the_tools_use_the_environment(fake, monkeypatch):
    monkeypatch.setattr(settings, "SPLUNK_ENABLED", True)
    monkeypatch.setattr(settings, "SPLUNK_API_TOKEN", fake.splunk_token)

    result = await _splunk_tool(_context(None))

    (search,) = fake.splunk_searches
    assert search["index"] == "main" and search["auth"] == "ok"
    assert "No backend errors found in Splunk for service 'checkout'" in result  # main holds WARN only


# ── Nothing is sent when it must not be ─────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_mode_sends_nothing_from_any_of_the_four_tools(fake, monkeypatch):
    cfg = await _saved_config(monkeypatch, fake)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    splunk = await _splunk_tool(_context(cfg))
    pod = await _ocp_tool(_context(cfg))
    anomaly, trace = await _log_stage_tools(cfg)

    assert fake.requests == []
    assert fake.splunk_searches == []
    assert "AI_OFFLINE_MODE=true" in splunk
    assert "Phase" not in pod
    assert "disabled" in anomaly["assessment"].lower()
    assert "disabled" in trace["causal_summary"].lower()


@pytest.mark.asyncio
async def test_a_loopback_base_is_refused_before_the_token_is_sent(fake, monkeypatch):
    cfg = await _saved_config(
        monkeypatch, fake, splunk_base_url="http://loopback.fake.test:8089", ocp_api_url="http://127.0.0.1:6443",
    )

    splunk = await _splunk_tool(_context(cfg))
    pod = await _ocp_tool(_context(cfg))
    anomaly, trace = await _log_stage_tools(cfg)

    assert fake.requests == []
    assert splunk == "Splunk lookup refused: Target host is not allowed."
    assert pod == "OpenShift lookup refused: Target host is not allowed."
    assert anomaly == {"anomaly_detected": False, "assessment": "Splunk lookup refused: Target host is not allowed."}
    assert trace["causal_summary"] == "Splunk lookup refused: Target host is not allowed."


@pytest.mark.asyncio
async def test_an_index_that_is_not_spl_safe_is_refused(fake, monkeypatch):
    monkeypatch.setattr(settings, "SPLUNK_ENABLED", True)
    monkeypatch.setattr(settings, "SPLUNK_API_TOKEN", fake.splunk_token)
    monkeypatch.setattr(settings, "SPLUNK_INDEX", "main | delete")

    splunk = await _splunk_tool(_context(None))
    anomaly, trace = await _log_stage_tools(None)

    assert fake.requests == []
    assert splunk == "Splunk lookup refused: the Splunk index is not valid."
    assert anomaly["assessment"] == "Splunk index is not valid — cannot measure log rate anomaly."
    assert trace["causal_summary"] == "Splunk index is not valid — cannot reconstruct the distributed trace."


@pytest.mark.asyncio
@pytest.mark.parametrize("pod,namespace", [("../secrets", "qa-payments"), (POD, "QA Payments")])
async def test_a_pod_or_namespace_that_is_not_a_dns_name_sends_nothing(fake, monkeypatch, pod, namespace):
    cfg = await _saved_config(monkeypatch, fake)
    result = await _ocp_tool(_context(cfg, ocp_pod_name=pod, ocp_namespace=namespace))

    assert fake.requests == []
    assert result == "OpenShift lookup refused: the namespace or pod name is not valid."


@pytest.mark.asyncio
async def test_a_service_list_cannot_inject_spl(fake, monkeypatch):
    cfg = await _saved_config(monkeypatch, fake)
    token = bind_runtime_integrations(cfg)
    try:
        await reconstruct_trace.reconstruct_distributed_trace.ainvoke({"params_json": json.dumps({
            "timestamp_utc": TS, "services": ['checkout") OR index=* ("'], "correlation_id": 'x" | delete',
        })})
    finally:
        reset_runtime_integrations(token)
    (search,) = fake.splunk_searches
    assert search["index"] == "payments"
    assert "| delete" not in search["search"]
    assert "index=*" not in search["search"]


@pytest.mark.asyncio
async def test_the_anomaly_tool_gates_once_before_at_most_16_searches(fake, monkeypatch):
    cfg = await _saved_config(monkeypatch, fake)
    checks: list[str] = []
    real_check = url_safety.egress_block_reason

    async def counting(url):
        checks.append(url)
        return await real_check(url)

    monkeypatch.setattr(detect_log_anomaly, "egress_block_reason", counting)
    token = bind_runtime_integrations(cfg)
    try:
        await detect_log_anomaly.detect_log_rate_anomaly.ainvoke({"params_json": json.dumps(
            {"service_name": "checkout", "timestamp_utc": TS, "window_minutes": 10, "baseline_days": 7}
        )})
    finally:
        reset_runtime_integrations(token)

    assert checks == [SPLUNK_SAVED]
    splunk_requests = [r for r in fake.requests if r["service"] == "splunk"]
    assert len(splunk_requests) == 16  # 2 levels x (1 window + 7 baseline days)
    assert {r["path"] for r in splunk_requests} == {"/services/search/jobs/export"}
