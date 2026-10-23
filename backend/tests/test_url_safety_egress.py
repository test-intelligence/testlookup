"""The integration egress guard, ``url_safety.egress_block_reason`` (W10 INT-01, D-5).

ONE guard for every integration base an operator (Splunk, OpenShift, Jira) or
a QA lead (per-project GitHub / GitLab) configures and the server then sends a
credential to. It refuses loopback, link-local (169.254.169.254: cloud
metadata) and unspecified addresses, and ALLOWS RFC1918 (an on-prem Splunk,
OpenShift, GitHub Enterprise or GitLab) and a host that does not resolve
(setup, air-gapped DNS). It is a different policy from the public-only
``is_safe_public_url`` (webhooks, knowledge URLs), which stays as it was.

The four ``_ssrf_block_reason`` cases moved here from
``tests/services/test_github_checks_service.py`` when the function moved
(regression review/github-checks-service, 2026-06-02: ``api_base_url`` is
QA_LEAD-configurable and the PAT is sent to it, so an unguarded base is an
SSRF read primitive). ``github_checks_service._ssrf_block_reason`` stays
importable under its old name and IS this function: five modules import it
and ~40 tests patch it per module.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import github_checks_service as gh
from app.services import url_safety


def _addrinfo(ip: str):
    return [(2, 1, 6, "", (ip, 0))]


def test_the_old_name_is_the_same_object():
    assert gh._ssrf_block_reason is url_safety.egress_block_reason
    assert "egress_block_reason" in url_safety.__all__


@pytest.mark.asyncio
@pytest.mark.parametrize("url,ip", [
    ("http://169.254.169.254/x", "169.254.169.254"),  # cloud metadata (link-local)
    ("http://127.0.0.1/x", "127.0.0.1"),               # loopback
    ("http://0.0.0.0/x", "0.0.0.0"),                   # unspecified
    ("http://[::1]/x", "::1"),                         # IPv6 loopback (bracketed)
    ("http://[fe80::1]/x", "fe80::1"),                 # IPv6 link-local
])
async def test_dangerous_targets_are_blocked(url, ip):
    with patch.object(url_safety.socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip)):
        reason = await url_safety.egress_block_reason(url)
    assert reason == f"blocked_target:{ip}"


@pytest.mark.asyncio
async def test_a_name_resolving_to_loopback_is_blocked():
    """A DNS name is resolved, not trusted: localhost-by-another-name is refused."""
    with patch.object(url_safety.socket, "getaddrinfo", lambda *a, **k: _addrinfo("127.0.0.1")):
        assert await url_safety.egress_block_reason("https://splunk.corp.example:8089") == "blocked_target:127.0.0.1"


@pytest.mark.asyncio
async def test_any_dangerous_answer_blocks_a_mixed_resolution():
    answers = _addrinfo("10.0.0.5") + _addrinfo("169.254.169.254")
    with patch.object(url_safety.socket, "getaddrinfo", lambda *a, **k: answers):
        assert await url_safety.egress_block_reason("https://ghe.corp/api/v3") == "blocked_target:169.254.169.254"


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["140.82.112.3", "10.0.0.5", "192.168.1.10", "172.16.4.2"])
async def test_public_and_private_hosts_are_allowed(ip):
    # Public GitHub AND RFC1918 (self-hosted GHE / on-prem Splunk) must be allowed.
    with patch.object(url_safety.socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip)):
        assert await url_safety.egress_block_reason(f"http://{ip}/x") is None


@pytest.mark.asyncio
async def test_a_non_resolving_host_is_allowed():
    def _boom(*a, **k):
        raise OSError("name resolution failed")

    with patch.object(url_safety.socket, "getaddrinfo", _boom):
        assert await url_safety.egress_block_reason("https://ghe.invalid.example/x") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["", "not a url", "https:///api/v3"])
async def test_a_url_without_a_host_is_invalid(url):
    assert await url_safety.egress_block_reason(url) == "invalid_url"


@pytest.mark.asyncio
async def test_the_public_only_policy_is_unchanged_for_rfc1918():
    """The two policies differ on purpose: RFC1918 passes the egress guard but
    not the public-only check that webhooks and knowledge URLs use."""
    with patch.object(url_safety.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))]):
        assert await url_safety.egress_block_reason("https://10.0.0.5/x") is None
        safe, reason = url_safety.is_safe_public_url("https://10.0.0.5/x")
    assert safe is False and "non-public" in reason
