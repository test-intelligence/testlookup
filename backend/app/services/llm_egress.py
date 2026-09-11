"""Connection-time residency for ``AI_OFFLINE_MODE`` (re-audit N8).

The offline ceiling decides whether an endpoint is on-box by resolving its
hostname and requiring every answer to be loopback or private
(``llm_policy_service._resolves_only_to_local_addresses``). That check ran
BEFORE the client was built, and the client then resolved the name AGAIN when
it connected. A name that answered ``10.0.0.5`` to the check and a public
address to the connection (DNS rebinding, or simply a short TTL that changed
in between) passed the ceiling and sent the prompt off-box.

This module closes that gap the same way ``url_safety.PublicOnlyNetworkBackend``
closes it for SSRF, with the rule inverted: the HTTP transport's network
backend resolves the name itself, requires every answer to be non-routable,
and dials the validated numeric address. The request keeps its original
origin, so the ``Host`` header, TLS SNI and certificate checks still see the
hostname; only the TCP connect receives the pinned IP. There is no second
resolution for an attacker to answer differently, and a redirect to a public
host is refused at its own connect.

The pre-construction check in ``llm_policy_service`` stays: it refuses early
with a readable message. This backend is the one that decides.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx

__all__ = [
    "OffBoxTargetError",
    "LocalOnlyAsyncHTTPTransport",
    "LocalOnlyHTTPTransport",
    "local_only_async_client",
    "local_only_sync_client",
    "pin_ollama_clients",
    "resolve_local_addresses",
]


class OffBoxTargetError(OSError):
    """A destination could not be proved on-box at the socket boundary."""


def _local_address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    from app.services.llm_policy_service import _is_routable

    try:
        address = ipaddress.ip_address(str(raw).split("%", 1)[0])
    except ValueError as exc:
        raise OffBoxTargetError(f"DNS returned an invalid address ({raw})") from exc
    if _is_routable(address):
        raise OffBoxTargetError(
            f"AI_OFFLINE_MODE=true and the target resolves to a routable address ({raw})"
        )
    return address


def resolve_local_addresses(host: str, port: int) -> list[str]:
    """Resolve ``host`` and require EVERY TCP answer to be on-box.

    Fails closed: no answer, a malformed answer, or one routable answer among
    many local ones all raise :class:`OffBoxTargetError`.
    """
    candidate = str(host or "").strip().strip("[]")
    if not candidate:
        raise OffBoxTargetError("target host is empty")
    try:
        ipaddress.ip_address(candidate.split("%", 1)[0])
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        return [str(_local_address(candidate))]
    try:
        infos = socket.getaddrinfo(
            candidate,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except Exception as exc:  # noqa: BLE001 -- every resolver failure denies
        raise OffBoxTargetError(
            f"target DNS resolution failed ({type(exc).__name__})"
        ) from exc
    addresses: list[str] = []
    for info in infos:
        try:
            raw = str(info[4][0])
        except (IndexError, TypeError) as exc:
            raise OffBoxTargetError("DNS returned a malformed address") from exc
        normalized = str(_local_address(raw))
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise OffBoxTargetError("target host did not resolve to an address")
    return addresses


def _verify_peer(stream: Any, approved: set[str]) -> None:
    peer = stream.get_extra_info("server_addr")
    try:
        peer_address = str(ipaddress.ip_address(str(peer[0]).split("%", 1)[0]))
    except (IndexError, TypeError, ValueError) as exc:
        raise OffBoxTargetError("connected peer address could not be verified") from exc
    if peer_address not in approved:
        raise OffBoxTargetError(
            f"connected peer did not match the validated DNS answers ({peer_address})"
        )
    _local_address(peer_address)


class LocalOnlyNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve, validate, and pin each TCP connection to on-box addresses.

    The resolution runs in a worker thread, so a slow resolver never stalls
    the event loop (re-audit N11).
    """

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
                addresses = await asyncio.to_thread(resolve_local_addresses, host, port)
            else:
                async with asyncio.timeout(timeout):
                    addresses = await asyncio.to_thread(resolve_local_addresses, host, port)
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
            try:
                _verify_peer(stream, approved)
            except OffBoxTargetError:
                await stream.aclose()
                raise
            return stream
        if last_error is not None:
            raise last_error
        raise OffBoxTargetError("target host had no connectable on-box address")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise OffBoxTargetError("Unix sockets are not used for offline model endpoints")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class LocalOnlySyncNetworkBackend(httpcore.NetworkBackend):
    """The synchronous twin, for LangChain's blocking ``invoke`` paths."""

    def __init__(self, backend: httpcore.NetworkBackend | None = None) -> None:
        self._backend = backend or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = resolve_local_addresses(host, port)
        approved = set(addresses)
        last_error: Exception | None = None
        for address in addresses:
            try:
                stream = self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue
            try:
                _verify_peer(stream, approved)
            except OffBoxTargetError:
                stream.close()
                raise
            return stream
        if last_error is not None:
            raise last_error
        raise OffBoxTargetError("target host had no connectable on-box address")

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        raise OffBoxTargetError("Unix sockets are not used for offline model endpoints")

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


def _install(pool: Any, pool_type: type, backend: Any) -> None:
    if not isinstance(pool, pool_type) or not hasattr(pool, "_network_backend"):
        # Fail closed: if the internals moved, the pin is not in place and an
        # offline client must not be handed out as though it were.
        raise RuntimeError(
            "HTTPX/httpcore transport internals changed; offline connection pinning unavailable"
        )
    pool._network_backend = backend
    if pool._network_backend is not backend:
        raise RuntimeError("offline connection pinning could not be installed")


class LocalOnlyAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """Async HTTPX transport whose TCP connects are pinned to on-box peers."""

    def __init__(self, *, verify: Any = True, network_backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        super().__init__(verify=verify, trust_env=False, retries=0)
        _install(getattr(self, "_pool", None), httpcore.AsyncConnectionPool,
                 LocalOnlyNetworkBackend(network_backend))


class LocalOnlyHTTPTransport(httpx.HTTPTransport):
    """Sync HTTPX transport whose TCP connects are pinned to on-box peers."""

    def __init__(self, *, verify: Any = True, network_backend: httpcore.NetworkBackend | None = None) -> None:
        super().__init__(verify=verify, trust_env=False, retries=0)
        _install(getattr(self, "_pool", None), httpcore.ConnectionPool,
                 LocalOnlySyncNetworkBackend(network_backend))


def _verify_setting() -> Any:
    from app.core.http_client import http_verify

    return http_verify()


def local_only_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` that can only reach on-box addresses.

    An explicit transport also stops HTTPX from routing through an
    ``HTTP(S)_PROXY`` taken from the environment, which would otherwise be the
    peer the pin checked instead of the model server.
    """
    return httpx.AsyncClient(
        transport=LocalOnlyAsyncHTTPTransport(verify=_verify_setting()),
        trust_env=False,
        **kwargs,
    )


def local_only_sync_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(
        transport=LocalOnlyHTTPTransport(verify=_verify_setting()),
        trust_env=False,
        **kwargs,
    )


_shared_local_client: httpx.AsyncClient | None = None
_shared_local_loop: Any = None


def get_local_only_http_client() -> httpx.AsyncClient:
    """A pooled, loop-aware client pinned to on-box peers.

    For notification webhooks under AI_OFFLINE_MODE (re-audit N8): the
    residency gate approved the destination because it resolved on-box, so
    the connection must go to an on-box address too. Rotated when the owning
    event loop changes, as ``http_client.get_public_http_client`` is.
    """
    global _shared_local_client, _shared_local_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    loop_changed = (
        current_loop is not None
        and _shared_local_loop is not None
        and current_loop is not _shared_local_loop
    )
    if _shared_local_client is None or _shared_local_client.is_closed or loop_changed:
        _shared_local_client = local_only_async_client(
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        _shared_local_loop = current_loop
    return _shared_local_client


def _rebuilt(old: Any, factory: Any) -> Any:
    return factory(
        base_url=old.base_url,
        timeout=old.timeout,
        headers=old.headers,
        follow_redirects=old.follow_redirects,
    )


def pin_ollama_clients(model: Any) -> Any:
    """Replace the HTTP clients inside a ``ChatOllama``/``OllamaEmbeddings``.

    Both hold an ``ollama.Client`` and an ``ollama.AsyncClient``, each wrapping
    an ``httpx`` client built from ``client_kwargs``. That dict goes to BOTH,
    so it cannot carry a transport (a sync transport breaks the async client
    and the reverse); the clients are rebuilt here with the same origin,
    timeout and headers instead. Raises when the attributes are missing -- an
    unpinned offline client must not be returned as a pinned one.
    """
    sync_holder = getattr(model, "_client", None)
    async_holder = getattr(model, "_async_client", None)
    sync_inner = getattr(sync_holder, "_client", None)
    async_inner = getattr(async_holder, "_client", None)
    if (
        sync_holder is None
        or async_holder is None
        or not isinstance(sync_inner, httpx.Client)
        or not isinstance(async_inner, httpx.AsyncClient)
    ):
        raise RuntimeError("ollama client internals changed; offline connection pinning unavailable")
    sync_holder._client = _rebuilt(sync_inner, local_only_sync_client)
    async_holder._client = _rebuilt(async_inner, local_only_async_client)
    return model
