"""W12 SRCH-02: ``services/embedding_function.py`` (contract §1.2, §1.3, §2.4).

Which ChromaDB embedding function a generation uses, and what this deployment
refuses: ``default`` needs none (the call sites omit the kwarg); Ollama passes
the provider policy BEFORE it is built, is pinned on-box offline and carries
the TLS policy online; OpenAI is refused offline and without a key.

No network: building ChromaDB's functions constructs clients and never calls
them, and every base URL here is an IP literal (the residency rule needs no
resolver).
"""
from __future__ import annotations

import ssl

import pytest

pytest.importorskip("chromadb")

import httpx  # noqa: E402
from chromadb.utils.embedding_functions import (  # noqa: E402
    ollama_embedding_function as ollama_ef_module,
)
from chromadb.utils.embedding_functions.openai_embedding_function import (  # noqa: E402
    OpenAIEmbeddingFunction,
)

from app.services import embedding_function as ef_mod  # noqa: E402
from app.services.chroma_collections import (  # noqa: E402
    LEGACY_GENERATION,
    ResolvedCollection,
)
from app.services.embedding_function import (  # noqa: E402
    DEFAULT_EMBEDDER,
    DEFAULT_EMBEDDING_MODEL,
    MODEL_REQUIRED,
    OPENAI_KEY_MISSING,
    OPENAI_OFFLINE_REFUSED,
    PROVIDER_UNSUPPORTED,
    EmbedderRefused,
    EmbedderSpec,
    clear_embedding_function_cache,
    get_chroma_embedding_function,
    validate_embedder,
)
from app.services.llm_egress import LocalOnlyHTTPTransport  # noqa: E402

ON_BOX = "http://127.0.0.1:11434"
OFF_BOX = "http://8.8.8.8:11434"
OFF_BOX_REFUSAL = (
    "AI_OFFLINE_MODE=true but LLM base_url host '8.8.8.8' is "
    "not a loopback/private address — refusing to send prompts off-box"
)
OLLAMA = EmbedderSpec("ollama", "nomic-embed-text:v1.5")
OPENAI = EmbedderSpec("openai", "text-embedding-3-small")


@pytest.fixture(autouse=True)
def _fresh_memo():
    clear_embedding_function_cache()
    yield
    clear_embedding_function_cache()


@pytest.fixture
def events(monkeypatch):
    """Record the provider policy call and the OllamaEmbeddingFunction construction, in order."""
    recorded: list[tuple] = []
    from app.services import llm_policy_service

    real_policy = llm_policy_service.enforce_provider_policy

    def _policy(provider, *, offline, base_url=None):
        recorded.append(("policy", provider, offline, base_url))
        return real_policy(provider, offline=offline, base_url=base_url)

    real_class = ollama_ef_module.OllamaEmbeddingFunction

    class _Recording(real_class):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            recorded.append(("construct", args, kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(llm_policy_service, "enforce_provider_policy", _policy)
    monkeypatch.setattr(ollama_ef_module, "OllamaEmbeddingFunction", _Recording)
    return recorded


def _inner(ef) -> httpx.Client:
    """The one sync httpx client inside the function's ``ollama.Client``."""
    client = ef._client._client
    assert isinstance(client, httpx.Client)
    return client


# ── the sentences (contract §1.2, verbatim) ──────────────────────────────────

def test_the_refusal_sentences_are_the_contract_text():
    assert OPENAI_OFFLINE_REFUSED == (
        "OpenAI embeddings are refused while offline mode is on: choose Bundled MiniLM or Ollama."
    )
    assert OPENAI_KEY_MISSING == (
        "Set the OpenAI API key under Cloud API Keys before choosing OpenAI embeddings."
    )
    assert PROVIDER_UNSUPPORTED == "Embedding provider must be one of: default, ollama, openai."
    assert MODEL_REQUIRED == "Name the embedding model (for example nomic-embed-text:v1.5)."
    assert ef_mod.EMBEDDING_PROVIDERS == ("default", "ollama", "openai")
    assert ef_mod.EMBED_TIMEOUT_SECONDS == 30


# ── default: no embedding function, no kwarg ─────────────────────────────────

@pytest.mark.parametrize("offline", [True, False])
def test_default_answers_none_so_the_call_sites_omit_the_kwarg(offline, events):
    assert get_chroma_embedding_function(DEFAULT_EMBEDDER, offline=offline, ollama_base_url=OFF_BOX) is None
    resolved = ResolvedCollection(name="test_case_search", embedding_function=None, generation=LEGACY_GENERATION)
    assert resolved.create_kwargs() == {}
    # The bundled embedder needs no policy decision and builds no client.
    assert events == []


def test_from_settings_normalises_the_stored_pair():
    assert EmbedderSpec.from_settings(None, None) == DEFAULT_EMBEDDER
    assert EmbedderSpec.from_settings(" Default ", "anything") == EmbedderSpec("default", DEFAULT_EMBEDDING_MODEL)
    assert DEFAULT_EMBEDDER.key == "default"
    spec = EmbedderSpec.from_settings(" OLLAMA ", " nomic-embed-text:v1.5 ")
    assert spec == OLLAMA
    assert spec.key == "ollama:nomic-embed-text:v1.5"
    # An unsupported provider is kept as given (shown, never applied).
    assert EmbedderSpec.from_settings("Cohere", "embed-v3") == EmbedderSpec("cohere", "embed-v3")


# ── ollama: the policy first; pinned offline; the TLS policy online ──────────

def test_ollama_runs_the_provider_policy_before_it_is_built(events):
    get_chroma_embedding_function(OLLAMA, offline=True, ollama_base_url=ON_BOX)
    assert [event[0] for event in events] == ["policy", "construct"]
    assert events[0] == ("policy", "ollama", True, ON_BOX)
    assert events[1][2] == {"url": ON_BOX, "model_name": "nomic-embed-text:v1.5", "timeout": 30}


def test_an_off_box_ollama_while_offline_is_refused_before_anything_is_built(events):
    for _ in range(2):  # a refusal is never memoised as a function
        with pytest.raises(EmbedderRefused) as caught:
            get_chroma_embedding_function(OLLAMA, offline=True, ollama_base_url=OFF_BOX)
        assert str(caught.value) == OFF_BOX_REFUSAL
    assert [event[0] for event in events] == ["policy", "policy"]
    with pytest.raises(EmbedderRefused) as caught:
        validate_embedder(OLLAMA, offline=True, ollama_base_url=OFF_BOX, openai_key_set=False)
    assert str(caught.value) == OFF_BOX_REFUSAL


def test_offline_ollama_gets_the_local_only_pinned_client():
    ef = get_chroma_embedding_function(OLLAMA, offline=True, ollama_base_url=ON_BOX)
    inner = _inner(ef)
    assert isinstance(inner._transport, LocalOnlyHTTPTransport)
    # The rebuild keeps the origin and the timeout.
    assert str(inner.base_url) == ON_BOX
    assert inner.timeout == httpx.Timeout(30)


def test_online_ollama_is_rebuilt_with_verify_from_http_verify(monkeypatch):
    from app.core import http_client

    calls: list[None] = []

    def _verify():
        calls.append(None)
        return False  # distinguishable from httpx's own default (verify=True)

    monkeypatch.setattr(http_client, "http_verify", _verify)
    ef = get_chroma_embedding_function(OLLAMA, offline=False, ollama_base_url=ON_BOX)
    inner = _inner(ef)
    assert calls == [None]
    assert not isinstance(inner._transport, LocalOnlyHTTPTransport)
    assert inner._transport._pool._ssl_context.verify_mode == ssl.CERT_NONE
    assert str(inner.base_url) == ON_BOX
    assert inner.timeout == httpx.Timeout(30)


def test_online_ollama_may_use_a_public_host():
    validate_embedder(OLLAMA, offline=False, ollama_base_url=OFF_BOX, openai_key_set=False)
    ef = get_chroma_embedding_function(OLLAMA, offline=False, ollama_base_url=OFF_BOX)
    assert str(_inner(ef).base_url) == OFF_BOX


# ── openai: refused offline, refused without a key ───────────────────────────

def test_openai_is_refused_while_offline_even_with_a_key():
    with pytest.raises(EmbedderRefused) as caught:
        get_chroma_embedding_function(OPENAI, offline=True, ollama_base_url=None, openai_api_key="sk-test-123")
    assert str(caught.value) == OPENAI_OFFLINE_REFUSED
    with pytest.raises(EmbedderRefused) as caught:
        validate_embedder(OPENAI, offline=True, ollama_base_url=None, openai_key_set=True)
    assert str(caught.value) == OPENAI_OFFLINE_REFUSED


@pytest.mark.parametrize("key", [None, ""])
def test_openai_without_a_key_is_refused(key):
    with pytest.raises(EmbedderRefused) as caught:
        get_chroma_embedding_function(OPENAI, offline=False, ollama_base_url=None, openai_api_key=key)
    assert str(caught.value) == OPENAI_KEY_MISSING
    with pytest.raises(EmbedderRefused) as caught:
        validate_embedder(OPENAI, offline=False, ollama_base_url=None, openai_key_set=False)
    assert str(caught.value) == OPENAI_KEY_MISSING


def test_openai_online_with_a_key_builds_chromas_function():
    validate_embedder(OPENAI, offline=False, ollama_base_url=None, openai_key_set=True)
    ef = get_chroma_embedding_function(OPENAI, offline=False, ollama_base_url=None, openai_api_key="sk-test-123")
    assert isinstance(ef, OpenAIEmbeddingFunction)


# ── memoised per (provider, model, base_url, offline) ────────────────────────

def test_the_function_is_memoised_per_provider_model_base_url_and_offline(events):
    first = get_chroma_embedding_function(OLLAMA, offline=True, ollama_base_url=ON_BOX)
    assert get_chroma_embedding_function(OLLAMA, offline=True, ollama_base_url=ON_BOX) is first
    assert ef_mod.cached_embedding_function(OLLAMA, offline=True, ollama_base_url=ON_BOX) == (True, first)
    assert [event[0] for event in events] == ["policy", "construct"]   # built once
    other_mode = get_chroma_embedding_function(OLLAMA, offline=False, ollama_base_url=ON_BOX)
    other_url = get_chroma_embedding_function(OLLAMA, offline=True, ollama_base_url="http://127.0.0.2:11434")
    other_model = get_chroma_embedding_function(
        EmbedderSpec("ollama", "mxbai-embed-large"), offline=True, ollama_base_url=ON_BOX
    )
    assert len({id(first), id(other_mode), id(other_url), id(other_model)}) == 4
    openai = get_chroma_embedding_function(OPENAI, offline=False, ollama_base_url=None, openai_api_key="sk-a")
    assert get_chroma_embedding_function(OPENAI, offline=False, ollama_base_url=None, openai_api_key="sk-a") is openai
    # A rotated key is a different client (the key sits beside the entry, compared in constant time; never hashed, never a dict key).
    rotated = get_chroma_embedding_function(OPENAI, offline=False, ollama_base_url=None, openai_api_key="sk-b")
    assert rotated is not openai
    assert all("sk-a" not in repr(key) for key in ef_mod._memo)
    clear_embedding_function_cache()
    assert ef_mod.cached_embedding_function(OLLAMA, offline=True, ollama_base_url=ON_BOX) == (False, None)


# ── what every provider needs ────────────────────────────────────────────────

def test_an_unsupported_provider_is_refused():
    spec = EmbedderSpec.from_settings("cohere", "embed-v3")
    with pytest.raises(EmbedderRefused) as caught:
        get_chroma_embedding_function(spec, offline=False, ollama_base_url=None)
    assert str(caught.value) == PROVIDER_UNSUPPORTED
    with pytest.raises(EmbedderRefused) as caught:
        validate_embedder(spec, offline=False, ollama_base_url=None, openai_key_set=True)
    assert str(caught.value) == PROVIDER_UNSUPPORTED


@pytest.mark.parametrize("model", ["", "x" * 191])
def test_a_missing_or_overlong_model_is_refused(model):
    for provider in ("ollama", "openai"):
        with pytest.raises(EmbedderRefused) as caught:
            validate_embedder(
                EmbedderSpec(provider, model), offline=False, ollama_base_url=ON_BOX, openai_key_set=True
            )
        assert str(caught.value) == MODEL_REQUIRED
    if not model:
        with pytest.raises(EmbedderRefused) as caught:
            get_chroma_embedding_function(EmbedderSpec("ollama", ""), offline=True, ollama_base_url=ON_BOX)
        assert str(caught.value) == MODEL_REQUIRED


def test_validate_accepts_what_this_deployment_builds():
    validate_embedder(DEFAULT_EMBEDDER, offline=True, ollama_base_url=OFF_BOX, openai_key_set=False)
    validate_embedder(OLLAMA, offline=True, ollama_base_url=ON_BOX, openai_key_set=False)
    validate_embedder(OPENAI, offline=False, ollama_base_url=None, openai_key_set=True)


def test_the_ollama_origin_is_the_resolvers_only_while_the_llm_provider_is_ollama(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    assert ef_mod.ollama_base_url({"provider": "ollama", "base_url": "http://10.0.0.5:11434"}) == (
        "http://10.0.0.5:11434"
    )
    # An OpenRouter / LM Studio override must not become the embedder's URL.
    assert ef_mod.ollama_base_url({"provider": "openrouter", "base_url": "http://10.0.0.6:1234"}) == (
        "http://127.0.0.1:11434"
    )
    assert ef_mod.ollama_base_url(None) == "http://127.0.0.1:11434"
