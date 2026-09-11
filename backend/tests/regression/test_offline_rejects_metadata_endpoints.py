"""Offline mode must not treat a cloud metadata endpoint as an on-box LLM.

Re-audit finding N7, raised in the batch 1 review of the offline ``base_url``
residency check (C3). The check decided "local" from how an address is
classified, and counted link-local as one way of being local. So under
``AI_OFFLINE_MODE`` an operator-supplied ``base_url`` of
``http://169.254.169.254/`` was accepted as on-box -- and that is the instance
metadata service, which is also where cloud credentials are served.

Measured before fixing: on Python 3.11 ``169.254.169.254`` reports
``is_private=True`` as well as ``is_link_local=True``. Deleting the link-local
clause alone would have changed nothing; the address was already accepted as
private. Link-local and known metadata addresses are therefore checked FIRST.
The AWS IPv6 metadata address, ``fd00:ec2::254``, is not link-local at all (it
sits in the ULA range), so it is named explicitly.
"""
from __future__ import annotations

import ipaddress
import socket

import pytest

from app.services import llm_policy_service
from app.services.llm_policy_service import (
    _is_routable,
    _residency_cache_clear,
    _resolves_only_to_local_addresses,
)

pytestmark = pytest.mark.regression


@pytest.fixture(autouse=True)
def _fresh_cache():
    _residency_cache_clear()
    yield
    _residency_cache_clear()


# ── The endpoints that were admitted ─────────────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",          # the IPv4 metadata service on every major cloud
        "169.254.1.1",              # anything else link-local
        "fe80::1",                  # IPv6 link-local
        "fd00:ec2::254",            # AWS metadata over IPv6 — ULA, not link-local
        "::ffff:169.254.169.254",   # the IPv4 endpoint spelled as IPv4-mapped IPv6
    ],
)
def test_metadata_and_link_local_are_not_on_box(address):
    assert _resolves_only_to_local_addresses(address) is False, (
        f"{address} counts as an on-box LLM host, so AI_OFFLINE_MODE would let "
        "prompts go to the instance metadata service"
    )


def test_the_premise_python_calls_it_private():
    """Why deleting the link-local clause alone would not have been a fix."""
    ip = ipaddress.ip_address("169.254.169.254")
    assert ip.is_private and ip.is_link_local
    assert _is_routable(ip) is True


def test_a_hostname_that_resolves_to_the_metadata_service_is_refused(monkeypatch):
    """GCP's metadata.google.internal resolves to exactly this address."""
    monkeypatch.setattr(
        llm_policy_service.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))],
    )
    assert _resolves_only_to_local_addresses("metadata.google.internal") is False


# ── Real local hosts are unaffected ──────────────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.5",
        "192.168.1.20",
        "172.16.4.2",
        "fd12:3456:789a::1",        # ULA that is not a metadata endpoint
    ],
)
def test_loopback_and_private_hosts_remain_on_box(address):
    assert _resolves_only_to_local_addresses(address) is True, (
        f"{address} is a legitimate local model host and must stay allowed"
    )


def test_public_addresses_are_still_refused():
    assert _resolves_only_to_local_addresses("8.8.8.8") is False
