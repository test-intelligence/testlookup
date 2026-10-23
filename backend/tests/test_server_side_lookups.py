"""W10 D-4: the server runs the Splunk / OpenShift lookups before the triage model is called.

Small models skip tools (mistral-nemo, the homelab's analysis class) and a
stack without a reachable model never calls one, so the evidence the
investigation context authorizes is fetched by the server, attached to the
question, memoised for the tools and kept on every exit of the agent run.

``run_triage_agent`` is driven here through the REAL ``BudgetedLLM`` wrapper
around a scripted ``FakeListChatModel`` (the
``test_triage_react_runs_through_the_budget_wrapper`` pattern; never a mocked
``get_llm`` past the wrapper). The runtime config is resolved by the REAL
resolver on a recording session factory; Splunk is the shared fake over
``httpx.ASGITransport``. An ordering recorder proves the resolver's session is
closed before any HTTP and before the provider is called.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.language_models import FakeListChatModel

import app.services.agent as triage
from app.core.config import settings
from app.services import integration_config_service, llm_circuit_breaker, llm_cost_reservation as cost, url_safety
from app.services.integration_config_service import clear_runtime_integration_cache
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import BudgetedLLM
from app.tools import query_splunk
from tests.fakes.fake_integrations import FakeState, create_app

TS = "2026-10-20T10:00:00Z"
PROJECT = str(uuid.uuid4())
RUN = str(uuid.uuid4())
SPLUNK = "http://splunk.fake.test:8089"
HEADER = "Server-side lookups already ran (do not call these tools again):"

FINAL = json.dumps({
    "root_cause_summary": "The payment DB pool was exhausted during checkout.",
    "failure_category": "INFRASTRUCTURE",
    "backend_error_found": True,
    "pod_issue_found": False,
    "is_flaky": False,
    "confidence_score": 81,
    "recommended_actions": ["Raise the DB pool size"],
    "role_actions": {},
    "evidence_references": [],
})
ANSWER = f"Thought: I now know the final answer\nFinal Answer: {FINAL}"
CALLS_SPLUNK = (
    "Thought: check the backend logs\nAction: query_splunk_logs\n"
    f'Action Input: {{"service_name": "checkout", "timestamp_utc": "{TS}"}}'
)


def _resolver():
    def getaddrinfo(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]

    return getaddrinfo


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, events: list[str]):
        self._inner, self._events = inner, events

    async def handle_async_request(self, request):
        self._events.append(f"http:{request.url.path}")
        return await self._inner.handle_async_request(request)


class _Result:
    def __init__(self, row=None, first=None):
        self._row, self._first = row, first

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        return self._first


def _session_factory(events: list[str]):
    """``AsyncSessionLocal`` for the resolver: the saved Splunk settings and the project's index."""

    class _Session:
        async def __aenter__(self):
            events.append("session_open")
            return self

        async def __aexit__(self, *_exc):
            events.append("session_close")
            return False

        async def execute(self, stmt):
            if "app_settings" in str(stmt):
                return _Result(row=SimpleNamespace(value={"splunk_enabled": True, "splunk_base_url": SPLUNK}))
            return _Result(first=("payments", None))

    return _Session


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_runtime_integration_cache()
    yield
    clear_runtime_integration_cache()


@pytest.fixture
def world(monkeypatch):
    events: list[str] = []
    state = FakeState()
    client = httpx.AsyncClient(transport=_RecordingTransport(httpx.ASGITransport(app=create_app(state)), events))
    monkeypatch.setattr(query_splunk, "get_http_client", lambda: client)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _resolver())
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _session_factory(events), raising=False)

    async def read_secret(_db, _scope, key):
        return state.splunk_token if key == "splunk_api_token" else None

    monkeypatch.setattr(integration_config_service, "read_secret", read_secret)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    for name, value in {"SPLUNK_ENABLED": False, "SPLUNK_BASE_URL": None, "SPLUNK_API_TOKEN": None,
                        "OCP_ENABLED": False, "OCP_API_URL": None, "SPLUNK_INDEX": "main"}.items():
        monkeypatch.setattr(settings, name, value)

    # The wrapper's gates, stubbed at their stores only.
    async def available(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        return Reservation(key="k", project_id="p", estimated_usd=0.01, ttl_seconds=60)

    async def nothing(*_a, **_k):
        return None

    async def passes(score):
        return {"passed": True, "threshold": 70, "score": score}

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", available)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(nothing))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(nothing))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", nothing)
    monkeypatch.setattr(triage, "_store_audit_trail", AsyncMock())
    monkeypatch.setattr(triage, "_emit_event", AsyncMock())
    monkeypatch.setattr("app.services.confidence_gate.check_confidence", passes)
    return SimpleNamespace(state=state, events=events, prompts=[])


def _model(world, responses: list[str], *, fail: Exception | None = None, provider: str = "ollama") -> BudgetedLLM:
    prompts, events = world.prompts, world.events

    class Scripted(FakeListChatModel):
        async def ainvoke(self, input, config=None, **kwargs):
            events.append("provider")
            prompts.append(str(input))
            if fail is not None:
                raise fail
            return await super().ainvoke(input, config, **kwargs)

    return BudgetedLLM(Scripted(responses=responses), provider=provider, model="scripted")


async def _triage(monkeypatch, llm, **overrides) -> dict:
    async def get_llm(*_a, **_k):
        return llm

    monkeypatch.setattr(triage, "get_llm", get_llm)
    kwargs = dict(
        test_case_id="tc-1", test_name="test_checkout_pays", service_name="checkout", timestamp=TS,
        error_message="AssertionError: expected 200, got 503", run_id=RUN, project_id=PROJECT,
        skip_fast_classifier=True, skip_cache=True,
    )
    kwargs.update(overrides)
    return await triage.run_triage_agent(**kwargs)


def _excerpts(analysis: dict) -> list[str]:
    return [ref["excerpt"] for ref in analysis.get("evidence_references") or []]


@pytest.mark.asyncio
async def test_a_model_that_calls_no_tool_still_gets_the_splunk_evidence(world, monkeypatch):
    analysis = await _triage(monkeypatch, _model(world, [ANSWER]))

    assert analysis["_routing"]["mode_used"] == "llm"
    assert analysis["failure_category"] == "INFRASTRUCTURE"
    assert analysis["tools_used"] == ["query_splunk_logs"]
    assert any("db pool exhausted" in excerpt for excerpt in _excerpts(analysis))
    (search,) = world.state.splunk_searches
    assert search["index"] == "payments" and search["auth"] == "ok"
    # The model was told what the server found.
    assert HEADER in world.prompts[0]
    assert "- query_splunk_logs: === Splunk Log Correlation (3 entries) ===" in world.prompts[0]


@pytest.mark.asyncio
async def test_the_resolver_session_is_closed_before_any_http_and_before_the_model(world, monkeypatch):
    await _triage(monkeypatch, _model(world, [ANSWER]))

    events = world.events
    assert events[:2] == ["session_open", "session_close"]
    assert events.count("session_open") == 1, "one resolve per analysis"
    first_http = events.index("http:/services/search/jobs")
    assert events.index("session_close") < first_http < events.index("provider")


@pytest.mark.asyncio
async def test_a_repeated_tool_call_answers_from_the_memo(world, monkeypatch):
    analysis = await _triage(monkeypatch, _model(world, [CALLS_SPLUNK, ANSWER]))

    assert len(world.state.splunk_searches) == 1, "the memo must stop a second search"
    assert analysis["tools_used"] == ["query_splunk_logs"]
    assert analysis["failure_category"] == "INFRASTRUCTURE"


@pytest.mark.asyncio
async def test_a_failed_model_call_still_carries_the_server_side_evidence(world, monkeypatch):
    llm = _model(world, ["unused"], fail=httpx.ConnectError("[Errno 111] Connection refused"))
    analysis = await _triage(monkeypatch, llm)

    assert analysis["_routing"]["fallback_from"] == "llm"
    assert analysis["_routing"]["fallback_reason"] == "llm_error"
    assert analysis["tools_used"] == ["query_splunk_logs"]
    assert any("db pool exhausted" in excerpt for excerpt in _excerpts(analysis))
    assert len(world.state.splunk_searches) == 1


@pytest.mark.asyncio
async def test_a_get_llm_failure_propagates_and_no_lookup_is_made(world, monkeypatch):
    async def broken(*_a, **_k):
        raise RuntimeError("model construction failed")

    monkeypatch.setattr(triage, "get_llm", broken)
    with pytest.raises(RuntimeError, match="model construction failed"):
        await triage.run_triage_agent(
            test_case_id="tc-1", test_name="t", service_name="checkout", timestamp=TS,
            error_message="boom", run_id=RUN, project_id=PROJECT, skip_fast_classifier=True, skip_cache=True,
        )
    assert world.events == [], "nothing resolved, nothing sent"
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_offline_mode_makes_no_request_and_no_server_step(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    analysis = await _triage(monkeypatch, _model(world, [ANSWER]))

    assert world.state.requests == []
    assert analysis["tools_used"] == []
    assert not any("Splunk" in excerpt for excerpt in _excerpts(analysis))
    assert HEADER not in world.prompts[0]


@pytest.mark.asyncio
async def test_without_a_run_nothing_is_resolved_or_looked_up(world, monkeypatch):
    analysis = await _triage(monkeypatch, _model(world, [ANSWER]), run_id=None)

    assert "session_open" not in world.events
    assert world.state.requests == []
    assert analysis["tools_used"] == []


@pytest.mark.asyncio
async def test_a_tool_grant_without_splunk_skips_the_lookup(world, monkeypatch):
    from app.services.workflow_step_context import workflow_step_scope

    with workflow_step_scope("root_cause_analysis", allowed_tools=["check_test_flakiness"]):
        analysis = await _triage(monkeypatch, _model(world, [ANSWER]))
    assert world.state.requests == []
    assert analysis["tools_used"] == []
