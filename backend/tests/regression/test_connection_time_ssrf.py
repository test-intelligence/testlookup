from __future__ import annotations

import asyncio
import socket

import httpcore
import httpx
import pytest

from app.core import http_client
from app.services import url_safety


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self, peer: str | None, response: bytes = b"") -> None:
        self.peer = peer
        self.closed = False
        self.tls_hostnames: list[bytes | str | None] = []
        self.response = response

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk, self.response = self.response[:max_bytes], self.response[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context,
        server_hostname: bytes | str | None = None,
        timeout: float | None = None,
    ) -> "FakeStream":
        self.tls_hostnames.append(server_hostname)
        return self

    def get_extra_info(self, info: str):
        if info == "server_addr" and self.peer is not None:
            return (self.peer, 443)
        return None


class FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, stream: FakeStream) -> None:
        self.stream = stream
        self.connects: list[tuple[str, int]] = []

    async def connect_tcp(self, host: str, port: int, **kwargs) -> FakeStream:
        self.connects.append((host, port))
        return self.stream

    async def connect_unix_socket(self, path: str, **kwargs) -> FakeStream:
        raise AssertionError("Unix socket must not be used")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


class FailingBackend(FakeBackend):
    async def connect_tcp(self, host: str, port: int, **kwargs) -> FakeStream:
        self.connects.append((host, port))
        raise url_safety.UnsafeTargetError("sentinel direct connection")


def _answer(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))


@pytest.mark.parametrize("result", [socket.gaierror("missing"), []])
def test_dns_failure_and_empty_answers_fail_closed(monkeypatch, result):
    def resolve(*_args, **_kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    safe, reason = url_safety.is_safe_public_url("https://missing.example/hook")
    assert safe is False
    assert "DNS" in reason or "address" in reason


@pytest.mark.parametrize(
    "answers",
    [
        ["93.184.216.34", "127.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["::ffff:169.254.169.254"],
    ],
)
@pytest.mark.asyncio
async def test_private_or_mixed_answers_never_reach_socket(monkeypatch, answers):
    monkeypatch.setattr(
        url_safety,
        "resolve_public_addresses",
        lambda *_args: url_safety._validated_addresses([_answer(a) for a in answers]),
    )
    raw = FakeBackend(FakeStream("93.184.216.34"))
    backend = url_safety.PublicOnlyNetworkBackend(raw)
    with pytest.raises(url_safety.UnsafeTargetError):
        await backend.connect_tcp("rebind.example", 443, timeout=1)
    assert raw.connects == []


@pytest.mark.asyncio
async def test_rebind_after_preflight_never_dials_private_peer(monkeypatch):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [_answer(a) for a in next(answers)],
    )
    assert url_safety.is_safe_public_url("https://rebind.example")[0] is True
    raw = FakeBackend(FakeStream("93.184.216.34"))
    with pytest.raises(url_safety.UnsafeTargetError):
        await url_safety.PublicOnlyNetworkBackend(raw).connect_tcp(
            "rebind.example", 443, timeout=1
        )
    assert raw.connects == []


@pytest.mark.asyncio
async def test_public_answer_is_pinned_and_origin_hostname_reaches_tls(monkeypatch):
    monkeypatch.setattr(
        url_safety, "resolve_public_addresses", lambda *_args: ["93.184.216.34"]
    )
    stream = FakeStream(
        "93.184.216.34",
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    )
    raw = FakeBackend(stream)
    transport = url_safety.PublicOnlyAsyncHTTPTransport(network_backend=raw)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://origin.example/path")
    assert response.status_code == 200
    assert raw.connects == [("93.184.216.34", 443)]
    assert stream.tls_hostnames in [["origin.example"], [b"origin.example"]]


@pytest.mark.asyncio
async def test_peer_mismatch_is_closed_and_rejected(monkeypatch):
    monkeypatch.setattr(
        url_safety, "resolve_public_addresses", lambda *_args: ["93.184.216.34"]
    )
    stream = FakeStream("127.0.0.1")
    with pytest.raises(url_safety.UnsafeTargetError, match="did not match"):
        await url_safety.PublicOnlyNetworkBackend(FakeBackend(stream)).connect_tcp(
            "origin.example", 443, timeout=1
        )
    assert stream.closed is True


@pytest.mark.asyncio
async def test_dns_resolution_obeys_connect_timeout(monkeypatch):
    def slow(*_args):
        import time

        time.sleep(0.1)
        return ["93.184.216.34"]

    monkeypatch.setattr(url_safety, "resolve_public_addresses", slow)
    raw = FakeBackend(FakeStream("93.184.216.34"))
    with pytest.raises(httpcore.ConnectTimeout):
        await url_safety.PublicOnlyNetworkBackend(raw).connect_tcp(
            "slow.example", 443, timeout=0.001
        )
    assert raw.connects == []


@pytest.mark.asyncio
async def test_each_new_connection_revalidates_dns(monkeypatch):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(
        url_safety,
        "resolve_public_addresses",
        lambda *_args: url_safety._validated_addresses(
            [_answer(address) for address in next(answers)]
        ),
    )
    raw = FakeBackend(FakeStream("93.184.216.34"))
    backend = url_safety.PublicOnlyNetworkBackend(raw)
    await backend.connect_tcp("origin.example", 443, timeout=1)
    with pytest.raises(url_safety.UnsafeTargetError):
        await backend.connect_tcp("origin.example", 443, timeout=1)
    assert raw.connects == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_public_transport_uses_safe_backend_and_ignores_proxy_environment(
    monkeypatch,
):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(
        url_safety, "resolve_public_addresses", lambda *_args: ["93.184.216.34"]
    )
    raw = FailingBackend(FakeStream("93.184.216.34"))
    transport_type = url_safety.PublicOnlyAsyncHTTPTransport

    def build_transport(**kwargs):
        return transport_type(network_backend=raw, **kwargs)

    monkeypatch.setattr(url_safety, "PublicOnlyAsyncHTTPTransport", build_transport)
    monkeypatch.setattr(http_client, "_public_client", None)
    monkeypatch.setattr(http_client, "_public_loop", None)
    client = http_client.get_public_http_client()
    transport = client._transport
    assert isinstance(transport._pool, httpcore.AsyncConnectionPool)
    assert isinstance(transport._pool._network_backend, url_safety.PublicOnlyNetworkBackend)
    assert not isinstance(transport._pool, httpcore.AsyncHTTPProxy)
    with pytest.raises(url_safety.UnsafeTargetError, match="sentinel"):
        await client.get("http://origin.example/path")
    await http_client.close_http_client()
    assert raw.connects == [("93.184.216.34", 80)]


def test_httpx_httpcore_versions_match_guarded_transport_contract():
    assert httpx.__version__ == "0.28.1"
    assert httpcore.__version__ == "1.0.9"


def test_public_tls_context_keeps_ssl_cert_file_support(monkeypatch):
    context = object()
    monkeypatch.setattr(http_client, "http_verify", lambda: True)
    monkeypatch.setattr(
        http_client.ssl, "create_default_context", lambda: context
    )
    assert http_client.public_http_verify() is context
