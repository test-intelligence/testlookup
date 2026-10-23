"""W10 INT-04: every project's GitHub / GHE and GitLab integration is probed against ITS OWN target.

The real ``run_project_probes`` runs against the shared contract fake
(``tests/fakes/fake_integrations.py``) through ``httpx.ASGITransport``: the
fake's request log says which base each call reached (``base: dotcom | ghe``)
and whether the credential it carried was the right one (``auth: ok | bad``)
-- never the credential itself. The read phase runs on a session double that
answers the two integration lists; the rows and the PATs come through the
seams the services read (``github_checks_service.get_integration``,
``gitlab_integration_service.get_integration``, ``secret_service.read_secret``).

What these pin:

* two GitHub projects on different bases (github.com, a GHE ``/api/v3``) each
  probe THEIR base with THEIR PAT; a third whose PAT the fake revoked reads
  ``auth_error`` and the other two are unchanged; a GitLab project is healthy;
* a project without a stored PAT reads ``auth_error`` "No token ..." and makes
  no request;
* offline: ``skipped`` rows, an empty log and no secret read;
* at most ``PROJECT_PROBE_CONCURRENCY`` (5) requests in flight over 8 projects;
* the read session is closed (or released) before the first request;
* no PAT reaches a result, a ``repr`` or the fake's log.
"""
from __future__ import annotations

import asyncio
import json
import socket
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.services import github_checks_service, gitlab_integration_service, secret_service
from app.services import integration_probe_service as probes
from app.services import url_safety
from tests.fakes.fake_integrations import FakeState, create_app

PUBLIC_ADDRESS = "140.82.112.5"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _ReadSession:
    """Answers the two integration lists of the read phase, filtered by the
    statement's own ``IN`` values; records when it gives its connection back."""

    def __init__(self, world, events):
        self.world = world
        self.events = events
        self.statements: list[str] = []
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, stmt, *_args, **_kwargs):
        sql = str(stmt)
        self.statements.append(sql)
        params = stmt.compile().params
        wanted = None
        for key, value in params.items():
            if key.startswith("project_id") and isinstance(value, (list, tuple, set)):
                wanted = {uuid.UUID(str(v)) for v in value}
        provider = "github" if "github_integrations" in sql else "gitlab"
        rows = [
            (pid, row.project_name)
            for pid, row in self.world[provider].items()
            if row.enabled and (wanted is None or pid in wanted)
        ]
        return _Rows(rows)

    async def close(self):
        self.events.append("session-closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        await self.close()
        return False


class _CountingTransport(httpx.AsyncBaseTransport):
    """The fake behind a gate that counts requests in flight."""

    def __init__(self, app, events):
        self.inner = httpx.ASGITransport(app=app)
        self.events = events
        self.in_flight = 0
        self.max_in_flight = 0

    async def handle_async_request(self, request):
        self.events.append(f"request {request.url.host}{request.url.path}")
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            return await self.inner.handle_async_request(request)
        finally:
            self.in_flight -= 1


def _github_row(pid, api_base, repo, name):
    owner, repo_name = repo.split("/")
    return SimpleNamespace(project_id=pid, enabled=True, api_base_url=api_base, repo_owner=owner,
                           repo_name=repo_name, has_pat=True, project_name=name)


def _gitlab_row(pid, base_url, path, name):
    return SimpleNamespace(project_id=pid, enabled=True, base_url=base_url, project_path=path,
                           has_pat=True, project_name=name)


@pytest.fixture
def world(monkeypatch):
    """The fake, a client routed to it, and a per-project world the seams read."""
    state = FakeState()
    events: list[str] = []
    transport = _CountingTransport(create_app(state), events)
    client = httpx.AsyncClient(transport=transport)
    data = {"github": {}, "gitlab": {}, "pats": {}, "secret_reads": []}

    async def github_integration(_db, project_id):
        return data["github"].get(uuid.UUID(str(project_id)))

    async def gitlab_integration(_db, project_id):
        return data["gitlab"].get(uuid.UUID(str(project_id)))

    async def read_secret(_db, scope, key):
        data["secret_reads"].append((scope, key))
        return data["pats"].get((scope, key))

    def resolves(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_ADDRESS, 443))]

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(probes, "get_http_client", lambda: client)
    monkeypatch.setattr(github_checks_service, "get_integration", github_integration)
    monkeypatch.setattr(gitlab_integration_service, "get_integration", gitlab_integration)
    monkeypatch.setattr(secret_service, "read_secret", read_secret)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", resolves)

    def add_github(name, api_base, repo, pat):
        pid = uuid.uuid4()
        data["github"][pid] = _github_row(pid, api_base, repo, name)
        if pat:
            data["pats"][(github_checks_service.SECRET_SCOPE, f"project:{pid}:pat")] = pat
        return pid

    def add_gitlab(name, base_url, path, pat):
        pid = uuid.uuid4()
        data["gitlab"][pid] = _gitlab_row(pid, base_url, path, name)
        if pat:
            data["pats"][(gitlab_integration_service.SECRET_SCOPE, f"project:{pid}:pat")] = pat
        return pid

    return SimpleNamespace(state=state, events=events, transport=transport, data=data,
                           add_github=add_github, add_gitlab=add_gitlab,
                           session=lambda: _ReadSession(data, events))


def _by_project(results):
    return {r.project_id: r for r in results}


@pytest.mark.asyncio
async def test_each_project_probes_its_own_base_with_its_own_token(world):
    payments = world.add_github("Payment Service", "https://api.github.com", "acme/payments", "fake-gh-pat")
    checkout = world.add_github("Checkout", "https://ghe.fake.test/api/v3", "acme/checkout", "fake-gh-pat-2")
    world.state.github_tokens["fake-gh-pat-3"] = {"acme/payments"}
    revoked = world.add_github("Ledger", "https://api.github.com", "acme/payments", "fake-gh-pat-3")
    world.state.revoked_github_tokens.add("fake-gh-pat-3")
    gateway = world.add_gitlab("API Gateway", "https://gitlab.fake.test", "group/api-gateway", "fake-gitlab-pat")

    results = _by_project(await probes.run_project_probes(db=world.session()))

    assert set(results) == {str(payments), str(checkout), str(revoked), str(gateway)}
    assert results[str(payments)].status == "healthy"
    assert results[str(payments)].message == "Repository acme/payments reachable"
    assert results[str(payments)].target == "api.github.com"
    assert results[str(checkout)].status == "healthy"
    assert results[str(checkout)].target == "ghe.fake.test/api/v3"
    assert results[str(revoked)].status == "auth_error"
    assert results[str(revoked)].message == "HTTP 401: the token was refused"
    assert results[str(gateway)].status == "healthy"
    assert results[str(gateway)].provider == "gitlab"
    assert results[str(gateway)].target == "gitlab.fake.test"
    for pid, result in results.items():
        assert result.scope_key == pid and result.project_id == pid

    github_calls = [r for r in world.state.requests if r["service"] == "github"]
    seen = sorted((r["base"], r["path"], r["auth"]) for r in github_calls)
    assert seen == sorted([
        ("dotcom", "/repos/acme/payments", "ok"),          # Payment Service, its PAT
        ("ghe", "/api/v3/repos/acme/checkout", "ok"),      # Checkout, ITS base and PAT
        ("dotcom", "/repos/acme/payments", "bad"),         # Ledger, the revoked PAT
    ])
    gitlab_calls = [(r["path"], r["auth"]) for r in world.state.requests if r["service"] == "gitlab"]
    assert gitlab_calls == [("/api/v4/projects/group/api-gateway", "ok")]


@pytest.mark.asyncio
async def test_a_project_without_a_stored_token_reads_auth_error_and_sends_nothing(world):
    pid = world.add_github("Payment Service", "https://api.github.com", "acme/payments", None)
    gid = world.add_gitlab("API Gateway", "https://gitlab.fake.test", "group/api-gateway", None)

    results = _by_project(await probes.run_project_probes(db=world.session()))

    assert results[str(pid)].status == "auth_error"
    assert results[str(pid)].message == "No token is stored for this project's GitHub integration."
    assert results[str(gid)].status == "auth_error"
    assert results[str(gid)].message == "No token is stored for this project's GitLab integration."
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_offline_every_row_is_skipped_with_no_request_and_no_secret_read(world, monkeypatch):
    world.add_github("Payment Service", "https://api.github.com", "acme/payments", "fake-gh-pat")
    world.add_gitlab("API Gateway", "https://gitlab.fake.test", "group/api-gateway", "fake-gitlab-pat")
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    results = await probes.run_project_probes(db=world.session())

    assert sorted((r.provider, r.status, r.message) for r in results) == [
        ("github", "skipped", "AI_OFFLINE_MODE=true -- outbound github calls are disabled"),
        ("gitlab", "skipped", "AI_OFFLINE_MODE=true -- outbound gitlab calls are disabled"),
    ]
    assert world.state.requests == []
    assert world.data["secret_reads"] == [], "offline, no PAT may even be decrypted"


@pytest.mark.asyncio
async def test_never_more_than_five_probes_are_in_flight(world):
    for n in range(8):
        world.add_github(f"Project {n}", "https://api.github.com", "acme/payments", "fake-gh-pat")

    results = await probes.run_project_probes(db=world.session())

    assert len(results) == 8 and {r.status for r in results} == {"healthy"}
    assert world.transport.max_in_flight <= probes.PROJECT_PROBE_CONCURRENCY == 5
    assert world.transport.max_in_flight > 1, "the probes ran one at a time"


@pytest.mark.asyncio
async def test_the_read_session_is_closed_before_the_first_request(world, monkeypatch):
    world.add_github("Payment Service", "https://api.github.com", "acme/payments", "fake-gh-pat")
    world.add_gitlab("API Gateway", "https://gitlab.fake.test", "group/api-gateway", "fake-gitlab-pat")
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", world.session, raising=False)

    await probes.run_project_probes()  # no session given: a short one of its own

    requests = [i for i, e in enumerate(world.events) if e.startswith("request")]
    assert requests, "no probe was made"
    assert world.events.index("session-closed") < requests[0], world.events


@pytest.mark.asyncio
async def test_a_request_session_is_released_before_the_first_request(world):
    world.add_github("Payment Service", "https://api.github.com", "acme/payments", "fake-gh-pat")

    await probes.run_project_probes(db=world.session())

    assert world.events[0] == "session-closed", world.events
    assert any(e.startswith("request") for e in world.events[1:])


@pytest.mark.asyncio
async def test_the_filters_reach_the_read(world):
    keep = world.add_github("Payment Service", "https://api.github.com", "acme/payments", "fake-gh-pat")
    world.add_github("Checkout", "https://ghe.fake.test/api/v3", "acme/checkout", "fake-gh-pat-2")
    gid = world.add_gitlab("API Gateway", "https://gitlab.fake.test", "group/api-gateway", "fake-gitlab-pat")

    only_github = await probes.run_project_probes(
        project_ids={keep, gid}, providers={"github"}, db=world.session(),
    )
    assert [r.project_id for r in only_github] == [str(keep)]
    assert await probes.run_project_probes(project_ids=set(), db=world.session()) == []


@pytest.mark.asyncio
async def test_a_blocked_base_is_refused_before_the_token_is_sent(world, monkeypatch):
    pid = world.add_github("Payment Service", "http://127.0.0.1:6379", "acme/payments", "fake-gh-pat")

    def loopback(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 6379))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", loopback)
    results = _by_project(await probes.run_project_probes(db=world.session()))

    assert results[str(pid)].status == "down"
    assert results[str(pid)].message == "Target host is not allowed (blocked_target:127.0.0.1)"
    assert world.state.requests == []


@pytest.mark.asyncio
async def test_no_token_reaches_a_result_a_repr_or_the_fakes_log(world):
    world.add_github("Payment Service", "https://api.github.com", "acme/payments", "fake-gh-pat")
    world.add_gitlab("API Gateway", "https://gitlab.fake.test", "group/api-gateway", "fake-gitlab-pat")

    results = await probes.run_project_probes(db=world.session())

    dumped = json.dumps(world.state.requests, default=str) + repr(results)
    for secret in ("fake-gh-pat", "fake-gitlab-pat"):
        assert secret not in dumped
    resolution = await gitlab_integration_service.load_gitlab_target(None, next(iter(world.data["gitlab"])))
    assert resolution.pat == "fake-gitlab-pat" and "fake-gitlab-pat" not in repr(resolution)
