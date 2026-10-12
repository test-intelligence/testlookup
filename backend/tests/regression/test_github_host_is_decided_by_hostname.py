"""Public GitHub is recognised by the API base's hostname, not a substring.

The Fixer's clone host and the commit links both asked ``"api.github.com" in
base``, so a configured base such as ``https://evil.example/api.github.com`` or
``https://api.github.com.evil.example`` was taken for public GitHub (CodeQL
py/incomplete-url-substring-sanitization, after the OSS release, 2026-10-12).
Both now go through ``github_urls.github_html_root``.
"""
from __future__ import annotations

import pytest

from app.agents.fixer.workflow import _clone_host
from app.services.commit_attribution_service import _commit_html_url
from app.services.github_urls import PUBLIC_GITHUB, github_html_root


@pytest.mark.parametrize(
    "api_base",
    [None, "", "https://api.github.com", "https://api.github.com/", "HTTPS://API.GITHUB.COM"],
)
def test_the_public_api_is_github_dot_com(api_base):
    assert github_html_root(api_base) == PUBLIC_GITHUB


@pytest.mark.parametrize(
    ("api_base", "root"),
    [
        ("https://ghe.corp/api/v3", "https://ghe.corp"),
        ("https://ghe.corp/api", "https://ghe.corp"),
        ("https://ghe.corp/api/v3/", "https://ghe.corp"),
        # The substring check took these for public GitHub.
        ("https://evil.example/api.github.com", "https://evil.example/api.github.com"),
        ("https://api.github.com.evil.example", "https://api.github.com.evil.example"),
        ("https://api.github.com.evil.example/api/v3", "https://api.github.com.evil.example"),
    ],
)
def test_any_other_host_is_its_own_root(api_base, root):
    assert github_html_root(api_base) == root


def test_the_fixer_clone_host_follows_the_rule():
    assert _clone_host("https://api.github.com") == PUBLIC_GITHUB
    assert _clone_host("https://api.github.com.evil.example/api/v3") == "https://api.github.com.evil.example"


def test_commit_links_follow_the_rule():
    sha = "a" * 40
    assert _commit_html_url("acme/webapp", "https://api.github.com", sha) == f"https://github.com/acme/webapp/commit/{sha}"
    assert (
        _commit_html_url("acme/webapp", "https://evil.example/api.github.com", sha)
        == f"https://evil.example/api.github.com/acme/webapp/commit/{sha}"
    )
