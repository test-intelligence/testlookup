"""Commit links point at the project's own GitHub host.

Both places that build commit links -- the commit range and the suspect
ranking -- passed ``api_base=None``, so every link went to github.com even for
a project whose GitHub integration names a GitHub Enterprise server
(found re-testing the links on the homelab, 2026-10-13). They now read the
project's integration, enabled or not.

The API base is free text and the link lands in an ``href``, so only an
http(s) root with a host becomes a link; anything else shows the SHA as text.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import commit_attribution_service as svc

PROJECT_ID = uuid.uuid4()
SHA = "57a6c9405a3c6584b8d0967c9103260fea232039"


def _run():
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=PROJECT_ID, commit_hash=SHA, ci_repo="acme/webapp",
    )


def _row():
    return SimpleNamespace(
        source=svc.SOURCE_SUPPLIED,
        base_source=svc.BASE_SOURCE_SUPPLIED,
        base_commit="a" * 40,
        head_commit=SHA,
        base_run_id=None,
        commits=[{"sha": SHA, "author": "Al", "message": "fix", "files": ["x.py"], "committed_at": None}],
        resolved_at=None,
    )


def _integration(api_base_url, enabled=True):
    return SimpleNamespace(api_base_url=api_base_url, enabled=enabled, repo_owner="acme", repo_name="webapp")


async def _links(integration):
    """The commit-range and suspect links for one run, with the project's integration."""
    lookup = AsyncMock(return_value=integration)
    with patch.object(svc, "_get_row", AsyncMock(return_value=_row())), \
         patch.object(svc, "_target_test_cases", AsyncMock(return_value=[])), \
         patch.object(svc, "get_integration", lookup):
        rng = await svc.get_commit_range(MagicMock(), _run())
        sus = await svc.rank_suspects(MagicMock(), _run(), fingerprint="fp")
    for call in lookup.await_args_list:
        assert call.args[1] == PROJECT_ID
    return [c["commit_url"] for c in rng["commits"]] + [s["commit_url"] for s in sus["suspects"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_an_enterprise_project_links_to_its_own_server(enabled):
    links = await _links(_integration("https://ghe.corp/api/v3", enabled=enabled))
    assert links == [f"https://ghe.corp/acme/webapp/commit/{SHA}"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "integration",
    [None, _integration("https://api.github.com"), _integration("api.github.com")],
)
async def test_public_github_and_no_integration_link_to_github_dot_com(integration):
    links = await _links(integration)
    assert links == [f"https://github.com/acme/webapp/commit/{SHA}"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_base_url",
    [
        "javascript:alert(document.domain)//",
        "ghe.corp/api/v3",
        "ftp://ghe.corp/api/v3",
        "https:///api/v3",
        "http://[::1/api/v3",
    ],
)
async def test_a_base_that_is_not_an_http_url_shows_the_sha_as_text(api_base_url):
    assert await _links(_integration(api_base_url)) == [None, None]


@pytest.mark.asyncio
async def test_an_unavailable_range_does_not_read_the_integration():
    lookup = AsyncMock(return_value=_integration("https://ghe.corp/api/v3"))
    with patch.object(svc, "_get_row", AsyncMock(return_value=None)), \
         patch.object(svc, "get_integration", lookup):
        out = await svc.get_commit_range(MagicMock(), _run())
    assert out["available"] is False
    lookup.assert_not_awaited()
