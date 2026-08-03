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

logger = structlog.get_logger("services.model_status")

# Deliberately short: this probe runs behind an interactive settings page
# and behind /health/details, so an unreachable daemon must fail fast
# rather than hold the request open.
PROBE_TIMEOUT_SECONDS = 3.0

_CLOUD_PROVIDERS = ("openai", "gemini", "anthropic")
# Local, OpenAI-compatible servers. They speak a different discovery API
# than Ollama, so TestLookup does not claim to verify model presence there.
_SELF_HOSTED_PROVIDERS = ("lmstudio", "localai", "vllm")


async def probe_ollama(
    base_url: str | None = None,
    timeout: Any = None,
) -> dict[str, Any]:
    """Ask Ollama which models are installed. Never raises.

    Returns ``{"reachable": bool, "models": list[str], "error": str | None}``.
    ``reachable`` is strictly about the daemon answering ``GET /api/tags`` —
    an empty ``models`` list on a reachable daemon means "Ollama is up and
    has nothing installed", which is a completely different operator
    problem from "Ollama is down". Callers must not collapse the two.
    """
    url = (base_url or settings.OLLAMA_BASE_URL or "").rstrip("/")
    try:
        from app.core.http_client import get_http_client  # noqa: PLC0415

        client = get_http_client()
        resp = await client.get(
            f"{url}/api/tags",
            timeout=timeout if timeout is not None else PROBE_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            return {"reachable": False, "models": [], "error": f"HTTP {resp.status_code}"}
        payload = resp.json() or {}
        models = [
            str(m.get("name"))
            for m in (payload.get("models") or [])
            if isinstance(m, dict) and m.get("name")
        ]
        return {"reachable": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001 — a probe must never propagate
        return {"reachable": False, "models": [], "error": str(exc)[:200]}


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

    Note the three distinct unavailable states — unreachable daemon,
    reachable daemon without the model, and a cloud provider blocked by
    offline mode. Collapsing them into one "LLM unavailable" is what sends
    operators down the wrong debugging path.
    """
    if provider == "ollama":
        if not probe.get("reachable"):
            return {
                "mode": "llm",
                "available": False,
                "reason": (
                    f"Ollama is unreachable at {base_url}"
                    + (f" ({probe.get('error')})" if probe.get("error") else "")
                    + " — this is a connectivity problem, not a missing model."
                ),
            }
        if not model_present(llm_model, probe.get("models") or []):
            return {"mode": "llm", "available": False, "reason": _missing_model_reason(llm_model)}
        return {"mode": "llm", "available": True, "reason": None}

    if provider in _CLOUD_PROVIDERS:
        # The settings loader only surfaces the OpenAI/Google keys, so fall
        # back to env for the rest — otherwise a configured Anthropic key
        # reads as "no key configured", which is a false alarm.
        cfg_key, env_attr = {
            "openai": ("openai_api_key", "OPENAI_API_KEY"),
            "gemini": ("google_api_key", "GOOGLE_API_KEY"),
            "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        }[provider]
        key_set = bool(cfg.get(cfg_key) or getattr(settings, env_attr, None))
        if offline_mode:
            return {
                "mode": "llm",
                "available": False,
                "reason": (
                    f"AI offline mode is on, so the cloud provider '{provider}' is blocked. "
                    "Switch the provider to Ollama and install a local model."
                ),
            }
        if not key_set:
            return {
                "mode": "llm",
                "available": False,
                "reason": f"No API key configured for provider '{provider}'.",
            }
        return {"mode": "llm", "available": True, "reason": None}

    if provider in _SELF_HOSTED_PROVIDERS:
        return {
            "mode": "llm",
            "available": True,
            "reason": (
                f"Provider '{provider}' is a self-hosted OpenAI-compatible endpoint — "
                "TestLookup cannot verify model presence for it, so this is unverified."
            ),
        }

    return {
        "mode": "llm",
        "available": False,
        "reason": f"Unknown LLM provider '{provider or 'none'}'.",
    }


async def build_model_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the ``GET /api/v1/settings/ai/model-status`` payload.

    ``cfg`` is the effective AI config (DB overrides merged over env, as
    returned by the settings router's ``_load_ai_config``). Every field
    falls back to ``settings`` so a failed DB read degrades to env truth
    instead of a 500. Never raises.
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

    uses_ollama = provider == "ollama" or embedding_provider == "ollama"
    if uses_ollama:
        probe = await probe_ollama(base_url)
    else:
        probe = {
            "reachable": False,
            "models": [],
            "error": (
                f"Ollama is not on the configured path (LLM provider '{provider or 'none'}', "
                f"embedding provider '{embedding_provider or 'none'}') — not probed."
            ),
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
    if embedding_provider == "ollama" and embedding_model:
        required.append(_required(embedding_model, "embedding"))
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
        },
        _llm_chain_entry(
            provider=provider,
            llm_model=llm_model,
            base_url=base_url,
            offline_mode=offline_mode,
            probe=probe,
            cfg=cfg,
        ),
        {"mode": "rules", "available": True, "reason": None},
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
