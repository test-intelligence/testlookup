"""W10 FAIL-09: ``fetch_build_changes`` reads the PROJECT's GitHub / GHE commits, gated, never the env token.

The real tool runs; its short session is a double that answers the two runs'
read, the project's GitHub integration and PAT come through the seams
``github_target`` reads, and the one HTTP call goes to the shared contract fake
through ``httpx.ASGITransport``.

What these pin:

* the project's GHE base and PAT reach the fake (``base: ghe``, ``auth: ok``),
  for the flaky run's repository, and the commits come back;
* offline: no request, ``github_status: "offline_mode"``, and the summary says
  so (not "set GITHUB_TOKEN + GITHUB_REPO");
* a repository mismatch and a project without an integration: no request;
* the env ``GITHUB_TOKEN`` / ``GITHUB_REPO`` are never used;
* the session is closed before the request.
"""
from __future__ import annotations

import json
import socket
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.services import github_checks_service, secret_service, url_safety
from app.tools import fetch_build_changes as tool
from tests.fakes.fake_integrations import FakeState, create_app

PROJECT = uuid.uuid4()


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _ToolSession:
    def __init__(self, world):
        self.world = world

    async def execute(self, _stmt):
        return _Rows(self.world.runs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.world.events.append("session-closed")
        return False


class _Recording(httpx.AsyncBaseTransport):
    def __init__(self, app, events):
        self.inner, self.events = httpx.ASGITransport(app=app), events

    async def handle_async_request(self, request):
        self.events.append("request")
        return await self.inner.handle_async_request(request)


@pytest.fixture
def world(monkeypatch):
    state = FakeState()
    events: list[str] = []
    client = httpx.AsyncClient(transport=_Recording(create_app(state), events))
    w = SimpleNamespace(
        state=state, events=events, flag=True,
        integration=SimpleNamespace(project_id=PROJECT, enabled=True, api_base_url="https://ghe.fake.test/api/v3",
                                    repo_owner="acme", repo_name="payments", has_pat=True),
        runs=[
            SimpleNamespace(build_number="100", ci_repo="acme/payments",
                            created_at=datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)),
            SimpleNamespace(build_number="101", ci_repo="acme/payments",
                            created_at=datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)),
        ],
    )

    async def get_integration(_db, project_id):
        return w.integration if str(project_id) == str(PROJECT) else None

    async def post_allowed(_db=None, *, project_id=None, **_kw):
        return w.flag

    async def read_secret(_db, scope, key):
        assert scope == github_checks_service.SECRET_SCOPE and key == f"project:{PROJECT}:pat"
        return "fake-gh-pat"

    def resolves(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.5", 443))]

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_env_token_must_not_be_used")
    monkeypatch.setattr(settings, "GITHUB_REPO", "acme/checkout")
    monkeypatch.setattr(tool, "AsyncSessionLocal", lambda: _ToolSession(w))
    monkeypatch.setattr(tool, "get_http_client", lambda: client)
    monkeypatch.setattr(github_checks_service, "get_integration", get_integration)
    monkeypatch.setattr(github_checks_service, "_post_allowed", post_allowed)
    monkeypatch.setattr(secret_service, "read_secret", read_secret)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", resolves)
    return w


async def _changes(project_id=str(PROJECT)) -> dict:
    out = await tool.fetch_build_changes.ainvoke({"params_json": json.dumps({
        "test_fingerprint": "fp-1", "stable_build_number": "100", "flaky_build_number": "101",
        "project_id": project_id,
    })})
    return json.loads(out)


@pytest.mark.asyncio
async def test_the_projects_ghe_base_and_pat_reach_the_fake(world):
    answer = await _changes()

    assert answer["github_status"] is None
    assert [c["message"] for c in answer["commits"]] == [
        "Retry card authorisation once on 503",
        "Shrink the DB connection pool to 5",
        "Raise the payment client timeout to 30 s",
    ]
    assert answer["change_summary"].startswith("Found 3 commits between stable build '100'")
    (call,) = world.state.requests
    assert (call["service"], call["base"], call["auth"], call["path"]) == (
        "github", "ghe", "ok", "/api/v3/repos/acme/payments/commits")
    assert call["query"]["per_page"] == "20"
    assert "ghp_env_token_must_not_be_used" not in json.dumps(world.state.requests)
    assert world.events == ["session-closed", "request"], "the session was open during the call"


@pytest.mark.asyncio
async def test_offline_no_request_and_the_summary_says_why(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    answer = await _changes()

    assert answer["commits"] == [] and answer["github_status"] == "offline_mode"
    assert answer["change_summary"].startswith("GitHub commits unavailable for this project (offline mode).")
    assert "GITHUB_TOKEN" not in answer["change_summary"]
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_a_repository_mismatch_sends_nothing(world):
    for run in world.runs:
        run.ci_repo = "acme/checkout"

    answer = await _changes()

    assert (answer["commits"], answer["github_status"]) == ([], "repo_mismatch")
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_no_integration_and_no_project_send_nothing_even_with_the_env_token(world):
    world.integration = None
    assert (await _changes())["github_status"] == "no_integration"
    assert (await _changes(project_id=""))["github_status"] == "no_integration"
    assert (await _changes(project_id="not-a-uuid"))["github_status"] == "no_integration"
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_github_checks_off_sends_nothing(world):
    world.flag = False
    answer = await _changes()
    assert answer["github_status"] == "github_checks_off"
    assert "GitHub checks are turned off for this project" in answer["change_summary"]
    assert world.state.requests == []
