"""Which embedding function a ChromaDB collection uses (W12 SRCH-02).

Three embedders, keyed by ``EmbedderSpec.key``:

* ``default`` -- ChromaDB's bundled all-MiniLM-L6-v2 ONNX model. The factory
  answers ``None`` and every call site OMITS ``embedding_function=`` exactly as
  before W12, so the model still loads through ChromaDB's own
  ``DefaultEmbeddingFunction`` and its download chokepoint stays behind
  ``services/local_embedder_guard.py`` (the offline weight ceiling).
* ``ollama:<model>`` -- ChromaDB's ``OllamaEmbeddingFunction``. The provider
  policy (``llm_policy_service.enforce_provider_policy``) runs BEFORE the
  function is built, and under ``AI_OFFLINE_MODE`` its one sync ``httpx``
  client is rebuilt by ``llm_egress.pin_ollama_sync_client`` so every TCP
  connect is pinned to an on-box address; online it is rebuilt with the
  deployment's TLS policy (``verify=http_verify()``).
* ``openai:<model>`` -- ChromaDB's ``OpenAIEmbeddingFunction``; refused while
  offline mode is on (``OPENAI_OFFLINE_REFUSED``), refused without a key.

Nothing here opens a network connection: constructing ChromaDB's functions
builds clients, it does not call them. A ChromaDB 1.5.9 collection also
persists its function's name and refuses a different non-default one on get, a
second guard against mixing two embedders in one collection.

Commit-free; imports nothing that touches a database.
"""
from __future__ import annotations

import hmac
import threading
from dataclasses import dataclass
from typing import Any, Mapping

EMBEDDING_PROVIDERS = ("default", "ollama", "openai")
EMBEDDING_PROVIDER_LABELS = {"default": "Bundled MiniLM", "ollama": "Ollama", "openai": "OpenAI"}
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBED_TIMEOUT_SECONDS = 30
OPENAI_OFFLINE_REFUSED = (
    "OpenAI embeddings are refused while offline mode is on: choose Bundled MiniLM or Ollama."
)
PROVIDER_UNSUPPORTED = "Embedding provider must be one of: default, ollama, openai."
MODEL_REQUIRED = "Name the embedding model (for example nomic-embed-text:v1.5)."
OPENAI_KEY_MISSING = (
    "Set the OpenAI API key under Cloud API Keys before choosing OpenAI embeddings."
)
CONFIGURED_UNSUPPORTED_NOTE = "The stored provider '{provider}' is not supported; {active} stays in use."

_MODEL_MAX_CHARS = 190  # the job row's embedder_key is String(200): "openai:" + model


class EmbedderRefused(ValueError):
    """An embedder that this deployment will not build; ``str(exc)`` is the sentence."""


@dataclass(frozen=True, slots=True)
class EmbedderSpec:
    provider: str
    model: str

    @property
    def key(self) -> str:
        """``default`` | ``<provider>:<model>`` -- the identity of the vectors."""
        if self.provider == "default":
            return "default"
        return f"{self.provider}:{self.model}"

    @property
    def label(self) -> str:
        return EMBEDDING_PROVIDER_LABELS.get(self.provider, self.provider)

    @classmethod
    def from_settings(cls, provider: str | None, model: str | None) -> "EmbedderSpec":
        """Normalise a stored / posted pair: provider lower-cased (empty = default),
        model stripped; the bundled embedder always carries its own model name.
        An unsupported provider is kept as given (shown, never applied)."""
        normalized = str(provider or "").strip().lower() or "default"
        if normalized == "default":
            return cls("default", DEFAULT_EMBEDDING_MODEL)
        return cls(normalized, str(model or "").strip())

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


DEFAULT_EMBEDDER = EmbedderSpec("default", DEFAULT_EMBEDDING_MODEL)


def ollama_base_url(effective: Mapping[str, Any] | None) -> str:
    """The Ollama origin an embedder talks to.

    The resolver's ``base_url`` is the LLM provider's endpoint override, so it
    names an Ollama server only while the LLM provider IS Ollama; otherwise
    (an OpenRouter or LM Studio URL, say) it would point the embedder at the
    wrong service, and ``OLLAMA_BASE_URL`` is the Ollama this deployment runs.
    """
    from app.core.config import settings

    cfg = effective or {}
    provider = str(cfg.get("provider") or cfg.get("llm_provider") or "").strip().lower()
    base = str(cfg.get("base_url") or "").strip()
    if provider == "ollama" and base:
        return base
    return str(settings.OLLAMA_BASE_URL or "")


def _policy_refusal(provider: str, *, offline: bool, base_url: str | None) -> None:
    from app.services.llm_policy_service import LLMPolicyViolation, enforce_provider_policy

    try:
        enforce_provider_policy(provider, offline=offline, base_url=base_url)
    except LLMPolicyViolation as exc:
        raise EmbedderRefused(str(exc)) from exc


def validate_embedder(
    spec: EmbedderSpec,
    *,
    offline: bool,
    ollama_base_url: str | None,
    openai_key_set: bool,
) -> None:
    """Raise :class:`EmbedderRefused` (one sentence) when ``spec`` cannot be built here.

    The policy check resolves a host name for the offline residency rule (a DNS
    lookup, no connection); callers in a request run this in a thread.
    """
    if spec.provider not in EMBEDDING_PROVIDERS:
        raise EmbedderRefused(PROVIDER_UNSUPPORTED)
    if spec.provider == "default":
        return
    if not spec.model or len(spec.model) > _MODEL_MAX_CHARS:
        raise EmbedderRefused(MODEL_REQUIRED)
    if spec.provider == "ollama":
        from app.core.config import settings

        base = ollama_base_url or str(settings.OLLAMA_BASE_URL or "") or None
        _policy_refusal("ollama", offline=offline, base_url=base)
        return
    # openai
    if offline:
        raise EmbedderRefused(OPENAI_OFFLINE_REFUSED)
    if not openai_key_set:
        raise EmbedderRefused(OPENAI_KEY_MISSING)
    _policy_refusal("openai", offline=offline, base_url=None)


# One entry per (provider, model, base_url, offline): the OpenAI key the function
# was built with sits BESIDE it and is compared in constant time on a hit, so the
# key is never hashed and never part of a dict key (a rotated key rebuilds the
# client, replacing the entry). The built OpenAI client holds the key anyway.
_memo: dict[tuple[str, str, str, bool], tuple[str, Any]] = {}
_memo_lock = threading.Lock()


def _memo_key(spec: EmbedderSpec, *, offline: bool, base_url: str | None) -> tuple[str, str, str, bool]:
    return (spec.provider, spec.model, str(base_url or ""), bool(offline))


def _memo_hit(key: tuple[str, str, str, bool], openai_api_key: str | None) -> tuple[bool, Any | None]:
    entry = _memo.get(key)
    if entry is not None and hmac.compare_digest(entry[0], openai_api_key or ""):
        return True, entry[1]
    return False, None


def cached_embedding_function(
    spec: EmbedderSpec, *, offline: bool, ollama_base_url: str | None,
    openai_api_key: str | None = None,
) -> tuple[bool, Any | None]:
    """``(True, ef)`` when this process already built the function, else ``(False, None)``."""
    if spec.provider == "default":
        return True, None
    key = _memo_key(spec, offline=offline, base_url=ollama_base_url)
    with _memo_lock:
        return _memo_hit(key, openai_api_key)


def clear_embedding_function_cache() -> None:
    with _memo_lock:
        _memo.clear()


def _rebuild_with_tls_policy(holder: Any) -> None:
    """Online: give Ollama's sync client the deployment's TLS policy.

    ``ollama.Client`` builds a bare ``httpx.Client``, which ignores
    ``HTTP_CA_BUNDLE`` / ``HTTP_VERIFY_TLS`` -- an Ollama behind an internal CA
    would fail TLS while the rest of the app worked.
    """
    import httpx

    from app.core.http_client import http_verify

    inner = getattr(holder, "_client", None)
    if not isinstance(inner, httpx.Client):
        raise RuntimeError("ollama client internals changed; TLS policy could not be applied")
    holder._client = httpx.Client(
        verify=http_verify(),
        base_url=inner.base_url,
        timeout=inner.timeout,
        headers=inner.headers,
        follow_redirects=inner.follow_redirects,
    )


def get_chroma_embedding_function(
    spec: EmbedderSpec,
    *,
    offline: bool,
    ollama_base_url: str | None,
    openai_api_key: str | None = None,
) -> Any | None:
    """The ChromaDB embedding function for ``spec``, or ``None`` for the bundled one.

    ``None`` means: pass NO ``embedding_function`` (ChromaDB's default, still
    behind the offline weight guard). Memoised per process per
    ``(provider, model, base_url, offline)``; an OpenAI function is reused only
    for the key it was built with (compared in constant time, never hashed).
    Raises :class:`EmbedderRefused` for anything this deployment refuses.
    """
    if spec.provider == "default":
        return None
    if spec.provider not in EMBEDDING_PROVIDERS:
        raise EmbedderRefused(PROVIDER_UNSUPPORTED)
    found, cached = cached_embedding_function(
        spec, offline=offline, ollama_base_url=ollama_base_url, openai_api_key=openai_api_key
    )
    if found:
        return cached
    if not spec.model:
        raise EmbedderRefused(MODEL_REQUIRED)

    ef: Any
    if spec.provider == "ollama":
        from app.core.config import settings

        base = ollama_base_url or str(settings.OLLAMA_BASE_URL or "")
        # The policy BEFORE construction: an off-box host under offline mode is
        # refused here with the policy's own sentence.
        _policy_refusal("ollama", offline=offline, base_url=base or None)
        from chromadb.utils.embedding_functions.ollama_embedding_function import (
            OllamaEmbeddingFunction,
        )

        ef = OllamaEmbeddingFunction(
            url=base,
            model_name=spec.model,
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        if offline:
            from app.services.llm_egress import pin_ollama_sync_client

            pin_ollama_sync_client(ef._client)
        else:
            _rebuild_with_tls_policy(ef._client)
    else:  # openai
        if offline:
            raise EmbedderRefused(OPENAI_OFFLINE_REFUSED)
        if not openai_api_key:
            raise EmbedderRefused(OPENAI_KEY_MISSING)
        _policy_refusal("openai", offline=offline, base_url=None)
        import warnings

        from chromadb.utils.embedding_functions.openai_embedding_function import (
            OpenAIEmbeddingFunction,
        )

        with warnings.catch_warnings():
            # "Direct api_key configuration will not be persisted": intended --
            # the key lives in secret_refs, never in ChromaDB's collection config.
            warnings.simplefilter("ignore", DeprecationWarning)
            ef = OpenAIEmbeddingFunction(api_key=openai_api_key, model_name=spec.model)

    key = _memo_key(spec, offline=offline, base_url=ollama_base_url)
    with _memo_lock:
        _memo[key] = (openai_api_key or "", ef)
    return ef
