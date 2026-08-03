"""Regression tests for the offline model pack status surface (PMF US-13.2).

The AC is "the AI settings page verifies model presence and shows the
fallback chain state", so what this file pins is *honesty under failure*:

  * reachable Ollama with every required model → all present, LLM tier live;
  * unreachable Ollama → ``ollama_reachable=False`` with the connectivity
    reason, and the chain is STILL returned with rules available (an
    operator debugging an air-gapped box needs the page to render exactly
    when things are broken);
  * reachable Ollama missing the configured model → a different, remedial
    reason naming the ``ollama pull`` / side-load fix — never confused with
    the unreachable case;
  * the probe never raises, whatever httpx does;
  * tag matching is exact (``:latest``-normalised), so a same-family
    different-size tag is not reported as the configured model;
  * ``routers/health.py`` delegates to the shared probe (no forked HTTP
    call) and ``agent.py`` shares the pull recipe (no forked remedy text);
  * the endpoint is QA_LEAD-gated, mirroring GET /settings/ai.

Hermetic: the outbound client and the ML classifier are faked.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")

from app.services import model_status_service as svc  # noqa: E402

REPO_BACKEND = Path(__file__).resolve().parents[1]

_BASE_CFG = {
    "llm_provider": "ollama",
    "llm_model": "qwen2.5:7b",
    "embedding_provider": "ollama",
    "embedding_model": "nomic-embed-text",
    "ai_offline_mode": True,
    "analysis_mode": "auto",
}


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    """Stand-in for the shared httpx client."""

    def __init__(self, resp=None, exc: Exception | None = None):
        self._resp = resp
        self._exc = exc
        self.calls: list[str] = []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        if self._exc is not None:
            raise self._exc
        return self._resp


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _Client) -> _Client:
    import app.core.http_client as http_client

    monkeypatch.setattr(http_client, "get_http_client", lambda: client)
    return client


def _tags(*names: str) -> _Resp:
    return _Resp(200, {"models": [{"name": n} for n in names]})


def _no_ml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_ml_available", lambda: False)


def _entry(status: dict, mode: str) -> dict:
    return next(e for e in status["fallback_chain"] if e["mode"] == mode)


# ── Probe ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_reports_installed_models(monkeypatch):
    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b", "nomic-embed-text:latest")))

    probe = await svc.probe_ollama("http://ollama:11434")

    assert probe == {
        "reachable": True,
        "models": ["qwen2.5:7b", "nomic-embed-text:latest"],
        "error": None,
    }


@pytest.mark.asyncio
async def test_probe_never_raises_on_transport_error(monkeypatch):
    _install_client(monkeypatch, _Client(exc=RuntimeError("connect refused")))

    probe = await svc.probe_ollama("http://ollama:11434")

    assert probe["reachable"] is False
    assert probe["models"] == []
    assert "connect refused" in probe["error"]


@pytest.mark.asyncio
async def test_probe_non_200_is_unreachable_with_status(monkeypatch):
    _install_client(monkeypatch, _Client(_Resp(503)))

    probe = await svc.probe_ollama("http://ollama:11434")

    assert probe["reachable"] is False
    assert probe["error"] == "HTTP 503"


@pytest.mark.asyncio
async def test_probe_strips_trailing_slash_from_base_url(monkeypatch):
    client = _install_client(monkeypatch, _Client(_tags()))

    await svc.probe_ollama("http://ollama:11434/")

    assert client.calls == ["http://ollama:11434/api/tags"]


# ── Tag matching ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("required", "installed", "expected"),
    [
        ("qwen2.5:7b", ["qwen2.5:7b"], True),
        ("llama3", ["llama3:latest"], True),            # implicit :latest
        ("llama3:latest", ["llama3"], True),
        ("qwen2.5:7b", ["qwen2.5:14b"], False),         # same family, wrong size
        ("qwen2.5:7b", [], False),
        ("", ["qwen2.5:7b"], False),
    ],
)
def test_model_present_matches_exact_tag(required, installed, expected):
    assert svc.model_present(required, installed) is expected


# ── Status: happy path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_required_models_present_and_llm_tier_live(monkeypatch):
    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b", "nomic-embed-text")))
    _no_ml(monkeypatch)

    status = await svc.build_model_status(_BASE_CFG)

    assert status["ollama_reachable"] is True
    assert status["ollama_error"] is None
    assert status["installed_models"] == ["qwen2.5:7b", "nomic-embed-text"]
    assert {(r["purpose"], r["name"], r["present"], r["remedy"]) for r in status["required"]} == {
        ("llm", "qwen2.5:7b", True, None),
        ("embedding", "nomic-embed-text", True, None),
    }
    assert _entry(status, "llm") == {"mode": "llm", "available": True, "reason": None}
    assert _entry(status, "rules")["available"] is True
    assert status["offline_mode"] is True
    assert status["analysis_mode"] == "auto"
    assert status["checked_at"]


@pytest.mark.asyncio
async def test_chain_order_mirrors_auto_resolution(monkeypatch):
    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b", "nomic-embed-text")))
    _no_ml(monkeypatch)

    status = await svc.build_model_status(_BASE_CFG)

    # analysis_router.get_analysis_mode(): ML if trained → LLM → rules.
    assert [e["mode"] for e in status["fallback_chain"]] == ["ml", "llm", "rules"]


@pytest.mark.asyncio
async def test_trained_ml_model_marks_ml_tier_available(monkeypatch):
    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b", "nomic-embed-text")))
    monkeypatch.setattr(svc, "_ml_available", lambda: True)

    status = await svc.build_model_status(_BASE_CFG)

    assert _entry(status, "ml") == {"mode": "ml", "available": True, "reason": None}


@pytest.mark.asyncio
async def test_ml_availability_probe_failure_degrades_to_unavailable(monkeypatch):
    """A broken ML model dir must not 500 the settings page."""
    import app.services.ml.classifier as classifier_mod

    def _boom():
        raise OSError("model dir unreadable")

    monkeypatch.setattr(classifier_mod.MLClassifier, "is_available", staticmethod(_boom))
    assert svc._ml_available() is False


# ── Status: unreachable vs missing (the load-bearing distinction) ────────────


@pytest.mark.asyncio
async def test_unreachable_ollama_still_returns_chain_with_rules(monkeypatch):
    _install_client(monkeypatch, _Client(exc=RuntimeError("connection refused")))
    _no_ml(monkeypatch)

    status = await svc.build_model_status(_BASE_CFG)

    assert status["ollama_reachable"] is False
    assert "connection refused" in status["ollama_error"]
    assert status["installed_models"] == []

    llm = _entry(status, "llm")
    assert llm["available"] is False
    assert "unreachable" in llm["reason"]
    assert "connection refused" in llm["reason"]
    # An unreachable daemon must NOT be reported as a missing model, and
    # must not hand out a pull recipe that cannot possibly work yet.
    assert "ollama pull" not in llm["reason"].lower()
    assert all(r["remedy"] is None for r in status["required"])

    # Rules is the terminal fallback and is available no matter what.
    assert _entry(status, "rules") == {"mode": "rules", "available": True, "reason": None}


@pytest.mark.asyncio
async def test_missing_llm_model_names_the_pull_remedy(monkeypatch):
    _install_client(monkeypatch, _Client(_tags("nomic-embed-text")))
    _no_ml(monkeypatch)

    status = await svc.build_model_status(_BASE_CFG)

    assert status["ollama_reachable"] is True      # reachable, just incomplete
    assert status["ollama_error"] is None
    required = {r["purpose"]: r for r in status["required"]}
    assert required["llm"]["present"] is False
    assert "ollama pull qwen2.5:7b" in required["llm"]["remedy"]
    assert required["embedding"]["present"] is True
    assert required["embedding"]["remedy"] is None

    llm = _entry(status, "llm")
    assert llm["available"] is False
    assert "qwen2.5:7b" in llm["reason"]
    assert "ollama pull qwen2.5:7b" in llm["reason"]
    assert "side-load" in llm["reason"]
    assert _entry(status, "rules")["available"] is True


@pytest.mark.asyncio
async def test_missing_embedding_model_does_not_disable_llm_tier(monkeypatch):
    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b")))
    _no_ml(monkeypatch)

    status = await svc.build_model_status(_BASE_CFG)

    required = {r["purpose"]: r for r in status["required"]}
    assert required["embedding"]["present"] is False
    assert _entry(status, "llm")["available"] is True


@pytest.mark.asyncio
async def test_classifier_model_listed_when_configured(monkeypatch):
    from app.core.config import settings

    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b", "nomic-embed-text")))
    _no_ml(monkeypatch)
    monkeypatch.setattr(settings, "CLASSIFIER_MODEL", "qwen2.5:3b")

    status = await svc.build_model_status(_BASE_CFG)

    required = {r["purpose"]: r for r in status["required"]}
    assert required["classifier"]["name"] == "qwen2.5:3b"
    assert required["classifier"]["present"] is False


# ── Status: non-Ollama providers ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cloud_provider_blocked_by_offline_mode(monkeypatch):
    _no_ml(monkeypatch)
    cfg = {**_BASE_CFG, "llm_provider": "openai", "embedding_provider": "openai", "openai_api_key": "sk-x"}

    status = await svc.build_model_status(cfg)

    assert status["ollama_reachable"] is False
    assert "not probed" in status["ollama_error"]
    assert status["required"] == []          # nothing is required *from Ollama*
    llm = _entry(status, "llm")
    assert llm["available"] is False
    assert "offline mode" in llm["reason"]


@pytest.mark.asyncio
async def test_cloud_provider_without_key_is_unavailable(monkeypatch):
    _no_ml(monkeypatch)
    cfg = {**_BASE_CFG, "llm_provider": "openai", "embedding_provider": "openai", "ai_offline_mode": False}

    llm = _entry(await svc.build_model_status(cfg), "llm")

    assert llm["available"] is False
    assert "No API key" in llm["reason"]


@pytest.mark.asyncio
async def test_self_hosted_provider_is_available_but_flagged_unverified(monkeypatch):
    _no_ml(monkeypatch)
    cfg = {**_BASE_CFG, "llm_provider": "vllm", "embedding_provider": "vllm", "ai_offline_mode": False}

    llm = _entry(await svc.build_model_status(cfg), "llm")

    assert llm["available"] is True
    assert "unverified" in llm["reason"]


@pytest.mark.asyncio
async def test_empty_config_falls_back_to_env_defaults(monkeypatch):
    """A failed settings read must degrade to env truth, not a 500."""
    _install_client(monkeypatch, _Client(_tags()))
    _no_ml(monkeypatch)

    status = await svc.build_model_status(None)

    from app.core.config import settings
    assert status["llm_provider"] == settings.LLM_PROVIDER
    assert [e["mode"] for e in status["fallback_chain"]] == ["ml", "llm", "rules"]


# ── Shared-probe / shared-remedy wiring (no forked copies) ───────────────────


@pytest.mark.asyncio
async def test_health_check_ollama_delegates_to_shared_probe(monkeypatch):
    from app.core.config import settings
    from app.routers import health

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b")))

    assert await health._check_ollama() == {"status": "ok", "models": ["qwen2.5:7b"]}

    _install_client(monkeypatch, _Client(exc=RuntimeError("down")))
    degraded = await health._check_ollama()
    assert degraded["status"] == "degraded"
    assert "down" in degraded["detail"]


@pytest.mark.asyncio
async def test_health_check_ollama_still_skips_when_offline_mode_off(monkeypatch):
    """Pinned as-is: the guard is coherent (offline mode == local models
    only), so /health/details keeps its shape. The provider-keyed truth
    lives in the model-status endpoint instead."""
    from app.core.config import settings
    from app.routers import health

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    assert (await health._check_ollama())["status"] == "skipped"


def test_health_router_has_no_forked_ollama_http_call():
    source = (REPO_BACKEND / "app" / "routers" / "health.py").read_text(encoding="utf-8")
    assert "/api/tags" not in source, "health.py must delegate to model_status_service.probe_ollama"


def test_pull_command_recipe_is_runtime_aware(monkeypatch):
    monkeypatch.setattr(svc.os.path, "exists", lambda p: False)
    assert svc.ollama_pull_command("qwen2.5:7b") == "docker compose exec ollama ollama pull qwen2.5:7b"

    monkeypatch.setattr(svc.os.path, "exists", lambda p: True)
    monkeypatch.setenv("KUBERNETES_NAMESPACE", "tl-prod")
    assert svc.ollama_pull_command("qwen2.5:7b") == (
        "kubectl -n tl-prod exec deploy/testlookup-ollama -- ollama pull qwen2.5:7b"
    )


def test_agent_missing_model_hint_shares_the_pull_recipe(monkeypatch):
    agent = pytest.importorskip("app.services.agent")
    monkeypatch.setattr(svc, "ollama_pull_command", lambda m: f"SENTINEL-PULL {m}")

    hint = agent._model_missing_hint("qwen2.5:7b")

    assert "SENTINEL-PULL qwen2.5:7b" in hint


# ── Endpoint contract + authorization ────────────────────────────────────────


def test_model_status_endpoint_is_qa_lead_gated():
    """Guard lives on the route decorator, mirroring GET /settings/ai.

    Asserted against source so the check survives the module-stubbing
    other suites do to ``app.core.deps``.
    """
    source = (REPO_BACKEND / "app" / "routers" / "app_settings.py").read_text(encoding="utf-8")
    block = re.search(
        r'@router\.get\(\s*"/ai/model-status".*?async def get_ai_model_status\((.*?)\)\s*->',
        source,
        re.DOTALL,
    )
    assert block, "GET /ai/model-status route not found"
    assert "require_role(UserRole.QA_LEAD)" in block.group(1)


@pytest.mark.asyncio
async def test_require_role_qa_lead_rejects_lower_roles():
    """Behavioural half of the guard: below QA_LEAD → 403."""
    from fastapi import HTTPException

    from app.core.deps import require_role
    from app.models.postgres import UserRole

    check = require_role(UserRole.QA_LEAD)

    with pytest.raises(HTTPException) as exc:
        await check(current_user=SimpleNamespace(role=UserRole.QA_ENGINEER))
    assert exc.value.status_code == 403

    lead = SimpleNamespace(role=UserRole.QA_LEAD)
    assert await check(current_user=lead) is lead


class _FakeResult:
    def scalar_one_or_none(self):
        return None            # no app_settings override row


class _FakeDB:
    def __init__(self, exc: Exception | None = None):
        self._exc = exc

    async def execute(self, *_a, **_kw):
        if self._exc is not None:
            raise self._exc
        return _FakeResult()


@pytest.mark.asyncio
async def test_endpoint_returns_contract_for_an_unconfigured_install(monkeypatch):
    from app.routers.app_settings import get_ai_model_status

    _install_client(monkeypatch, _Client(_tags("qwen2.5:7b", "nomic-embed-text")))
    _no_ml(monkeypatch)

    result = await get_ai_model_status(_=SimpleNamespace(id="u1"), db=_FakeDB())

    assert result.ollama_reachable is True
    assert [e.mode for e in result.fallback_chain] == ["ml", "llm", "rules"]
    assert result.fallback_chain[-1].available is True


@pytest.mark.asyncio
async def test_endpoint_degrades_when_the_settings_read_fails(monkeypatch):
    """A broken settings read must not 500 the page that diagnoses breakage."""
    from app.routers.app_settings import get_ai_model_status

    _install_client(monkeypatch, _Client(exc=RuntimeError("no route to host")))
    _no_ml(monkeypatch)

    result = await get_ai_model_status(
        _=SimpleNamespace(id="u1"), db=_FakeDB(exc=RuntimeError("db down")),
    )

    assert result.ollama_reachable is False
    assert "no route to host" in (result.ollama_error or "")
    assert [e.mode for e in result.fallback_chain] == ["ml", "llm", "rules"]


def test_model_status_response_schema_accepts_the_service_payload():
    """The router's response_model must not 422 the service's own output."""
    from app.routers.app_settings import AIModelStatusRead

    payload = {
        "ollama_reachable": False,
        "ollama_error": "connection refused",
        "ollama_base_url": "http://ollama:11434",
        "installed_models": [],
        "required": [{"name": "qwen2.5:7b", "purpose": "llm", "present": False, "remedy": None}],
        "fallback_chain": [
            {"mode": "ml", "available": False, "reason": "no model"},
            {"mode": "llm", "available": False, "reason": "unreachable"},
            {"mode": "rules", "available": True, "reason": None},
        ],
        "offline_mode": True,
        "llm_provider": "ollama",
        "analysis_mode": "auto",
        "checked_at": "2026-08-03T00:00:00+00:00",
    }
    model = AIModelStatusRead.model_validate(payload)
    assert model.fallback_chain[-1].mode == "rules"
    assert model.required[0].present is False
