"""The Ollama availability probes follow the same rules as a model call.

The rule: before either probe sends ``GET {base_url}/api/tags`` it applies the
provider policy to that base URL. With offline mode on (from the environment
or from Settings > AI) a base URL that is not on-box gets no request at all,
and an on-box one is reached only through the client that pins the connection
to on-box addresses. A failed probe answers with a fixed word; the text of the
error, or of whatever answered, is never returned.

Before this rule the model call refused an off-box base URL under offline
mode, but ``model_status_service.probe_ollama`` (behind
``GET /settings/ai/model-status``) and
``analysis_router._probe_ollama_model_async`` still sent the GET to whatever
base URL an ADMIN had saved, and the first returned ``str(exc)``.
"""
from __future__ import annotations

import socket

import pytest

from app.core.config import settings
from app.services import analysis_router, llm_egress, llm_policy_service, model_status_service
from tests.ai_config_fakes import reset_analysis_router_state

pytestmark = pytest.mark.regression

PUBLIC_URL = "http://8.8.8.8:11434"
LOCAL_URL = "http://127.0.0.1:11434"
BANNER = "internal-banner 10.1.2.3 refused by upstream-proxy-7"


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = {"models": [{"name": "llama3:8b"}]} if payload is None else payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Client:
    """Records every GET; answers with ``response`` or raises it."""

    def __init__(self, response=None):
        self.urls: list[str] = []
        self.response = response if response is not None else _Response()

    async def get(self, url, **_kwargs):
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_analysis_router_state()
    llm_policy_service._residency_cache_clear()
    monkeypatch.setattr(settings, "AI_LLM_ALLOWED_BASE_URLS", "")
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "")

    def no_lookups(*_a, **_k):
        raise AssertionError("a name lookup was attempted")

    # Every URL here is a literal address; nothing may be resolved or dialed.
    monkeypatch.setattr(socket, "getaddrinfo", no_lookups)
    yield
    reset_analysis_router_state()
    llm_policy_service._residency_cache_clear()


@pytest.fixture
def clients(monkeypatch):
    """The shared client and the on-box-only client of the model-status probe."""
    shared, pinned = _Client(), _Client()
    monkeypatch.setattr("app.core.http_client.get_http_client", lambda: shared)
    monkeypatch.setattr(llm_egress, "get_local_only_http_client", lambda: pinned)
    return shared, pinned


# ── model_status_service.probe_ollama ────────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_the_status_probe_sends_nothing_to_a_public_base_url(clients):
    shared, pinned = clients

    result = await model_status_service.probe_ollama(PUBLIC_URL, offline=True)

    assert shared.urls == [] and pinned.urls == []
    assert result == {"reachable": False, "models": [], "error": "refused_by_policy"}


@pytest.mark.asyncio
async def test_the_status_probe_reads_the_offline_switch_itself_when_not_told(clients, monkeypatch):
    """``/health`` calls it without the flag; the environment still decides."""
    shared, pinned = clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    result = await model_status_service.probe_ollama(PUBLIC_URL)

    assert shared.urls == [] and pinned.urls == []
    assert result["error"] == "refused_by_policy"


@pytest.mark.asyncio
async def test_offline_the_status_probe_reaches_an_on_box_url_through_the_pinned_client(clients):
    shared, pinned = clients

    result = await model_status_service.probe_ollama(LOCAL_URL, offline=True)

    assert pinned.urls == [f"{LOCAL_URL}/api/tags"]
    assert shared.urls == [], "offline, the unpinned client must not be used"
    assert result == {"reachable": True, "models": ["llama3:8b"], "error": None}


@pytest.mark.asyncio
async def test_online_the_status_probe_works_as_before(clients, monkeypatch):
    shared, pinned = clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    result = await model_status_service.probe_ollama(PUBLIC_URL, offline=False)

    assert shared.urls == [f"{PUBLIC_URL}/api/tags"] and pinned.urls == []
    assert result["reachable"] is True and result["models"] == ["llama3:8b"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "word"),
    [
        (RuntimeError(BANNER), "unreachable"),
        (_Response(status_code=502), "unexpected_status"),
        (_Response(payload=ValueError(BANNER)), "invalid_response"),
        (_Response(payload=[BANNER]), "invalid_response"),
    ],
)
async def test_a_failed_status_probe_answers_with_a_fixed_word(clients, response, word):
    shared, _pinned = clients
    shared.response = response

    result = await model_status_service.probe_ollama(LOCAL_URL, offline=False)

    assert result == {"reachable": False, "models": [], "error": word}
    assert "banner" not in repr(result) and "10.1.2.3" not in repr(result)


@pytest.mark.asyncio
async def test_the_model_status_page_probes_with_the_saved_offline_switch(clients, monkeypatch):
    """Environment says online, Settings > AI says offline: the stricter one wins."""
    shared, pinned = clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    status = await model_status_service.build_model_status({
        "llm_provider": "ollama",
        "llm_model": "llama3:8b",
        "ai_offline_mode": True,
        "base_url": PUBLIC_URL,
    })

    assert shared.urls == [] and pinned.urls == []
    assert status["ollama_reachable"] is False
    assert status["ollama_error"] == "refused_by_policy"


# ── analysis_router._probe_ollama_model_async ────────────────────────────────


@pytest.fixture
def router_clients(monkeypatch):
    """The two clients the analysis-mode probe can build: plain and on-box-only."""
    import httpx

    plain, pinned = _Client(), _Client()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: plain)
    monkeypatch.setattr(llm_egress, "local_only_async_client", lambda *a, **k: pinned)
    return plain, pinned


@pytest.mark.asyncio
async def test_offline_the_mode_probe_sends_nothing_to_a_public_base_url(router_clients, monkeypatch):
    plain, pinned = router_clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    # Refused by the policy: the model cannot be used, so Auto must not pick it (D-01).
    assert await analysis_router._probe_ollama_model_async("ollama", "llama3:8b", PUBLIC_URL) is False

    assert plain.urls == [] and pinned.urls == []


@pytest.mark.asyncio
async def test_the_mode_probe_obeys_the_offline_switch_saved_in_settings(router_clients, monkeypatch):
    plain, pinned = router_clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    # What the refresh caches from Settings > AI.
    monkeypatch.setattr(analysis_router, "_effective_offline", True, raising=False)

    # Refused by the policy: the model cannot be used, so Auto must not pick it (D-01).
    assert await analysis_router._probe_ollama_model_async("ollama", "llama3:8b", PUBLIC_URL) is False

    assert plain.urls == [] and pinned.urls == []


@pytest.mark.asyncio
async def test_offline_the_mode_probe_reaches_an_on_box_url_through_the_pinned_client(
    router_clients, monkeypatch
):
    plain, pinned = router_clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    assert await analysis_router._probe_ollama_model_async("ollama", "llama3:8b", LOCAL_URL) is True

    assert pinned.urls == [f"{LOCAL_URL}/api/tags"] and plain.urls == []


@pytest.mark.asyncio
async def test_online_the_mode_probe_works_as_before(router_clients, monkeypatch):
    plain, pinned = router_clients
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    assert await analysis_router._probe_ollama_model_async("ollama", "llama3:8b", PUBLIC_URL) is True
    assert await analysis_router._probe_ollama_model_async("ollama", "mistral-nemo", PUBLIC_URL) is False

    assert plain.urls == [f"{PUBLIC_URL}/api/tags"] * 2 and pinned.urls == []


@pytest.mark.asyncio
async def test_the_refresh_caches_the_effective_offline_switch_for_the_probe(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import ai_config_resolver

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(
        analysis_router, "resolve_configured_analysis_mode",
        AsyncMock(return_value=analysis_router.ConfiguredMode("auto", "env_default")),
    )
    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", AsyncMock(return_value={
        "provider": "ollama", "model": "llama3:8b", "base_url": PUBLIC_URL, "offline_mode": True,
    }))
    seen: dict = {}

    async def probe(*_a, **_k):
        seen["offline"] = analysis_router._effective_offline
        return None

    monkeypatch.setattr(analysis_router, "_probe_ollama_model_async", probe)

    await analysis_router.refresh_analysis_mode_from_cache(db=object())

    assert seen == {"offline": True}
