"""W05 REL-08: ``POST /releases/sync`` explains every way it can fail.

The two sync services raise ``GitHubSyncUnavailable`` / ``JiraSyncUnavailable``
for every gate they own (offline mode, no integration, no credential, an SSRF
block, a non-200 from the source) and the router maps both to 503 with the
gate's own words. What nothing caught was the source being UNREACHABLE: a
refused connection, DNS failure or timeout raised ``httpx.HTTPError`` straight
through the handler, and the release page's new Sync dialog got an opaque 500
that read like a TestLookup bug.

These drive the router through the REAL sync functions and their REAL egress
gate (``_authorized_get``); only the integration row, the credential, the SSRF
DNS check and the HTTP transport are faked. Pinned:

1. ``AI_OFFLINE_MODE`` -> 503 ``AI_OFFLINE_MODE is enabled`` for GitHub AND
   Jira, nothing staged, nothing committed, no request sent;
2. a missing integration / credential keeps its own sentence (503);
3. a network failure -> 502 ``Could not reach GitHub: ConnectError`` /
   ``Could not reach Jira: ReadTimeout`` -- the exception CLASS only: the
   request URL (repository, Jira host) never reaches the response;
4. a source that answers with an HTTP error is still the 503 it was.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers import releases as router_mod  # noqa: E402
from app.services import github_release_sync as gh  # noqa: E402
from app.services import jira_release_sync as jira  # noqa: E402

PROJECT = uuid.uuid4()
GITHUB_URL = "https://api.github.com/repos/acme-internal/payments-secret/milestones"
JIRA_HOST = "jira.acme-internal.example"


class _Session:
    """Counts commits and staged rows. ``execute`` must never be reached: every
    case here fails before the first release is looked up."""

    def __init__(self):
        self.added: list = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, *_a, **_kw):
        raise AssertionError("the sync reached the release table")


@pytest.fixture
def sent(monkeypatch):
    """The HTTP transport. Records every request; the test sets what it does."""
    calls: list[str] = []
    behaviour = SimpleNamespace(action=None)

    async def _get(self, url, *_a, **_kw):
        calls.append(str(url))
        return await behaviour.action(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    behaviour.calls = calls
    return behaviour


@pytest.fixture
def online(monkeypatch):
    """Both integrations configured and reachable as far as TestLookup can tell."""
    monkeypatch.setattr(gh.settings, "AI_OFFLINE_MODE", False, raising=False)

    async def _integration(_db, _project_id):
        return SimpleNamespace(enabled=True, api_base_url="https://api.github.com",
                               repo_owner="acme-internal", repo_name="payments-secret")

    async def _secret(_db, _scope, _key):
        return "ghp_test_token"

    async def _no_block(_url):
        return None

    async def _jira_cfg(_db):
        return {"enabled": True, "domain": JIRA_HOST, "email": "qa@acme.example",
                "api_token": "jira-test-token", "default_project_key": "PAY"}

    monkeypatch.setattr(gh, "get_integration", _integration)
    monkeypatch.setattr("app.services.secret_service.read_secret", _secret)
    monkeypatch.setattr(gh, "_ssrf_block_reason", _no_block)
    monkeypatch.setattr(jira, "_ssrf_block_reason", _no_block)
    monkeypatch.setattr(jira, "resolve_jira_config", _jira_cfg)


@pytest.fixture(autouse=True)
def scoped(monkeypatch):
    async def _scope(_db, _user, _pid):
        return PROJECT, None

    monkeypatch.setattr(router_mod, "resolve_project_scope", _scope)


async def _sync(db, source, key=None):
    return await router_mod.sync_releases_from_external(
        body=router_mod.ReleaseSyncIn(project_id=str(PROJECT), source=source, jira_project_key=key),
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()),
    )


async def _refused(db, source):
    with pytest.raises(HTTPException) as exc:
        await _sync(db, source)
    return exc.value


# ── 1. Offline mode ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["github", "jira"])
async def test_offline_mode_is_a_503_in_its_own_words_and_changes_nothing(monkeypatch, online, sent, source):
    # Everything else configured: the kill switch alone must stop the request.
    # (Jira reads its stored config first, for the default key: a database
    # read, not egress.)
    monkeypatch.setattr(gh.settings, "AI_OFFLINE_MODE", True, raising=False)

    async def _never(url):
        raise AssertionError("an offline deployment sent a request")

    sent.action = _never
    db = _Session()

    exc = await _refused(db, source)

    assert (exc.status_code, exc.detail) == (503, "AI_OFFLINE_MODE is enabled")
    assert sent.calls == []
    assert db.added == [] and db.commits == 0


# ── 2. A missing integration keeps its own sentence ──────────────────────────


@pytest.mark.asyncio
async def test_github_without_an_integration_or_a_pat_says_which(monkeypatch, online):
    async def _none(_db, _project_id):
        return None

    monkeypatch.setattr(gh, "get_integration", _none)
    exc = await _refused(_Session(), "github")
    assert (exc.status_code, exc.detail) == (503, "No enabled GitHub integration for this project")

    async def _row(_db, _project_id):
        return SimpleNamespace(enabled=True, api_base_url="https://api.github.com",
                               repo_owner="acme-internal", repo_name="payments-secret")

    async def _no_pat(_db, _scope, _key):
        return None

    monkeypatch.setattr(gh, "get_integration", _row)
    monkeypatch.setattr("app.services.secret_service.read_secret", _no_pat)
    exc = await _refused(_Session(), "github")
    assert (exc.status_code, exc.detail) == (503, "No PAT stored for this integration")


@pytest.mark.asyncio
async def test_jira_without_a_credential_says_so(monkeypatch, online):
    async def _cfg(_db):
        return {"enabled": True, "domain": JIRA_HOST, "email": None, "api_token": None,
                "default_project_key": "PAY"}

    monkeypatch.setattr(jira, "resolve_jira_config", _cfg)
    exc = await _refused(_Session(), "jira")
    assert (exc.status_code, exc.detail) == (503, "No Jira credential configured")


# ── 3. An unreachable source is a 502 naming the class, never the URL ────────


@pytest.mark.asyncio
async def test_a_refused_connection_to_github_is_a_502_without_the_url(online, sent):
    async def _refuse(url):
        raise httpx.ConnectError(f"[Errno 111] Connection refused: {url}",
                                 request=httpx.Request("GET", url))

    sent.action = _refuse
    db = _Session()

    exc = await _refused(db, "github")

    assert exc.status_code == 502
    assert exc.detail == "Could not reach GitHub: ConnectError"
    assert sent.calls == [GITHUB_URL], "the real egress path was not exercised"
    for leak in ("acme-internal", "payments-secret", "api.github.com", "ghp_test_token"):
        assert leak not in exc.detail, f"the response leaked {leak!r}"
    assert db.added == [] and db.commits == 0


@pytest.mark.asyncio
async def test_a_jira_timeout_is_a_502_without_the_host(online, sent):
    async def _timeout(url):
        raise httpx.ReadTimeout(f"timed out reading {url}", request=httpx.Request("GET", url))

    sent.action = _timeout
    db = _Session()

    exc = await _refused(db, "jira")

    assert exc.status_code == 502
    assert exc.detail == "Could not reach Jira: ReadTimeout"
    assert sent.calls and sent.calls[0].startswith(f"https://{JIRA_HOST}/rest/api/3/project/PAY/versions")
    assert JIRA_HOST not in exc.detail
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_dns_failure_is_a_502_too(online, sent):
    async def _dns(url):
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=httpx.Request("GET", url))

    sent.action = _dns

    exc = await _refused(_Session(), "jira")

    assert (exc.status_code, exc.detail) == (502, "Could not reach Jira: ConnectError")


# ── 4. A source that answered is still the 503 it was ────────────────────────


@pytest.mark.asyncio
async def test_an_http_error_from_github_keeps_its_503(online, sent):
    async def _unauthorized(url):
        return httpx.Response(401, request=httpx.Request("GET", url), json={"message": "Bad credentials"})

    sent.action = _unauthorized

    exc = await _refused(_Session(), "github")

    assert (exc.status_code, exc.detail) == (503, "GitHub returned 401 for /milestones")


@pytest.mark.asyncio
async def test_a_reachable_source_still_syncs_and_commits_once(online, sent, monkeypatch):
    """The control: the new except clause must not swallow a working sync."""
    async def _empty(url):
        return httpx.Response(200, request=httpx.Request("GET", url), json=[])

    sent.action = _empty
    db = _Session()

    out = await _sync(db, "github")

    assert out == {"source": "github", "created": 0, "updated": 0, "skipped": 0}
    assert db.commits == 1
