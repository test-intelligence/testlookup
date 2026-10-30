"""W23d AGT-15 (M-4 / M-4a): the log stage asks the metrics tool itself, against the fake Prometheus.

The stage is the REAL ``LogIntelligenceAgent.run`` with its lifecycle writes stubbed and ``investigate`` (the
Splunk trace / anomaly half, covered elsewhere) answering a complete verdict. The metrics half is real end to end:
the bound runtime config resolved from a saved ``integrations_config`` row, ``fetch_app_metrics``,
``app_metrics_service``, the shared client bound to ``tests/fakes/fake_integrations.py`` over
``httpx.ASGITransport``. Timestamps are built inside the tests.
"""
from __future__ import annotations

import socket
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agents.log_intelligence_agent import LogIntelligenceAgent
from app.core.config import settings
from app.services import app_metrics_service, integration_config_service, url_safety
from app.services.integration_config_service import clear_runtime_integration_cache
from app.services.workflow_step_context import workflow_step_scope
from app.tools.investigation_context import runtime_integrations
from tests.fakes.fake_integrations import FakeState, create_app

PROJECT = str(uuid.uuid4())
RUN = str(uuid.uuid4())
FAKE_BASE = "http://prometheus.fake.test:9090"
PROM_TOKEN = "prom_w23dFakeBearerSampleQx7Lm"
ROSE = "p95 latency for checkout rose 3.1× in the failure window"


class _Result:
    def __init__(self, row=None, first=None, rows=None):
        self._row, self._first, self._rows = row, first, rows or []

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        return self._first

    def all(self):
        return self._rows


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


@pytest.fixture
def world(monkeypatch):
    state = FakeState(prometheus_token=PROM_TOKEN)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    monkeypatch.setattr(app_metrics_service, "get_http_client", lambda: client)

    def getaddrinfo(host, *_a, **_k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)
    saved = {"prometheus_enabled": True, "prometheus_base_url": FAKE_BASE}
    secrets = {"prometheus_bearer_token": PROM_TOKEN}
    cases: list[tuple] = []
    statements: list[str] = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, stmt):
            text = str(stmt)
            statements.append(text)
            if "app_settings" in text:
                return _Result(row=SimpleNamespace(value=dict(saved)))
            if "test_cases" in text:
                return _Result(rows=list(cases))
            return _Result(first=(None, None))

    async def read_secret(_db, _scope, key):
        return secrets.get(key)

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _Session, raising=False)
    monkeypatch.setattr(integration_config_service, "read_secret", read_secret)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    return SimpleNamespace(state=state, saved=saved, secrets=secrets, cases=cases, statements=statements)


def _agent() -> LogIntelligenceAgent:
    agent = LogIntelligenceAgent()
    for name in ("mark_stage_running", "mark_stage_done", "log_decision"):
        setattr(agent, name, AsyncMock())
    agent.investigate = AsyncMock(return_value={
        "status": "complete",
        "distributed_trace": {"causal_summary": "checkout trace"},
        "log_anomaly": {"anomaly_detected": False},
        "log_summary": "Trace: checkout trace | Anomaly: none",
        "evidence_refs": [{"source": "log_anomaly", "ref_id": "checkout"}],
    })
    return agent


def _failed_at() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()


def _state(**overrides) -> dict:
    state = {
        "pipeline_run_id": "pipe-1",
        "project_id": PROJECT,
        "test_run_id": RUN,
        "analyses": {"tc-1": {"service_name": "checkout", "timestamp_utc": _failed_at()}},
        "failure_clusters": [],
        "test_run_data": {},
    }
    state.update(overrides)
    return state


def _queries(world) -> list[dict]:
    return [q for q in world.state.prometheus_queries if q["endpoint"] == "query_range"]


@pytest.mark.asyncio
async def test_the_stage_records_the_rise_and_a_prometheus_evidence_ref(world):
    delta = await _agent().run(_state())

    findings = delta["log_findings"]
    assert findings["status"] == "complete"
    metrics = findings["app_metrics"]
    assert (metrics["outcome"], metrics["service"]) == ("ok", "checkout")
    by_id = {s["template_id"]: s for s in metrics["series"]}
    assert by_id["http_p95_latency"]["sentence"] == ROSE
    assert 2 <= len(by_id["http_p95_latency"]["points"]) <= 120
    assert ROSE in findings["metrics_summary"]
    prometheus_refs = [r for r in findings["evidence_refs"] if r["source"] == "prometheus"]
    rose_ref = next(r for r in prometheus_refs if r["ref_id"] == "checkout:http_p95_latency")
    assert (rose_ref["strength"], rose_ref["contribution"], rose_ref["excerpt"]) == ("strong", 70, ROSE)
    # Four templates, each asked once with the token; every query names the escaped service.
    queries = _queries(world)
    assert len(queries) == 4
    assert {q["auth"] for q in queries} == {"ok"}
    assert {q["service"] for q in queries} == {"checkout"}
    # The binding ends with the stage; the token never reaches the workflow delta.
    assert runtime_integrations() is None
    assert PROM_TOKEN not in repr(delta)


@pytest.mark.asyncio
async def test_offline_mode_says_so_and_sends_nothing(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    delta = await _agent().run(_state())

    metrics = delta["log_findings"]["app_metrics"]
    assert (metrics["outcome"], metrics["sentence"]) == ("offline_mode", "Prometheus lookup disabled: offline mode")
    assert delta["log_findings"]["metrics_summary"] == "Prometheus lookup disabled: offline mode"
    assert world.state.prometheus_queries == []
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_a_narrowed_tool_list_is_tool_not_allowed_with_no_call(world):
    with workflow_step_scope("log_intelligence",
                             allowed_tools=["reconstruct_distributed_trace", "detect_log_rate_anomaly"]):
        delta = await _agent().run(_state())

    metrics = delta["log_findings"]["app_metrics"]
    assert metrics["outcome"] == "tool_not_allowed"
    assert metrics["sentence"] == (
        "metrics unavailable: the frozen agent configuration does not allow fetch_app_metrics")
    assert world.state.prometheus_queries == []


@pytest.mark.asyncio
async def test_nothing_to_scope_is_no_context_with_no_session(world):
    delta = await _agent().run(_state(analyses={}))

    findings = delta["log_findings"]
    assert findings["status"] == "not_enough_evidence"
    assert findings["app_metrics"]["outcome"] == "no_context"
    assert findings["app_metrics"]["sentence"] == "metrics unavailable: the failures name no service and failure time"
    assert world.statements == []
    assert world.state.prometheus_queries == []


@pytest.mark.asyncio
async def test_a_stored_service_name_scopes_the_lookup_when_no_analysis_names_one(world):
    """M-4a: the representative failure's TestCase.service_name and finished_at."""
    case_id = uuid.uuid4()
    world.cases.append((case_id, "checkout", datetime.now(timezone.utc) - timedelta(minutes=5)))
    delta = await _agent().run(_state(
        analyses={str(case_id): {"failure_category": "PRODUCT_BUG"}},
        failed_test_ids=[str(case_id)],
        failure_clusters=[{"cluster_id": "c1", "member_test_ids": [str(case_id)]}],
    ))

    findings = delta["log_findings"]
    assert findings["status"] == "not_enough_evidence", "the trace / anomaly verdict is unchanged"
    assert findings["app_metrics"]["outcome"] == "ok"
    assert ROSE in findings["metrics_summary"]
    assert any(r["source"] == "prometheus" for r in findings["evidence_refs"])
    case_selects = [s for s in world.statements if "test_cases" in s]
    assert len(case_selects) == 1 and "service_name IS NOT NULL" in case_selects[0]


@pytest.mark.asyncio
async def test_a_metrics_failure_never_changes_the_stage_status(world):
    world.secrets["prometheus_bearer_token"] = "prom_w23dWrongBearerSampleZz9"
    delta = await _agent().run(_state())

    findings = delta["log_findings"]
    assert findings["status"] == "complete"
    assert findings["app_metrics"]["outcome"] == "auth_error"
    assert findings["app_metrics"]["sentence"] == "metrics unavailable: Prometheus refused the token (HTTP 401)"
    assert not [r for r in findings["evidence_refs"] if r["source"] == "prometheus"]


@pytest.mark.asyncio
async def test_an_unconfigured_block_reads_not_configured(world):
    world.saved.pop("prometheus_base_url")
    delta = await _agent().run(_state())

    assert delta["log_findings"]["app_metrics"]["sentence"] == "metrics unavailable: Prometheus is not configured"
    assert delta["log_findings"]["status"] == "complete"
    assert world.state.prometheus_queries == []
