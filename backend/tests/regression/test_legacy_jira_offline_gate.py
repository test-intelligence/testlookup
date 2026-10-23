"""W10 INT-01 (contract §0.5, D-7): the legacy Jira path refuses offline, and reads the saved Jira.

``jira_client.create_jira_issue`` -- behind ``POST /api/v1/integrations/jira``
and the triage stage's tickets -- had NO ``AI_OFFLINE_MODE`` gate: an
air-gapped deployment POSTed failure text and an AI summary to
``https://{JIRA_DOMAIN}``. It also read only the environment (so the Jira
saved in Settings > Integrations was ignored there), built that URL with no
egress check, and re-POSTed a create after a read timeout or a 5xx (a second
ticket). Pinned here, against the shared fake over ``httpx.ASGITransport``:

* offline -> 503 with the offline sentence, and the fake saw NOTHING;
* online with the Jira saved in the UI -> the ticket, on the saved domain;
* a 5xx is not re-POSTed; a connect error (never sent) is retried;
* the triage stage offline files no ticket and makes no request;
* a loopback Jira host -> ``JiraUnavailable("target_not_allowed")``.
"""
from __future__ import annotations

import ipaddress
import socket
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.db.postgres import get_db
from app.models.postgres import TestCase as CaseRow
from app.routers import integrations as integrations_router
from app.services import jira_client, url_safety
from app.services.action_policy import ActionStatus
from app.services.jira_client import LEGACY_JIRA_REASON_TEXT, JiraUnavailable
from tests.fakes.fake_integrations import FakeState, create_app

pytestmark = pytest.mark.regression

DOMAIN = "fake-jira.example.test"
PROJECT = uuid.uuid4()


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


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RequestSession:
    """The route's session: the test case, its run's project, no AI analysis,
    and the ``integrations_config`` row the Jira settings were saved in."""

    def __init__(self, tc: CaseRow, saved: dict):
        self.tc, self.saved = tc, saved
        self.new, self.dirty, self.deleted = [], [], []
        self.closed = 0

    async def execute(self, stmt, *args, **kwargs):
        tables = {t.name for t in stmt.get_final_froms()}
        if tables == {"test_cases"}:
            return _Result(self.tc)
        if tables == {"test_runs"}:
            return _Result(PROJECT)
        if tables == {"app_settings"}:
            return _Result(SimpleNamespace(value=dict(self.saved)))
        return _Result(None)

    async def close(self):
        self.closed += 1


@pytest.fixture
def fake(monkeypatch):
    state = FakeState(base_url=f"https://{DOMAIN}")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    monkeypatch.setattr(jira_client, "get_http_client", lambda: client)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _resolver({"localhost": "127.0.0.1"}))
    monkeypatch.setattr("app.services.resilience.asyncio.sleep", AsyncMock())
    # The environment holds no usable Jira: only the saved settings can reach the fake.
    monkeypatch.setattr(settings, "JIRA_ENABLED", False)
    monkeypatch.setattr(settings, "JIRA_DOMAIN", None)
    monkeypatch.setattr(settings, "JIRA_EMAIL", None)
    monkeypatch.setattr(settings, "JIRA_API_TOKEN", None)

    async def read_secret(_db, _scope, key):
        return state.jira_token if key == "jira_api_token" else None

    monkeypatch.setattr("app.services.secret_service.read_secret", read_secret)
    return state


def _saved(state: FakeState) -> dict:
    return {"jira_enabled": True, "jira_domain": DOMAIN, "jira_email": state.jira_email,
            "jira_default_project_key": "QA"}


def _cfg(state: FakeState, **overrides) -> dict:
    cfg = {"enabled": True, "domain": DOMAIN, "email": state.jira_email, "api_token": state.jira_token,
           "default_project_key": "QA"}
    cfg.update(overrides)
    return cfg


def _app(session: _RequestSession) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router.router)
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")

    async def _db():
        yield session

    for route in app.routes:
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
            if dependency.call is not get_db:
                app.dependency_overrides[dependency.call] = lambda: user
    app.dependency_overrides[get_db] = _db
    return app


async def _post(monkeypatch, state: FakeState):
    tc = CaseRow(id=uuid.uuid4(), test_run_id=uuid.uuid4(), test_name="test_checkout", status="FAILED",
                 error_message="AssertionError: expected 200, got 503")
    session = _RequestSession(tc, _saved(state))
    monkeypatch.setattr(integrations_router, "resolve_project_scope", AsyncMock())
    monkeypatch.setattr(integrations_router, "check_jira_ticket_creation_policy", AsyncMock(return_value={
        "requires_approval": False, "initial_status": ActionStatus.APPROVED, "policy_reasons": [],
    }))
    async with AsyncClient(transport=ASGITransport(app=_app(session)), base_url="http://test") as client:
        response = await client.post("/api/v1/integrations/jira", json={
            "project_key": "QA", "test_case_id": str(tc.id), "test_name": tc.test_name,
            "run_id": str(tc.test_run_id), "ai_summary": "Checkout fails with a 503",
            "recommended_action": "Fix the gateway timeout",
        })
    return response, session


def _creates(state: FakeState) -> list[dict]:
    return [r for r in state.requests if r["method"] == "POST" and r["path"] == "/rest/api/3/issue"]


@pytest.mark.asyncio
async def test_offline_mode_refuses_with_503_and_sends_nothing(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    response, _ = await _post(monkeypatch, fake)

    assert response.status_code == 503
    assert response.json()["detail"] == LEGACY_JIRA_REASON_TEXT["offline_mode"]
    assert fake.requests == []
    assert fake.issues == {}


@pytest.mark.asyncio
async def test_online_the_ticket_is_filed_on_the_jira_saved_in_the_ui(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    response, session = await _post(monkeypatch, fake)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticket_key"] == "QA-1"
    assert body["ticket_url"] == "https://fake-jira.example.test/browse/QA-1"
    (create,) = _creates(fake)
    assert create["authorized"] is True, "the saved email and token were sent"
    assert session.closed == 1, "the request's read connection was handed back before the call"


@pytest.mark.asyncio
async def test_an_unconfigured_saved_jira_answers_its_sentence(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def no_secret(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.secret_service.read_secret", no_secret)
    response, _ = await _post(monkeypatch, fake)
    assert response.status_code == 503
    assert response.json()["detail"] == LEGACY_JIRA_REASON_TEXT["not_configured"]
    assert fake.requests == []


@pytest.mark.asyncio
async def test_a_5xx_is_not_re_posted(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    fake.faults.append({"method": "POST", "path": "/rest/api/3/issue", "status": 500, "times": 3})
    response, _ = await _post(monkeypatch, fake)

    assert response.status_code == 500
    assert response.json()["detail"].startswith("Jira API error")
    assert fake.jira_token not in response.text
    assert len(_creates(fake)) == 1, "a create that may have succeeded must not be sent again"


@pytest.mark.asyncio
async def test_a_connect_error_is_retried_because_nothing_was_sent(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    inner = httpx.ASGITransport(app=create_app(fake))
    attempts: list[str] = []

    class _FlakyConnect(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            attempts.append(request.url.path)
            if len(attempts) == 1:
                raise httpx.ConnectError("connection refused", request=request)
            return await inner.handle_async_request(request)

    monkeypatch.setattr(jira_client, "get_http_client", lambda: httpx.AsyncClient(transport=_FlakyConnect()))
    ticket = await jira_client.create_jira_issue(
        "QA", "test_checkout", "run-1", "summary", "action", "trace", "https://testlookup/x", cfg=_cfg(fake),
    )
    assert ticket["ticket_key"] == "QA-1"
    assert len(attempts) == 2
    assert len(_creates(fake)) == 1


@pytest.mark.asyncio
async def test_a_read_timeout_is_not_retried(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    attempts: list[str] = []

    class _ReadTimeout(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            attempts.append(request.url.path)
            raise httpx.ReadTimeout("read timed out", request=request)

    monkeypatch.setattr(jira_client, "get_http_client", lambda: httpx.AsyncClient(transport=_ReadTimeout()))
    with pytest.raises(httpx.ReadTimeout):
        await jira_client.create_jira_issue("QA", "t", "r", "s", "a", cfg=_cfg(fake))
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_a_loopback_jira_host_is_refused_before_credentials_are_sent(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    with pytest.raises(JiraUnavailable) as caught:
        await jira_client.create_jira_issue("QA", "t", "r", "s", "a", cfg=_cfg(fake, domain="localhost"))
    assert caught.value.reason == "target_not_allowed"
    assert str(caught.value) == "Jira is unavailable: Target host is not allowed."
    assert fake.requests == []


@pytest.mark.asyncio
async def test_offline_is_refused_before_any_other_check(fake, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    with pytest.raises(JiraUnavailable) as caught:
        await jira_client.create_jira_issue("QA", "t", "r", "s", "a", cfg=_cfg(fake, enabled=False, domain="localhost"))
    assert caught.value.reason == "offline_mode"
    assert fake.requests == []


# ── The triage stage ────────────────────────────────────────────────────────


def _triage_sessions():
    """The agent's own sessions (defect insert, link): a new OPEN defect."""

    @asynccontextmanager
    async def _session():
        class _S:
            async def execute(self, stmt):
                sql = str(stmt).upper()
                if sql.startswith("INSERT"):
                    return SimpleNamespace(first=lambda: (uuid.uuid4(),))
                return SimpleNamespace(
                    first=lambda: SimpleNamespace(test_name="test_checkout", suite_name="pay", test_fingerprint=None,
                                                  created_at=None),
                    scalar_one_or_none=lambda: None,
                )

            async def commit(self):
                return None

        yield _S()

    return _session


@pytest.mark.asyncio
async def test_the_triage_stage_offline_files_no_ticket_and_sends_nothing(fake, monkeypatch):
    from app.agents import triage_agent

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(triage_agent, "AsyncSessionLocal", _triage_sessions())
    monkeypatch.setattr(triage_agent, "_recurring_open_defect", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    policy = AsyncMock(side_effect=AssertionError("no policy check offline"))
    monkeypatch.setattr(triage_agent, "check_jira_ticket_creation_policy", policy)
    ledger = AsyncMock(side_effect=AssertionError("no ledger read offline"))
    monkeypatch.setattr(triage_agent, "prior_tool_calls", ledger)
    monkeypatch.setattr(triage_agent, "resolve_jira_config", AsyncMock(return_value=_cfg(fake)))

    agent = triage_agent.DefectTriageAgent()
    for name in ("mark_stage_running", "broadcast_progress", "log_decision", "mark_stage_done"):
        monkeypatch.setattr(agent, name, AsyncMock())
    monkeypatch.setattr(agent, "_get_jira_key", AsyncMock(return_value="QA"))
    analysis = {"confidence_score": 95, "failure_category": "PRODUCT_BUG", "root_cause_summary": "boom",
                "recommended_actions": ["fix"]}
    out = await agent.run({"pipeline_run_id": "pipe-1", "project_id": str(PROJECT), "test_run_id": str(uuid.uuid4()),
                           "analyses": {str(uuid.uuid4()): analysis}})

    results = out["triage_results"] if isinstance(out, dict) else out.triage_results
    assert [r["action"] for r in results] == ["created"]
    assert results[0]["ticket_key"] is None
    assert fake.requests == []
    policy.assert_not_awaited()
    ledger.assert_not_awaited()
