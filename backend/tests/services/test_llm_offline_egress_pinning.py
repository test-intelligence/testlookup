"""Re-audit N8 (connect-time residency pin), N11 (no blocking DNS on the loop)
and L2 (AI_MAX_RETRIES governs the LLM clients).

N8: the offline ceiling resolved a hostname, found it on-box, and then let the
client resolve it AGAIN to connect. These tests drive a real HTTPX transport
with a recording network backend and prove the connection goes to the address
that was validated, and that a name answering a public address at connect time
is refused before anything is dialed.
"""
from __future__ import annotations

import asyncio
import socket
import time

import httpcore
import httpx
import pytest

from app.core.config import settings
from app.services import llm_egress, llm_policy_service
from app.services.llm_egress import (
    LocalOnlyAsyncHTTPTransport,
    LocalOnlyHTTPTransport,
    OffBoxTargetError,
    resolve_local_addresses,
)
from app.services.llm_factory import BudgetedLLM, get_llm
from app.services.llm_policy_service import (
    LLMPolicyViolation,
    _residency_cache_clear,
    enforce_provider_policy,
    enforce_provider_policy_async,
)

PUBLIC = "93.184.216.34"
LOCAL = "10.0.0.7"


def _answers(*addresses, delay: float = 0.0):
    calls = []

    def fake(host, port=None, family=0, type=0, proto=0, flags=0):  # noqa: A002
        calls.append(host)
        if delay:
            time.sleep(delay)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0)) for a in addresses]

    fake.calls = calls
    return fake


class _Stream(httpcore.AsyncNetworkStream):
    def __init__(self, peer: str, port: int):
        self._addr = (peer, port)
        self._chunks = [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"]

    async def read(self, max_bytes, timeout=None):
        return self._chunks.pop(0) if self._chunks else b""

    async def write(self, buffer, timeout=None):
        return None

    async def aclose(self):
        return None

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info):
        return self._addr if info == "server_addr" else None


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, peer: str | None = None):
        self.dialed: list[str] = []
        self.peer = peer

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialed.append(host)
        return _Stream(self.peer or host, port)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("unexpected unix socket")

    async def sleep(self, seconds):
        return None


def _client(backend) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=LocalOnlyAsyncHTTPTransport(network_backend=backend))


# ── N8: the connect-time pin ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_connection_goes_to_the_validated_address_not_the_name(monkeypatch):
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(LOCAL))
    backend = _RecordingBackend()
    async with _client(backend) as client:
        response = await client.get("http://model.internal:11434/api/tags")
    assert response.status_code == 200
    assert response.text == "ok"
    # The TCP connect received the numeric address the check approved; the
    # hostname never reached a second resolver.
    assert backend.dialed == [LOCAL]


@pytest.mark.asyncio
async def test_a_name_that_rebinds_to_a_public_address_is_refused_before_dialing(monkeypatch):
    # The pre-construction check saw an on-box answer...
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _answers(LOCAL))
    _residency_cache_clear()
    enforce_provider_policy("vllm", offline=True, base_url="http://model.internal:8000")
    # ...and the resolver answers differently when the client connects.
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(PUBLIC))
    backend = _RecordingBackend()
    async with _client(backend) as client:
        with pytest.raises(OffBoxTargetError, match="routable"):
            await client.get("http://model.internal:8000/v1/models")
    assert backend.dialed == []


@pytest.mark.asyncio
async def test_one_public_answer_among_local_ones_is_refused(monkeypatch):
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(LOCAL, PUBLIC))
    backend = _RecordingBackend()
    async with _client(backend) as client:
        with pytest.raises(OffBoxTargetError):
            await client.get("http://model.internal:8000/")
    assert backend.dialed == []


@pytest.mark.asyncio
async def test_a_peer_that_is_not_the_validated_address_is_refused(monkeypatch):
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(LOCAL))
    backend = _RecordingBackend(peer="10.9.9.9")
    async with _client(backend) as client:
        with pytest.raises(OffBoxTargetError, match="did not match"):
            await client.get("http://model.internal:8000/")


def test_metadata_and_link_local_answers_are_not_on_box(monkeypatch):
    for address in ("169.254.169.254", "fe80::1"):
        monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(address))
        with pytest.raises(OffBoxTargetError):
            resolve_local_addresses("model.internal", 80)


def test_an_unresolvable_name_fails_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", boom)
    with pytest.raises(OffBoxTargetError, match="resolution failed"):
        resolve_local_addresses("model.internal", 80)


def test_the_sync_transport_pins_too(monkeypatch):
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(PUBLIC))
    with httpx.Client(transport=LocalOnlyHTTPTransport()) as client:
        with pytest.raises(OffBoxTargetError):
            client.get("http://model.internal:8000/")


@pytest.mark.asyncio
async def test_offline_get_llm_pins_every_local_provider(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _answers(LOCAL))
    _residency_cache_clear()
    ollama = await get_llm(provider="ollama", model="m")
    assert isinstance(ollama._inner._client._client._transport, LocalOnlyHTTPTransport)
    assert isinstance(ollama._inner._async_client._client._transport, LocalOnlyAsyncHTTPTransport)
    for provider in ("vllm", "lmstudio", "localai"):
        chat = await get_llm(provider=provider, model="m")
        assert isinstance(chat._inner.root_async_client._client._transport, LocalOnlyAsyncHTTPTransport), provider
        assert isinstance(chat._inner.root_client._client._transport, LocalOnlyHTTPTransport), provider


@pytest.mark.asyncio
async def test_online_get_llm_does_not_pin(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    chat = await get_llm(provider="vllm", model="m")
    assert not isinstance(chat._inner.root_async_client._client._transport, LocalOnlyAsyncHTTPTransport)


@pytest.mark.asyncio
async def test_an_offline_model_call_to_a_rebound_name_never_leaves_the_box(monkeypatch):
    """End to end through the real OpenAI SDK client get_llm builds."""
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "AI_MAX_RETRIES", 0)
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _answers(LOCAL))
    _residency_cache_clear()
    chat = await get_llm(provider="vllm", model="m")
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(PUBLIC))
    dialed = []

    async def recording_connect(self, host, port, *args, **kwargs):
        dialed.append(host)
        raise AssertionError("dialed an address the pin did not approve")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", recording_connect)
    with pytest.raises(Exception) as caught:
        await chat.ainvoke("hello")
    chain, current = [], caught.value
    while current is not None:
        chain.append(current)
        current = current.__cause__ or current.__context__
    assert any(isinstance(e, OffBoxTargetError) for e in chain), chain
    assert dialed == []


@pytest.mark.asyncio
async def test_an_offline_webhook_is_posted_through_the_pinned_client(monkeypatch):
    from app.services.notification import egress, slack_service

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "")
    monkeypatch.setattr(egress, "host_is_local", lambda host: True)  # the gate saw on-box
    monkeypatch.setattr(llm_egress.socket, "getaddrinfo", _answers(PUBLIC))  # the connect sees public
    with pytest.raises(OffBoxTargetError):
        await slack_service.send_notification(
            "http://hooks.internal/services/x", "t", "b", "test_failed",
        )


def test_the_allow_listed_deployment_host_keeps_the_public_client(monkeypatch):
    from app.core.http_client import get_public_http_client
    from app.services.notification.egress import delivery_http_client

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "OFFLINE_NOTIFICATION_ALLOWED_HOSTS", "hooks.slack.com")
    url = "https://hooks.slack.com/services/x"
    assert delivery_http_client(url, deployment_wide=True) is get_public_http_client()
    # A user's webhook on the same host is judged by residency, so it is pinned.
    pinned = delivery_http_client(url, deployment_wide=False)
    assert isinstance(pinned._transport, LocalOnlyAsyncHTTPTransport)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    assert delivery_http_client(url) is get_public_http_client()


# ── N11: the residency lookup does not block the event loop ─────────────────


async def _ticks_while(awaitable) -> int:
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    start = ticks
    try:
        await awaitable
    finally:
        stop = True
        await task
    return ticks - start


@pytest.mark.asyncio
async def test_the_async_policy_check_keeps_the_loop_running(monkeypatch):
    _residency_cache_clear()
    slow = _answers(LOCAL, delay=0.4)
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", slow)
    ticks = await _ticks_while(
        enforce_provider_policy_async("vllm", offline=True, base_url="http://slow-model.internal:8000")
    )
    assert slow.calls == ["slow-model.internal"]
    assert ticks >= 10, f"the loop ticked {ticks} times during a 0.4 s lookup"


@pytest.mark.asyncio
async def test_the_control_the_sync_check_does_block(monkeypatch):
    """Proves the ticker can see a blocked loop at all."""
    _residency_cache_clear()
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _answers(LOCAL, delay=0.4))

    async def sync_in_coroutine():
        enforce_provider_policy("vllm", offline=True, base_url="http://slow-model.internal:8000")

    assert await _ticks_while(sync_in_coroutine()) <= 1


@pytest.mark.asyncio
async def test_get_llm_keeps_the_loop_running(monkeypatch):
    _residency_cache_clear()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr(settings, "VLLM_BASE_URL", "http://slow-model.internal:8000")
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _answers(LOCAL, delay=0.4))
    assert await _ticks_while(get_llm(provider="vllm", model="m")) >= 10


@pytest.mark.asyncio
async def test_the_async_check_still_refuses_a_public_host(monkeypatch):
    _residency_cache_clear()
    monkeypatch.setattr(llm_policy_service.socket, "getaddrinfo", _answers(PUBLIC))
    with pytest.raises(LLMPolicyViolation, match="off-box"):
        await enforce_provider_policy_async("vllm", offline=True, base_url="http://x.example:8000")


# ── L2: AI_MAX_RETRIES governs the LLM calls ────────────────────────────────


class _Flaky:
    def __init__(self, failures: list[BaseException]):
        self.failures = list(failures)
        self.calls = 0

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "answer"


def _budgeted(inner, retries, monkeypatch):
    monkeypatch.setattr(BudgetedLLM, "_retry_delay", staticmethod(lambda attempt: 0.0))
    return BudgetedLLM(inner, provider="ollama", model="m", connect_retries=retries)


@pytest.mark.asyncio
async def test_connect_failures_are_retried_up_to_the_setting(monkeypatch):
    request = httpx.Request("POST", "http://model.internal")
    inner = _Flaky([httpx.ConnectError("refused", request=request)] * 2)
    assert await _budgeted(inner, 2, monkeypatch).ainvoke("x") == "answer"
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_retries_stop_at_the_setting(monkeypatch):
    request = httpx.Request("POST", "http://model.internal")
    inner = _Flaky([httpx.ConnectError("refused", request=request)] * 3)
    with pytest.raises(httpx.ConnectError):
        await _budgeted(inner, 2, monkeypatch).ainvoke("x")
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_a_read_timeout_and_a_policy_refusal_are_not_retried(monkeypatch):
    request = httpx.Request("POST", "http://model.internal")
    for failure in (httpx.ReadTimeout("slow", request=request), OffBoxTargetError("public")):
        inner = _Flaky([failure])
        with pytest.raises(type(failure)):
            await _budgeted(inner, 3, monkeypatch).ainvoke("x")
        assert inner.calls == 1, type(failure).__name__


@pytest.mark.asyncio
async def test_get_llm_hands_ai_max_retries_to_every_client(monkeypatch):
    monkeypatch.setattr(settings, "AI_MAX_RETRIES", 5)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    ollama = await get_llm(provider="ollama", model="m")
    assert ollama._connect_retries == 5
    assert ollama.bind(stop=["x"])._connect_retries == 5
    for provider in ("vllm", "lmstudio", "localai"):
        assert (await get_llm(provider=provider, model="m"))._inner.max_retries == 5
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    assert (await get_llm(provider="openai", model="gpt-4o-mini"))._inner.max_retries == 5
