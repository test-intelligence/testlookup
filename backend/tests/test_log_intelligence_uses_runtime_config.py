"""W10 INT-01 / INT-03: the deep workflow's log stage searches with the saved Splunk settings.

``detect_log_rate_anomaly`` and ``reconstruct_distributed_trace`` are never
chosen by a model: ``LogIntelligenceAgent`` calls them server-side, where no
InvestigationContext is bound. The stage therefore binds the resolved runtime
config on the standalone ContextVar for its tools, and unbinds it when it ends
(the config carries tokens; it must not outlive the stage or enter workflow
state). Splunk is the shared fake over ``httpx.ASGITransport``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agents.log_intelligence_agent import LogIntelligenceAgent
from app.core.config import settings
from app.services import integration_config_service, url_safety
from app.services.integration_config_service import clear_runtime_integration_cache
from app.tools import detect_log_anomaly, reconstruct_trace
from app.tools.investigation_context import runtime_integrations
from tests.fakes.fake_integrations import FakeState, create_app

PROJECT = str(uuid.uuid4())
TS = "2026-10-20T10:00:00Z"


class _Result:
    def __init__(self, row=None, first=None):
        self._row, self._first = row, first

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        return self._first


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


@pytest.fixture
def world(monkeypatch):
    import socket

    state = FakeState()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    for module in (detect_log_anomaly, reconstruct_trace):
        monkeypatch.setattr(module, "get_http_client", lambda: client)

    def getaddrinfo(host, *_a, **_k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    opened: list[str] = []

    class _Session:
        async def __aenter__(self):
            opened.append("open")
            return self

        async def __aexit__(self, *_exc):
            opened.append("close")
            return False

        async def execute(self, stmt):
            if "app_settings" in str(stmt):
                return _Result(row=SimpleNamespace(value={
                    "splunk_enabled": True, "splunk_base_url": "http://splunk.fake.test:8089",
                }))
            return _Result(first=("payments", None))

    async def read_secret(_db, _scope, key):
        return state.splunk_token if key == "splunk_api_token" else None

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _Session, raising=False)
    monkeypatch.setattr(integration_config_service, "read_secret", read_secret)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    # The environment knows nothing of this Splunk: only the saved settings can reach it.
    monkeypatch.setattr(settings, "SPLUNK_ENABLED", False)
    monkeypatch.setattr(settings, "SPLUNK_BASE_URL", None)
    monkeypatch.setattr(settings, "SPLUNK_API_TOKEN", None)
    monkeypatch.setattr(settings, "SPLUNK_INDEX", "main")
    return SimpleNamespace(state=state, opened=opened)


def _agent() -> LogIntelligenceAgent:
    agent = LogIntelligenceAgent()
    for name in ("mark_stage_running", "mark_stage_done", "log_decision"):
        setattr(agent, name, AsyncMock())
    return agent


def _state(**overrides) -> dict:
    state = {
        "pipeline_run_id": "pipe-1",
        "project_id": PROJECT,
        "analyses": {"tc-1": {"service_name": "checkout", "timestamp_utc": TS}},
        "failure_clusters": [],
        "test_run_data": {},
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_the_stage_binds_the_resolved_config_for_its_splunk_tools(world):
    seen: list = []
    real = detect_log_anomaly._count_splunk_events

    async def spy(*args, **kwargs):
        seen.append(runtime_integrations())
        return await real(*args, **kwargs)

    detect_log_anomaly._count_splunk_events = spy
    try:
        delta = await _agent().run(_state())
    finally:
        detect_log_anomaly._count_splunk_events = real

    assert world.state.splunk_searches, "the stage made no search"
    assert {s["index"] for s in world.state.splunk_searches} == {"payments"}
    assert {s["auth"] for s in world.state.splunk_searches} == {"ok"}
    assert seen and all(cfg is not None and cfg.project_id == PROJECT for cfg in seen)
    assert runtime_integrations() is None, "the binding must not outlive the stage"
    assert world.opened == ["open", "close"], "one short resolver session, closed"
    findings = delta["log_findings"]
    assert findings["log_anomaly"]["levels"]["ERROR"]["current_count"] == 3
    # The tokens never reach the workflow delta.
    assert world.state.splunk_token not in repr(delta)


@pytest.mark.asyncio
async def test_the_binding_is_gone_even_when_the_stage_fails(world, monkeypatch):
    import app.agents.log_intelligence_agent as module

    def broken(_payload):
        assert runtime_integrations() is not None, "the failure happens inside the binding"
        raise RuntimeError("persistence sanitizer failed")

    monkeypatch.setattr(module, "sanitize_persistence_payload", broken)
    with pytest.raises(RuntimeError, match="persistence sanitizer failed"):
        await _agent().run(_state())
    assert runtime_integrations() is None


@pytest.mark.asyncio
async def test_a_stage_with_nothing_to_look_at_resolves_nothing(world):
    delta = await _agent().run(_state(analyses={}))

    assert world.opened == []
    assert world.state.requests == []
    assert delta["log_findings"]["status"] == "not_enough_evidence"
