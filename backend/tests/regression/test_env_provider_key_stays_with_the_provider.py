"""A provider key from the environment is only ever sent to that provider.

The rule: when the LLM base URL is not the provider's own endpoint, the model
call uses a key the ADMIN stored in Settings > AI Configuration for that
endpoint, or it does not happen. The key in the deployment's environment
(``OPENAI_API_KEY``, ``OPENROUTER_API_KEY``) belongs to whoever runs the
deployment; an ADMIN who can type a base URL must not be able to have it
posted to a host of their choosing.

Before this rule, ``PUT /settings/ai`` with ``{"llm_provider": "openai",
"base_url": "https://listener.example/v1"}`` made the next model call send
``Authorization: Bearer <OPENAI_API_KEY from the environment>`` to that host.

These tests drive the real ``get_llm`` wrapper and the real OpenAI SDK client
it builds. Only the last hop is replaced (the ``send`` of the HTTP client class
the SDK is built on), so each test can say which Authorization value would
have gone to which host.
"""
from __future__ import annotations

import json
import socket
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import ai_config_resolver, llm_circuit_breaker, llm_policy_service
from app.services import llm_cost_reservation as cost
from app.services.llm_cost_reservation import Reservation
from app.services.llm_factory import get_llm
from app.services.llm_policy_service import (
    LLMPolicyViolation,
    enforce_provider_policy,
    enforce_provider_policy_async,
)
from tests.ai_config_fakes import RecordingRedis, StoredConfigSession, install

pytestmark = pytest.mark.regression

ENV_OPENAI = "env-openai-key-not-real"
ENV_OPENROUTER = "env-openrouter-key-not-real"
STORED_OPENAI = "stored-openai-key-not-real"
LISTENER = "https://listener.example/v1"

_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-test",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture
def sent(monkeypatch):
    """Every outbound request as ``(host, Authorization)``; nothing leaves the process."""
    from openai._base_client import AsyncHttpxClientWrapper

    # The SDK's HTTP library differs between SDK releases; take whichever
    # ``AsyncClient`` its own client class extends.
    client_class = next(c for c in AsyncHttpxClientWrapper.__mro__ if c.__name__ == "AsyncClient")
    http = sys.modules[client_class.__module__.split(".")[0]]
    requests: list[tuple[str, str | None]] = []

    async def record(self, request, **_kwargs):
        requests.append((request.url.host, request.headers.get("authorization")))
        return http.Response(200, json=_COMPLETION, request=request)

    def no_lookups(*_a, **_k):
        raise AssertionError("a name lookup was attempted: a request got past the stub")

    monkeypatch.setattr(client_class, "send", record)
    monkeypatch.setattr(socket, "getaddrinfo", no_lookups)

    # The wrapper's own gates, stubbed at their stores only.
    async def nothing(*_a, **_k):
        return None

    async def reserve(provider, model, *, input_tokens, max_output_tokens):
        return Reservation(key="k", project_id="p", estimated_usd=0.01, ttl_seconds=60)

    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(settings, "AI_MAX_RETRIES", 0)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", nothing)
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(nothing))
    monkeypatch.setattr(llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(nothing))
    monkeypatch.setattr(cost, "reserve", reserve)
    monkeypatch.setattr(cost, "settle", nothing)
    return requests


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "AI_LLM_ALLOWED_BASE_URLS", "")
    monkeypatch.setattr(settings, "AI_LLM_PROVIDER_ALLOWLIST", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", ENV_OPENAI)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", ENV_OPENROUTER)
    monkeypatch.setattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    for name in ("OPENAI_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


async def _saved_settings(monkeypatch, stored: dict, *, secrets: dict | None = None) -> dict:
    """Serve the effective config the REAL resolver builds from a saved row.

    ``secrets`` are the keys stored in Settings (the ``secret_refs`` table).
    """
    secrets = secrets or {}

    async def read_secret(_db, scope, key_name):
        return secrets.get(key_name) if scope == "ai_config" else None

    monkeypatch.setattr("app.services.secret_service.read_secret", read_secret)
    config = await ai_config_resolver._load_from_db_and_env(db=StoredConfigSession(stored))
    monkeypatch.setattr(ai_config_resolver, "get_effective_ai_config", AsyncMock(return_value=config))
    return config


async def _one_model_call() -> None:
    chat = await get_llm()
    await chat.ainvoke("hello")


# ── Refused: the environment key does not follow an ADMIN-chosen URL ─────────


@pytest.mark.asyncio
async def test_the_environment_openai_key_is_not_sent_to_a_custom_base_url(monkeypatch, sent):
    """The reported request: provider ``openai``, a listener as the base URL."""
    await _saved_settings(monkeypatch, {"llm_provider": "openai", "base_url": LISTENER})

    with pytest.raises(LLMPolicyViolation) as refused:
        await _one_model_call()

    assert sent == [], f"a request left the process: {sent}"
    # The refusal says what was refused and what to do instead.
    message = str(refused.value)
    assert "listener.example" in message
    assert "Settings > AI Configuration" in message
    assert ENV_OPENAI not in message


@pytest.mark.asyncio
async def test_the_environment_openrouter_key_is_not_sent_to_a_custom_base_url(monkeypatch, sent):
    await _saved_settings(monkeypatch, {"llm_provider": "openrouter", "base_url": LISTENER})

    with pytest.raises(LLMPolicyViolation):
        await _one_model_call()

    assert sent == []


@pytest.mark.asyncio
async def test_a_lookalike_of_the_provider_origin_is_still_a_custom_endpoint(monkeypatch, sent):
    """``api.openai.com`` as the user-info part, or as a subdomain label, is not OpenAI."""
    for url in (
        "https://api.openai.com@listener.example/v1",
        "https://api.openai.com.listener.example/v1",
        "http://api.openai.com/v1",  # not the provider's https origin
    ):
        await _saved_settings(monkeypatch, {"llm_provider": "openai", "base_url": url})
        with pytest.raises(LLMPolicyViolation):
            await _one_model_call()
    assert sent == []


@pytest.mark.asyncio
async def test_a_project_tier_endpoint_gets_the_same_rule(monkeypatch, sent):
    """An agent's own endpoint names a base URL too; credentials are global."""
    from app.services.agent_config_resolver import ResolvedEndpoint

    await _saved_settings(monkeypatch, {"llm_provider": "ollama"})
    endpoint = ResolvedEndpoint(
        provider="openai", model="gpt-test", temperature=0.0, max_tokens=16,
        base_url=LISTENER, source="project",
    )
    with pytest.raises(LLMPolicyViolation):
        chat = await get_llm(endpoint=endpoint)
        await chat.ainvoke("hello")
    assert sent == []


@pytest.mark.asyncio
async def test_a_cached_config_that_does_not_say_where_the_key_came_from_is_refused(monkeypatch, sent):
    """An entry written by an older build has no ``stored_api_keys``: not proof."""
    config = await _saved_settings(
        monkeypatch, {"llm_provider": "openai", "base_url": LISTENER},
        secrets={"openai_api_key": STORED_OPENAI},
    )
    config.pop("stored_api_keys")
    with pytest.raises(LLMPolicyViolation):
        await _one_model_call()
    assert sent == []


def test_the_synchronous_check_refuses_when_nobody_vouches_for_the_key():
    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy("openai", offline=False, base_url=LISTENER)
    with pytest.raises(LLMPolicyViolation):
        enforce_provider_policy("openai", offline=False, base_url=LISTENER, key_from_settings=False)


# ── Still works ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_key_stored_in_settings_goes_to_the_custom_base_url(monkeypatch, sent):
    """A gateway the ADMIN configured with its own key keeps working."""
    config = await _saved_settings(
        monkeypatch, {"llm_provider": "openai", "base_url": LISTENER},
        secrets={"openai_api_key": STORED_OPENAI},
    )
    assert config["stored_api_keys"] == ["openai_api_key"]

    await _one_model_call()

    assert sent == [("listener.example", f"Bearer {STORED_OPENAI}")]


@pytest.mark.asyncio
async def test_the_environment_key_still_goes_to_the_provider_itself(monkeypatch, sent):
    config = await _saved_settings(monkeypatch, {"llm_provider": "openai"})
    assert config["stored_api_keys"] == []

    await _one_model_call()

    assert sent == [("api.openai.com", f"Bearer {ENV_OPENAI}")]


@pytest.mark.asyncio
async def test_the_provider_origin_written_out_counts_as_the_provider(monkeypatch, sent):
    await _saved_settings(
        monkeypatch, {"llm_provider": "openai", "base_url": "https://API.OpenAI.com:443/v1"}
    )
    await _one_model_call()
    assert sent == [("api.openai.com", f"Bearer {ENV_OPENAI}")]


@pytest.mark.asyncio
async def test_openrouter_on_its_own_origin_keeps_the_environment_key(monkeypatch, sent):
    await _saved_settings(monkeypatch, {"llm_provider": "openrouter"})
    await _one_model_call()
    assert sent == [("openrouter.ai", f"Bearer {ENV_OPENROUTER}")]


@pytest.mark.asyncio
async def test_a_local_provider_with_a_custom_base_url_is_unaffected():
    """Local providers carry no provider key; the rule is not about them."""
    profile = await enforce_provider_policy_async(
        "vllm", offline=False, base_url="https://gpu-box.example/v1"
    )
    assert profile.provider == "vllm"
    assert enforce_provider_policy("ollama", offline=False, base_url="http://10.0.0.5:11434")


def test_the_synchronous_check_passes_when_the_caller_vouches_for_the_key():
    assert enforce_provider_policy(
        "openai", offline=False, base_url=LISTENER, key_from_settings=True
    ).provider == "openai"
    assert enforce_provider_policy(
        "openai", offline=False, base_url="https://api.openai.com/v1"
    ).provider == "openai"


def test_the_allow_list_still_refuses_an_unlisted_origin_even_with_a_stored_key(monkeypatch):
    """``AI_LLM_ALLOWED_BASE_URLS`` works as before; a stored key does not widen it."""
    monkeypatch.setattr(settings, "AI_LLM_ALLOWED_BASE_URLS", "https://gateway.example")
    with pytest.raises(LLMPolicyViolation, match="AI_LLM_ALLOWED_BASE_URLS"):
        enforce_provider_policy("openai", offline=False, base_url=LISTENER, key_from_settings=True)
    assert enforce_provider_policy(
        "openai", offline=False, base_url="https://gateway.example/v1", key_from_settings=True
    )
    # Listing an origin does not, by itself, send it the environment key.
    with pytest.raises(LLMPolicyViolation, match="Settings > AI Configuration"):
        enforce_provider_policy("openai", offline=False, base_url="https://gateway.example/v1")


# ── PUT /settings/ai answers 422 at save time ────────────────────────────────


def _stub_put(monkeypatch):
    from app.routers import app_settings as router_mod

    monkeypatch.setattr(router_mod, "log_settings_change", AsyncMock())
    monkeypatch.setattr(router_mod, "store_secret", AsyncMock())
    monkeypatch.setattr(router_mod, "_ml_status", AsyncMock(return_value={
        "ml_model_available": False,
        "ml_model_accuracy": None,
        "ml_training_sample_count": 0,
        "ml_human_label_count": 0,
        "ml_human_label_floor": 50,
        "ml_maturity": "not_trained",
    }))
    monkeypatch.setattr("app.services.feature_flags.is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.agent_authority_lock.lock_global_agent_authority", AsyncMock())
    install(monkeypatch, redis=RecordingRedis())
    return router_mod


async def _put(router_mod, db, **fields):
    from app.models.schemas import AIConfigUpdate

    return await router_mod.update_ai_config(
        payload=AIConfigUpdate(**fields), current_user=MagicMock(id=uuid.uuid4()), db=db,
    )


@pytest.mark.asyncio
async def test_saving_a_custom_base_url_without_a_stored_key_is_a_422(monkeypatch):
    router_mod = _stub_put(monkeypatch)
    db = StoredConfigSession({"llm_provider": "ollama"})

    with pytest.raises(HTTPException) as refused:
        await _put(router_mod, db, llm_provider="openai", base_url=LISTENER)

    assert refused.value.status_code == 422
    assert "Settings > AI Configuration" in refused.value.detail
    assert "listener.example" in refused.value.detail
    assert db.stored == {"llm_provider": "ollama"}, "the refused value was stored"
    assert not db.committed
    router_mod.store_secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_saving_a_base_url_that_is_not_a_url_is_a_422(monkeypatch):
    router_mod = _stub_put(monkeypatch)
    db = StoredConfigSession({"llm_provider": "ollama"})
    with pytest.raises(HTTPException) as refused:
        await _put(router_mod, db, llm_provider="openai", base_url="file:///etc/passwd")
    assert refused.value.status_code == 422
    assert not db.committed


@pytest.mark.asyncio
async def test_saving_a_custom_base_url_together_with_its_key_is_accepted(monkeypatch):
    router_mod = _stub_put(monkeypatch)
    db = StoredConfigSession({"llm_provider": "ollama"})

    out = await _put(router_mod, db, llm_provider="openai", base_url=LISTENER, openai_api_key=STORED_OPENAI)

    assert out.llm_provider == "openai"
    assert db.stored["base_url"] == LISTENER
    assert STORED_OPENAI not in json.dumps(db.stored)
    router_mod.store_secret.assert_awaited_once()


@pytest.mark.asyncio
async def test_saving_a_custom_base_url_when_a_key_is_already_stored_is_accepted(monkeypatch):
    router_mod = _stub_put(monkeypatch)
    monkeypatch.setattr("app.services.secret_service.has_secret", AsyncMock(return_value=True))
    db = StoredConfigSession({"llm_provider": "openai"})

    await _put(router_mod, db, llm_provider="openai", base_url=LISTENER)

    assert db.stored["base_url"] == LISTENER


@pytest.mark.asyncio
async def test_saving_a_local_provider_base_url_needs_no_key(monkeypatch):
    router_mod = _stub_put(monkeypatch)
    db = StoredConfigSession({"llm_provider": "ollama"})

    await _put(router_mod, db, llm_provider="ollama", base_url="http://ollama.internal:11434")

    assert db.stored["base_url"] == "http://ollama.internal:11434"


def test_the_rule_names_only_providers_whose_client_follows_the_base_url():
    """If a third remote provider starts honouring ``base_url``, add it to the map."""
    assert set(llm_policy_service.PROVIDER_KEY_SETTINGS) == {"openai", "openrouter"}
    assert SimpleNamespace(**llm_policy_service.PROVIDER_KEY_SETTINGS).openai == "openai_api_key"
