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

Two policies (W10)
------------------

* **Public only** -- ``is_safe_public_url`` / ``assert_public_url`` /
  ``PublicOnlyAsyncHTTPTransport``: for a URL any user or QA lead types that
  the server then POSTs data to (outbound webhooks, knowledge-source URLs, SSO
  metadata). Every non-global address is refused (RFC1918 included) and a DNS
  failure fails closed.
* **Integration egress** -- ``egress_block_reason``: for an integration base
  an ADMIN (Splunk, OpenShift, Jira) or a QA lead (per-project GitHub /
  GitLab) configures, to which the server sends that integration's
  credential. Only loopback, link-local (169.254.169.254 included) and
  unspecified addresses are refused: RFC1918 stays allowed (on-prem Splunk,
  OpenShift, GitHub Enterprise, GitLab) and a host that does not resolve is
  allowed (setup, air-gapped DNS -- nothing is reachable through it). It runs
  at save and again at use, before the credential is sent.
  ``github_checks_service._ssrf_block_reason`` is this same function under its
  old name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Iterable
from typing import Any, Optional
from urllib.parse import urlparse

import httpcore
import httpx

__all__ = [
    "UnsafeTargetError",
    "PublicOnlyAsyncHTTPTransport",
    "assert_public_url",
    "egress_block_reason",
    "is_safe_public_url",
    "is_unsafe_target_error",
    "resolve_public_addresses",
]


class UnsafeTargetError(OSError):
    """A destination could not be proved safe at the socket boundary."""


def _public_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise UnsafeTargetError(f"DNS returned an invalid address ({raw})") from exc
    mapped = getattr(address, "ipv4_mapped", None)
    if not address.is_global or (mapped is not None and not mapped.is_global):
        raise UnsafeTargetError(f"target resolves to a non-public address ({raw})")
    return address


def _validated_addresses(infos: Iterable[tuple[Any, ...]]) -> list[str]:
    addresses: list[str] = []
    for info in infos:
        try:
            raw = str(info[4][0])
        except (IndexError, TypeError) as exc:
            raise UnsafeTargetError("DNS returned a malformed address") from exc
        address = _public_ip(raw)
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise UnsafeTargetError("target host did not resolve to an address")
    return addresses


def resolve_public_addresses(host: str, port: int) -> list[str]:
    """Resolve ``host`` and require every TCP answer to be globally routable."""
    try:
        infos = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except Exception as exc:
        raise UnsafeTargetError(
            f"target DNS resolution failed ({type(exc).__name__})"
        ) from exc
    return _validated_addresses(infos)


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Return ``(safe, reason)`` for fetching ``url`` server-side.

    ``safe is False`` carries a human-readable ``reason``; ``safe is True``
    carries an empty reason.

    DNS failure and empty or malformed answers fail closed. Every returned
    address must be globally routable; mixed public/private answers are denied.

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
        resolve_public_addresses(host, port)
    except UnsafeTargetError as exc:
        return False, str(exc)
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


async def egress_block_reason(url: str) -> Optional[str]:
    """Why the server must not send an integration's credential to ``url``'s
    host, or ``None`` when it may.

    ``"invalid_url"`` when the URL names no host; ``"blocked_target:<ip>"``
    when ANY resolved address is loopback, link-local (169.254.169.254, cloud
    metadata) or unspecified. RFC1918 is ALLOWED (an on-prem Splunk,
    OpenShift, GitHub Enterprise or GitLab); a host that does not resolve is
    ALLOWED (initial setup, air-gapped DNS: nothing is reachable through it).
    Run at egress time, so it also covers DNS rebinding and rows saved before
    the check existed. DNS runs off the event loop.

    Moved verbatim from ``github_checks_service._ssrf_block_reason`` (W10),
    which remains importable there as this same object.
    """
    host = (urlparse(url).hostname or "").strip()
    if not host:
        return "invalid_url"
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except Exception:
        return None  # non-resolving -> allow (setup-friendly, no SSRF reach)
    for info in infos:
        raw = str(info[4][0])
        try:
            addr = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            return f"blocked_target:{raw}"
    return None


def is_unsafe_target_error(exc: BaseException) -> bool:
    """Return whether an exception chain contains a socket-policy rejection."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, UnsafeTargetError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


class PublicOnlyNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve, validate, and pin each outbound TCP connection to public IPs."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        started = time.monotonic()
        try:
            if timeout is None:
                addresses = await asyncio.to_thread(resolve_public_addresses, host, port)
            else:
                async with asyncio.timeout(timeout):
                    addresses = await asyncio.to_thread(
                        resolve_public_addresses, host, port
                    )
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("DNS resolution deadline expired") from exc
        approved = set(addresses)
        last_error: Exception | None = None
        for address in addresses:
            remaining = None
            if timeout is not None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise httpcore.ConnectTimeout("connection deadline expired")
            try:
                stream = await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue

            peer = stream.get_extra_info("server_addr")
            try:
                peer_address = str(ipaddress.ip_address(str(peer[0])))
            except (IndexError, TypeError, ValueError) as exc:
                await stream.aclose()
                raise UnsafeTargetError(
                    "connected peer address could not be verified"
                ) from exc
            if peer_address not in approved:
                await stream.aclose()
                raise UnsafeTargetError(
                    f"connected peer did not match approved DNS answers ({peer_address})"
                )
            _public_ip(peer_address)
            return stream
        if last_error is not None:
            raise last_error
        raise UnsafeTargetError("target host had no connectable public address")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeTargetError("Unix sockets are not allowed for public URL fetches")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PublicOnlyAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport whose TCP backend enforces connection-time SSRF policy.

    HTTPX 0.28.1 is pinned in requirements. Keeping the request origin intact
    preserves the hostname used for HTTP Host, TLS SNI, and certificate checks;
    only the network backend receives the validated numeric destination.
    """

    def __init__(
        self,
        *,
        verify: Any = True,
        limits: httpx.Limits | None = None,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        super().__init__(
            verify=verify,
            trust_env=False,
            limits=limits or httpx.Limits(),
            retries=0,
        )
        pool = getattr(self, "_pool", None)
        if not isinstance(pool, httpcore.AsyncConnectionPool):
            raise RuntimeError(
                "HTTPX transport internals changed; public URL policy unavailable"
            )
        if not hasattr(pool, "_network_backend"):
            raise RuntimeError(
                "HTTPcore transport internals changed; public URL policy unavailable"
            )
        safe_backend = PublicOnlyNetworkBackend(network_backend)
        pool._network_backend = safe_backend
        if pool._network_backend is not safe_backend:
            raise RuntimeError("Public URL connection policy could not be installed")
