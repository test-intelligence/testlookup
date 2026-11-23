"""Live model-presence and fallback-chain status (PMF US-13.2 — offline model pack).

Air-gapped installs have no way to ``ollama pull`` at runtime: the models
arrive as a side-loaded *model pack* (see ``user-guide/offline-model-pack.md``).
The failure mode that costs operators a day is silent — the models never
made it into the Ollama volume, AI triage quietly degrades to the rules
engine, and nothing in the UI says so. This module is the honest answer to
"is the model actually there, and which analysis tier will really run?".

Three responsibilities:

1. ``probe_ollama`` — the single outbound ``GET /api/tags`` probe. It is
   shared with ``routers/health.py`` (``_check_ollama`` delegates here) so
   there is exactly one place that knows how to ask Ollama what it has.
   Never raises: an unreachable daemon comes back as
   ``{"reachable": False, "error": "..."}``.
2. ``ollama_pull_command`` — the runtime-aware ``ollama pull`` recipe
   (compose vs kubectl). ``services/agent.py::_model_missing_hint`` calls
   it, so the remedy string an operator sees in the UI matches the one the
   agent writes into a degraded analysis.
3. ``build_model_status`` — assembles the ``/settings/ai/model-status``
   contract: which required models are installed, and which analysis tiers
   are *really* available right now.

**Matching semantics.** ``model_present`` matches on the exact Ollama name,
normalising a missing tag to ``:latest`` (``llama3`` == ``llama3:latest``).
``analysis_router._probe_ollama_model_async`` uses a looser family-prefix
rule for auto-mode, so a box with ``qwen2.5:14b`` installed and
``LLM_MODEL=qwen2.5:7b`` configured will see auto-mode *attempt* the LLM
while this endpoint reports the configured model as missing. That is the
intended asymmetry: the router's looseness only decides whether to try
(a wrong-tag attempt just errors and falls back to rules), whereas this
endpoint answers "did my model pack import correctly?", where "close
enough" is a wrong answer.

**AI_OFFLINE_MODE.** ``health.py`` only probes Ollama when
``AI_OFFLINE_MODE`` is true (offline mode == "local models only", so a
cloud deployment has no Ollama to report on). That guard is coherent but
keyed on the wrong setting — a deployment can run ``LLM_PROVIDER=ollama``
with ``AI_OFFLINE_MODE=False``, and ``/health/details`` then reports Ollama
as ``skipped`` while it is very much in use. The health output is left
alone (ops dashboards consume its shape); this module keys off the
effective **provider** instead, which is the setting that actually decides
whether Ollama is on the path.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import structlog

from app.core.config import settings
from app.services.llm_policy_service import LOCAL_PROVIDERS, REMOTE_PROVIDERS

logger = structlog.get_logger("services.model_status")

# Deliberately short: this probe runs behind an interactive settings page
# and behind /health/details, so an unreachable daemon must fail fast
# rather than hold the request open.
PROBE_TIMEOUT_SECONDS = 3.0

# Derived from llm_policy_service rather than duplicated here. A second,
# hand-maintained provider list is what made a fully working OpenRouter
# deployment report "Unknown LLM provider 'openrouter'" on the AI settings
# page (reference K3s install, 2026-08-16): #616 taught llm_factory and llm_policy_service
# the new provider, this module was never updated, and the readiness page then
# declared the LLM tier unavailable while it was demonstrably serving traffic.
_CLOUD_PROVIDERS = tuple(sorted(REMOTE_PROVIDERS))
# Local, OpenAI-compatible servers. They speak a different discovery API
# than Ollama, so TestLookup does not claim to verify model presence there.
_SELF_HOSTED_PROVIDERS = tuple(sorted(LOCAL_PROVIDERS - {"ollama"}))

# Where each cloud provider's API key comes from: (settings-loader key, env attr).
# The settings loader only surfaces some of them, so the env attribute is the
# fallback — otherwise a configured key reads as "no key configured".
_CLOUD_KEY_SOURCES: dict[str, tuple[str, str]] = {
    "openai": ("openai_api_key", "OPENAI_API_KEY"),
    "gemini": ("google_api_key", "GOOGLE_API_KEY"),
    "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
    "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
}


# Why a probe failed, as one fixed word. The base URL is something an ADMIN
# saves, so the text of the exception, or of whatever answered at that URL,
# would let the probe be used to read things about hosts on the inside.
PROBE_REFUSED = "refused_by_policy"        # the provider policy forbids this base URL
PROBE_UNREACHABLE = "unreachable"          # no answer (connection, timeout, TLS)
PROBE_UNEXPECTED_STATUS = "unexpected_status"  # answered, but not 200
PROBE_INVALID_RESPONSE = "invalid_response"    # 200, but not Ollama's /api/tags shape
PROBE_NOT_USED = "not_used"                # neither the model nor the embeddings run on Ollama: not probed

#: D-23: what a person reads for each word (the page and the fallback chain).
PROBE_SENTENCES = {
    PROBE_REFUSED: "This address is not allowed by the provider policy while offline mode is on.",
    PROBE_UNREACHABLE: "No answer from the Ollama server (connection, time limit or TLS).",
    PROBE_UNEXPECTED_STATUS: "The server answered, but not as Ollama does.",
    PROBE_INVALID_RESPONSE: "The server answered with something other than an Ollama model list.",
    PROBE_NOT_USED: "Ollama is not used: neither the model nor the embeddings run on it.",
}

# Bounds on what a probe hands back from the answer it got.
_PROBE_MAX_MODELS = 500
_PROBE_MAX_NAME_CHARS = 200


def _probe_failure(word: str) -> dict[str, Any]:
    return {"reachable": False, "models": [], "error": word}


async def _effective_offline() -> bool:
    """Offline as a model call sees it: the environment, or Settings > AI."""
    if settings.AI_OFFLINE_MODE:
        return True
    try:
        from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415

        return bool((await get_effective_ai_config()).get("offline_mode"))
    except Exception:  # noqa: BLE001 — the resolver degrades to env itself
        return bool(settings.AI_OFFLINE_MODE)


async def probe_ollama(
    base_url: str | None = None,
    timeout: Any = None,
    *,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Ask Ollama which models are installed. Never raises.

    Returns ``{"reachable": bool, "models": list[str], "error": str | None}``.
    ``reachable`` is strictly about the daemon answering ``GET /api/tags`` —
    an empty ``models`` list on a reachable daemon means "Ollama is up and
    has nothing installed", which is a completely different operator
    problem from "Ollama is down". Callers must not collapse the two.

    The probe obeys the same rules as a model call. The provider policy is
    applied to the base URL first; with offline mode on, a URL that is not
    on-box gets no request, and an on-box one is reached through the client
    that pins the connection to on-box addresses. ``offline`` is the
    effective switch when the caller already has it; left out, it is read
    here. ``error`` is one of the ``PROBE_*`` words, never the error text.
    """
    url = (base_url or settings.OLLAMA_BASE_URL or "").rstrip("/")
    if offline is None:
        offline = await _effective_offline()
    try:
        from app.services.llm_policy_service import enforce_provider_policy_async  # noqa: PLC0415

        await enforce_provider_policy_async("ollama", offline=bool(offline), base_url=url)
    except Exception as exc:  # noqa: BLE001 — LLMPolicyViolation, or the check itself failing
        logger.info("ollama_probe_refused", error=type(exc).__name__)
        return _probe_failure(PROBE_REFUSED)
    try:
        if offline:
            from app.services.llm_egress import get_local_only_http_client  # noqa: PLC0415

            client = get_local_only_http_client()
        else:
            from app.core.http_client import get_http_client  # noqa: PLC0415

            client = get_http_client()
        resp = await client.get(
            f"{url}/api/tags",
            timeout=timeout if timeout is not None else PROBE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — a probe must never propagate
        logger.debug("ollama_probe_failed", error=type(exc).__name__)
        return _probe_failure(PROBE_UNREACHABLE)
    if resp.status_code != 200:
        return _probe_failure(PROBE_UNEXPECTED_STATUS)
    try:
        payload = resp.json() or {}
        listed = payload.get("models") or []
        models = [
            str(m.get("name"))[:_PROBE_MAX_NAME_CHARS]
            for m in listed[:_PROBE_MAX_MODELS]
            if isinstance(m, dict) and m.get("name")
        ]
    except Exception:  # noqa: BLE001 — not Ollama's answer
        return _probe_failure(PROBE_INVALID_RESPONSE)
    return {"reachable": True, "models": models, "error": None}


def _normalise_tag(name: str) -> str:
    """``llama3`` → ``llama3:latest``; Ollama's implicit default tag."""
    return name if ":" in name else f"{name}:latest"


def model_present(required: str, installed: list[str]) -> bool:
    """True when ``required`` is installed, tolerating the implicit ``:latest``."""
    if not required:
        return False
    wanted = _normalise_tag(required.strip())
    return any(_normalise_tag(str(name).strip()) == wanted for name in installed)


def ollama_pull_command(model: str) -> str:
    """Runtime-aware ``ollama pull`` recipe.

    A hardcoded ``docker compose exec`` line is wrong on K3s/OpenShift, so
    detect the runtime via the standard service-account file. Single source
    of truth for the remedy text shown in the AI settings page and written
    into degraded analyses by ``agent.py``.
    """
    on_k8s = os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if on_k8s:
        namespace = os.environ.get("KUBERNETES_NAMESPACE", "testlookup")
        return f"kubectl -n {namespace} exec deploy/testlookup-ollama -- ollama pull {model}"
    return f"docker compose exec ollama ollama pull {model}"


def _missing_model_reason(model: str) -> str:
    return (
        f"Model '{model}' is not installed on Ollama. "
        f"Pull it: {ollama_pull_command(model)} — or, on an air-gapped host "
        "with no registry egress, side-load it (see the offline model pack guide)."
    )


def _ml_available() -> bool:
    """Whether a trained ML classifier exists on disk. Never raises."""
    try:
        from app.services.ml.classifier import MLClassifier  # noqa: PLC0415

        return bool(MLClassifier.is_available())
    except Exception as exc:  # noqa: BLE001
        logger.debug("ml_availability_probe_failed", error=str(exc))
        return False


def _llm_chain_entry(
    *,
    provider: str,
    llm_model: str,
    base_url: str,
    offline_mode: bool,
    probe: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Honest availability for the LLM tier.

    Note the distinct unavailable states — unreachable daemon, reachable daemon
    without the model, a cloud provider blocked by offline mode, and a cloud
    provider with no key. Collapsing them into one "LLM unavailable" is what
    sends operators down the wrong debugging path.

    ``reason_code`` carries that distinction in machine-readable form. The prose
    ``reason`` is for a human to read; the code is what UI badges branch on. The
    settings page previously re-derived the cause from ``ollama_reachable`` and
    so labelled *any* non-Ollama failure "Model Missing" — telling an operator
    with an unset OpenRouter key to go install a model (reference K3s install, 2026-08-16).
    A consumer that re-derives a cause the producer already determined will get
    it wrong the moment a new cause appears.
    """
    if provider == "ollama":
        if not probe.get("reachable"):
            return {
                "mode": "llm",
                "available": False,
                # D-23: a sentence, never the raw code or the internal address.
                "reason": (
                    "Ollama is unreachable"
                    + (f": {PROBE_SENTENCES[probe['error']]}" if probe.get("error") in PROBE_SENTENCES else ".")
                    + " This is a connectivity problem, not a missing model."
                ),
                "reason_code": "unreachable",
            }
        if not model_present(llm_model, probe.get("models") or []):
            return {
                "mode": "llm",
                "available": False,
                "reason": _missing_model_reason(llm_model),
                "reason_code": "model_missing",
            }
        return {"mode": "llm", "available": True, "reason": None, "reason_code": "ok"}

    if provider in _CLOUD_PROVIDERS:
        key_source = _CLOUD_KEY_SOURCES.get(provider)
        if key_source is None:
            # A provider the policy service permits but whose key location this
            # module doesn't know. Say exactly that instead of raising KeyError
            # (which 500s the settings page) or implying the provider is bogus.
            return {
                "mode": "llm",
                "available": False,
                "reason": (
                    f"Provider '{provider}' is permitted but this build does not know "
                    "where to read its API key, so its readiness cannot be verified."
                ),
                "reason_code": "unverifiable",
            }
        cfg_key, env_attr = key_source
        key_set = bool(cfg.get(cfg_key) or getattr(settings, env_attr, None))
        if offline_mode:
            return {
                "mode": "llm",
                "available": False,
                "reason": (
                    f"AI offline mode is on, so the cloud provider '{provider}' is blocked. "
                    "Switch the provider to Ollama and install a local model."
                ),
                "reason_code": "offline_blocked",
            }
        if not key_set:
            return {
                "mode": "llm",
                "available": False,
                "reason": f"No API key configured for provider '{provider}'.",
                "reason_code": "no_api_key",
            }
        return {"mode": "llm", "available": True, "reason": None, "reason_code": "ok"}

    if provider in _SELF_HOSTED_PROVIDERS:
        return {
            "mode": "llm",
            "available": True,
            "reason": (
                f"Provider '{provider}' is a self-hosted OpenAI-compatible endpoint — "
                "TestLookup cannot verify model presence for it, so this is unverified."
            ),
            "reason_code": "unverifiable",
        }

    return {
        "mode": "llm",
        "available": False,
        "reason": f"Unknown LLM provider '{provider or 'none'}'.",
        "reason_code": "unknown_provider",
    }


def _ollama_embedders(embedding: dict[str, Any]) -> list[tuple[str, bool]]:
    """``[(model, in_use)]`` for the ACTIVE and TARGET embedders that run on Ollama."""
    out: list[tuple[str, bool]] = []
    for role in ("active", "target"):
        entry = embedding.get(role)
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or "").strip().lower()
        model = str(entry.get("model") or "").strip()
        if provider == "ollama" and model and model not in [m for m, _ in out]:
            out.append((model, role == "active"))
    return out


async def build_model_status(
    cfg: dict[str, Any] | None = None,
    *,
    embedding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the ``GET /api/v1/settings/ai/model-status`` payload.

    ``cfg`` is the effective AI config (DB overrides merged over env, as
    returned by the settings router's ``_load_ai_config``). Every field
    falls back to ``settings`` so a failed DB read degrades to env truth
    instead of a 500. Never raises.

    ``embedding`` (W12 SRCH-02) is ``{"active": {"provider", "model"},
    "target": {...} | None}`` from the embedding index state. Given, an
    Ollama embedding model is REQUIRED only when it is the active or the
    target embedder (``in_use`` true for the active one) -- a configured but
    unapplied model runs nothing. Left out (every direct caller before W12),
    the configured ``embedding_provider`` decides, as it always did.
    """
    cfg = dict(cfg or {})

    provider = str(cfg.get("llm_provider") or settings.LLM_PROVIDER or "").lower()
    llm_model = str(cfg.get("llm_model") or settings.LLM_MODEL or "")
    embedding_provider = str(cfg.get("embedding_provider") or settings.EMBEDDING_PROVIDER or "").lower()
    embedding_model = str(cfg.get("embedding_model") or settings.EMBEDDING_MODEL or "")
    classifier_model = settings.CLASSIFIER_MODEL or ""
    offline_mode = bool(cfg.get("ai_offline_mode", settings.AI_OFFLINE_MODE))
    analysis_mode = str(cfg.get("analysis_mode") or settings.ANALYSIS_MODE or "auto").lower()
    base_url = str(cfg.get("base_url") or settings.OLLAMA_BASE_URL or "")

    ollama_embedders = _ollama_embedders(embedding) if embedding is not None else []
    if embedding is None:
        uses_ollama = provider == "ollama" or embedding_provider == "ollama"
    else:
        uses_ollama = provider == "ollama" or bool(ollama_embedders)
    if uses_ollama:
        probe = await probe_ollama(base_url, offline=offline_mode)
    else:
        probe = {
            "reachable": False,
            "models": [],
            # D-23: a word the page shows as "not used", not as a red "unreachable" box.
            "error": PROBE_NOT_USED,
        }

    installed: list[str] = list(probe.get("models") or [])
    reachable = bool(probe.get("reachable"))

    def _required(name: str, purpose: str) -> dict[str, Any]:
        present = model_present(name, installed)
        # A remedy only makes sense once we know the daemon answers. While
        # Ollama is unreachable every model reads as "missing", and telling
        # the operator to pull would send them down the wrong path.
        remedy = None if (present or not reachable) else _missing_model_reason(name)
        return {"name": name, "purpose": purpose, "present": present, "remedy": remedy}

    required: list[dict[str, Any]] = []
    if provider == "ollama" and llm_model:
        required.append(_required(llm_model, "llm"))
    if embedding is None:
        if embedding_provider == "ollama" and embedding_model:
            required.append(_required(embedding_model, "embedding"))
    else:
        for model, in_use in ollama_embedders:
            required.append({**_required(model, "embedding"), "in_use": in_use})
    if provider == "ollama" and classifier_model and classifier_model != llm_model:
        required.append(_required(classifier_model, "classifier"))

    ml_ok = _ml_available()
    # Order mirrors analysis_router.get_analysis_mode()'s auto resolution:
    # ML if trained → LLM if reachable → rules (terminal, zero dependencies).
    fallback_chain: list[dict[str, Any]] = [
        {
            "mode": "ml",
            "available": ml_ok,
            "reason": None if ml_ok else (
                "No trained ML classifier on disk — auto mode falls through to the next tier."
            ),
            "reason_code": "ok" if ml_ok else "not_trained",
        },
        _llm_chain_entry(
            provider=provider,
            llm_model=llm_model,
            base_url=base_url,
            offline_mode=offline_mode,
            probe=probe,
            cfg=cfg,
        ),
        {"mode": "rules", "available": True, "reason": None, "reason_code": "ok"},
    ]

    return {
        "ollama_reachable": bool(probe.get("reachable")),
        "ollama_error": probe.get("error"),
        "ollama_base_url": base_url,
        "installed_models": installed,
        "required": required,
        "fallback_chain": fallback_chain,
        "offline_mode": offline_mode,
        "llm_provider": provider,
        "analysis_mode": analysis_mode,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
