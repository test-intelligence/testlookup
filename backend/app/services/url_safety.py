"""Shared SSRF guard for server-side fetches of customer-supplied URLs.

Any code path that takes a URL from a user (webhook target, knowledge-source
URL, connector base URL) and fetches it *server-side* is an SSRF sink: the
attacker picks the host, our server makes the request from inside the trust
boundary. Without a guard they can reach cloud metadata (169.254.169.254),
loopback admin ports (127.0.0.1:6379 Redis, :8000 the app itself), or any
RFC1918 host the pod can route to, and exfiltrate the response.

This module is the single source of truth for "is this URL safe to fetch".
It was originally inlined in ``webhook_service``; extracted here so
``connectors/url_connector`` (and future connectors) share one implementation
instead of each growing a subtly-different copy.

The check is intentionally a *resolve-then-classify* guard, not a string
denylist: a denylist misses ``0x7f.0.0.1``, ``2130706433``, IPv6-mapped
forms, and DNS names that resolve to private space. We resolve the host and
reject if ANY resolved address is private / loopback / link-local / reserved /
multicast / unspecified.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

__all__ = ["is_safe_public_url", "assert_public_url"]


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Return ``(safe, reason)`` for fetching ``url`` server-side.

    ``safe is False`` carries a human-readable ``reason``; ``safe is True``
    carries an empty reason.

    A host that does NOT resolve is treated as safe: it can't be reached, so
    there's no SSRF, and we don't want to reject a legitimate endpoint that
    simply isn't live yet (the actual request then fails naturally at the
    socket layer). Every host that DOES resolve must map exclusively to
    public addresses.

    Synchronous (does a blocking DNS lookup); call ``assert_public_url`` —
    or wrap in ``asyncio.to_thread`` — from async code.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL could not be parsed"
    if parsed.scheme not in ("http", "https"):
        return False, "URL scheme must be http or https"
    host = parsed.hostname
    if not host:
        return False, "URL has no host"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False, "URL has an invalid port"
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        # Unresolvable host — not reachable, so not an SSRF risk.
        return True, ""
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"target resolves to a non-public address ({addr})"
    return True, ""


async def assert_public_url(url: str) -> None:
    """Async wrapper that raises ``ValueError`` when ``url`` is SSRF-unsafe.

    Runs the blocking ``is_safe_public_url`` DNS lookup off the event loop.
    Callers translate the ``ValueError`` into their own boundary error
    (HTTPException 422 for routers, ConnectorFetchError for connectors).
    """
    safe, reason = await asyncio.to_thread(is_safe_public_url, url)
    if not safe:
        raise ValueError(reason)
