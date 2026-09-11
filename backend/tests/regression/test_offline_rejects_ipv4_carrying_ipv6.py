"""Re-audit QA-B45-A4: IPv6 forms that carry an IPv4 address are not on-box.

CPython marks 64:ff9b:1::/48 (RFC 8215 local-use NAT64) and 2001::/23 (which
holds Teredo) as ``is_private``, and 6to4 wraps any IPv4. So under
AI_OFFLINE_MODE a hostname answering one of these, each carrying 8.8.8.8,
passed the residency rule and the offline pin, and was dialled: a NAT64
gateway or tunnel then took the prompt off-box.
"""
from __future__ import annotations

import ipaddress
import socket

import pytest

from app.services import llm_policy_service
from app.services.llm_policy_service import _is_routable

CARRIES_8_8_8_8 = [
    "64:ff9b:1::808:808",                     # RFC 8215 local-use NAT64
    "2002:808:808::1",                        # 6to4
    "2001:0:4136:e378:8000:63bf:f7f7:f7f7",   # Teredo (client 8.8.8.8)
    "64:ff9b::808:808",                       # well-known NAT64 (was already refused)
]

STILL_ON_BOX = ["::1", "::ffff:127.0.0.1", "fd12:3456:789a::1", "10.0.0.5", "127.0.0.1"]


@pytest.mark.parametrize("address", CARRIES_8_8_8_8)
def test_an_ipv6_address_carrying_an_ipv4_is_off_box(address):
    assert _is_routable(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", STILL_ON_BOX)
def test_loopback_private_and_mapped_local_addresses_stay_on_box(address):
    assert _is_routable(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", CARRIES_8_8_8_8)
def test_a_hostname_answering_one_is_not_local(monkeypatch, address):
    """The residency rule the offline pin and the policy both use."""

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 0, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert llm_policy_service._resolves_only_to_local_addresses("llm.internal") is False
