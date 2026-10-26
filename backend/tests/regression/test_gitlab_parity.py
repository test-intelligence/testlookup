"""W19 INT-05: GitLab parity — commit attribution, change correlation (against the fake GitLab).

* GitLab's one-call compare becomes the normalized commit list, oldest first,
  with the range's changed files on the head commit;
* a run whose repo is not the configured project path is not fetched; a numeric
  project id is trusted;
* the ``gitlab`` gate (flag + offline) decides before anything is read;
* change correlation's GitLab branch reads ``/repository/commits`` in the window.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.services import commit_attribution_service as ca
from tests.fakes.fake_integrations import FakeState, create_app, demo_sha

BASE = "http://gitlab.fake"


@pytest.fixture
def fake(monkeypatch):
    state = FakeState()
    transport = httpx.ASGITransport(app=create_app(state))
    real = httpx.AsyncClient

    class Routed(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)

    async def _no_block(url):
        return None

    monkeypatch.setattr(ca, "_ssrf_block_reason", _no_block)
    return state


def test_gitlab_compare_becomes_normalized_commits_with_files(fake):
    target = ca.GitLabConnectorTarget(base_url=BASE, project_path="group/api-gateway", pat="fake-gitlab-pat")
    base, head = demo_sha("group/api-gateway", 0), demo_sha("group/api-gateway", 2)
    commits = asyncio.run(ca._fetch_gitlab_range(target, base, head, run_id=uuid.uuid4()))
    assert [c["message"] for c in commits] == ["Retry upstream on 503", "Tighten auth token parsing"]
    assert commits[0]["author"] == "Omar Haddad" and commits[0]["files"] == []
    assert set(commits[-1]["files"]) == {"gateway/upstream.py", "gateway/retry.py", "gateway/auth.py"}
    req = [r for r in fake.requests if r["service"] == "gitlab"][-1]
    assert req["path"].endswith("/repository/compare") and req["auth"] == "ok"
    assert "pat" not in repr(target).lower() or "fake-gitlab-pat" not in repr(target)


def test_a_bad_token_or_unknown_sha_answers_none(fake):
    bad = ca.GitLabConnectorTarget(base_url=BASE, project_path="group/api-gateway", pat="wrong")
    assert asyncio.run(ca._fetch_gitlab_range(bad, "a" * 40, "b" * 40, run_id=uuid.uuid4())) is None
    ok = ca.GitLabConnectorTarget(base_url=BASE, project_path="group/api-gateway", pat="fake-gitlab-pat")
    assert asyncio.run(ca._fetch_gitlab_range(ok, "0" * 40, "1" * 40, run_id=uuid.uuid4())) is None


def _gl_target(path="group/api-gateway"):
    from app.services.gitlab_integration_service import GitLabTargetResolution

    return GitLabTargetResolution(project_id="p", base_url=BASE, project_path=path, pat="fake-gitlab-pat")


@pytest.mark.parametrize("ci_repo,path,expected", [
    ("group/api-gateway", "group/api-gateway", True),
    ("Group/API-Gateway.git", "group/api-gateway", True),
    ("other/repo", "group/api-gateway", False),
    ("anything/at-all", "42", True),
    (None, "group/api-gateway", True),
])
def test_repo_match_decides_the_gitlab_target(monkeypatch, ci_repo, path, expected):
    from app.services import gitlab_integration_service as gl

    async def _open(db, project_id=None, **k):
        return True

    async def _target(db, project_id, **k):
        return _gl_target(path)

    monkeypatch.setattr(gl, "_post_allowed", _open)
    monkeypatch.setattr(gl, "load_gitlab_target", _target)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), ci_repo=ci_repo)
    got = asyncio.run(ca._gitlab_connector_target(None, run))
    assert (got is not None) is expected


def test_the_gitlab_gate_decides_before_any_read(monkeypatch):
    from app.services import gitlab_integration_service as gl

    reads = []

    async def _closed(db, project_id=None, **k):
        return False

    async def _target(db, project_id, **k):
        reads.append(project_id)
        return _gl_target()

    monkeypatch.setattr(gl, "_post_allowed", _closed)
    monkeypatch.setattr(gl, "load_gitlab_target", _target)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), ci_repo="group/api-gateway")
    assert asyncio.run(ca._gitlab_connector_target(None, run)) is None and reads == []


def test_change_correlation_reads_gitlab_commits_in_the_window(fake, monkeypatch):
    from app.tools import fetch_build_changes as fbc

    async def _no_block(url):
        return None

    class _Client:
        async def get(self, url, **kw):
            async with httpx.AsyncClient() as c:
                return await c.get(url, **kw)

    monkeypatch.setattr(fbc, "egress_block_reason", _no_block)
    monkeypatch.setattr(fbc, "get_http_client", lambda: _Client())
    commits = asyncio.run(fbc._fetch_gitlab_commits(BASE, "group/api-gateway", "fake-gitlab-pat",
                                                    "2026-10-20T09:30:00Z", "2026-10-20T11:30:00Z"))
    assert [c["message"] for c in commits] == ["Tighten auth token parsing", "Retry upstream on 503"]
    assert commits[0]["author"] == "Dana Whitfield" and len(commits[0]["sha"]) == 8
