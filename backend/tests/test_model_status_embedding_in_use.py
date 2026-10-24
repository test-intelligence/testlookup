"""W12 SRCH-02 §2.4 test 8: an Ollama embedding model is REQUIRED when it is the
ACTIVE or the TARGET embedder (``in_use`` true for the active one), not when it
is merely configured; ``embedding=None`` (every pre-W12 direct caller) keeps
the configured-embedder rule. ``GET /settings/ai/model-status`` passes the
state's active / target.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.routers import app_settings
from app.services import embedding_index_state as eis
from app.services import model_status_service as svc
from app.services.chroma_collections import Generation
from app.services.embedding_function import DEFAULT_EMBEDDER, EmbedderSpec

CLOUD_LLM_OLLAMA_EMBEDDER = {
    "llm_provider": "openai",
    "llm_model": "gpt-4o-mini",
    "embedding_provider": "ollama",
    "embedding_model": "nomic-embed-text:v1.5",
    "ai_offline_mode": False,
}
DEFAULT_ENTRY = {"provider": "default", "model": "all-MiniLM-L6-v2"}
NOMIC = {"provider": "ollama", "model": "nomic-embed-text:v1.5"}
MXBAI = {"provider": "ollama", "model": "mxbai-embed-large"}


@pytest.fixture
def probes(monkeypatch):
    calls: list[str] = []

    async def probe(base_url=None, timeout=None):
        calls.append(base_url)
        return {"reachable": True, "models": ["nomic-embed-text:v1.5"], "error": None}

    monkeypatch.setattr(svc, "probe_ollama", probe)
    monkeypatch.setattr(svc, "_ml_available", lambda: False)
    return calls


def _embedding_required(status):
    return [r for r in status["required"] if r["purpose"] == "embedding"]


async def test_without_the_state_the_configured_embedder_decides(probes):
    status = await svc.build_model_status(CLOUD_LLM_OLLAMA_EMBEDDER)

    assert _embedding_required(status) == [
        {"name": "nomic-embed-text:v1.5", "purpose": "embedding", "present": True, "remedy": None}
    ]
    assert len(probes) == 1


async def test_a_configured_but_unapplied_ollama_embedder_is_not_required(probes):
    status = await svc.build_model_status(
        CLOUD_LLM_OLLAMA_EMBEDDER, embedding={"active": DEFAULT_ENTRY, "target": None}
    )

    assert status["required"] == []
    assert probes == []                       # nothing on the path runs on Ollama: not probed
    assert status["ollama_reachable"] is False


async def test_the_active_ollama_embedder_is_required_and_in_use(probes):
    status = await svc.build_model_status(
        CLOUD_LLM_OLLAMA_EMBEDDER, embedding={"active": NOMIC, "target": None}
    )

    assert _embedding_required(status) == [
        {"name": "nomic-embed-text:v1.5", "purpose": "embedding", "present": True, "remedy": None, "in_use": True}
    ]


async def test_a_target_embedder_is_required_but_not_yet_in_use(probes):
    status = await svc.build_model_status(
        CLOUD_LLM_OLLAMA_EMBEDDER, embedding={"active": NOMIC, "target": MXBAI}
    )

    rows = {(r["name"], r["in_use"], r["present"]) for r in _embedding_required(status)}
    assert rows == {("nomic-embed-text:v1.5", True, True), ("mxbai-embed-large", False, False)}
    missing = next(r for r in _embedding_required(status) if r["name"] == "mxbai-embed-large")
    assert missing["remedy"]                  # the daemon answers: the pull hint is shown


async def test_the_same_model_as_active_and_target_is_listed_once(probes):
    status = await svc.build_model_status(
        CLOUD_LLM_OLLAMA_EMBEDDER, embedding={"active": NOMIC, "target": dict(NOMIC)}
    )

    assert [(r["name"], r["in_use"]) for r in _embedding_required(status)] == [("nomic-embed-text:v1.5", True)]


async def _route_status(monkeypatch, state):
    async def load(db):
        return dict(CLOUD_LLM_OLLAMA_EMBEDDER)

    async def read_state(db):
        return state

    monkeypatch.setattr(app_settings, "_load_ai_config", load)
    monkeypatch.setattr(eis, "read_state", read_state)
    return await app_settings.get_ai_model_status(_=MagicMock(), db=MagicMock())


async def test_the_route_passes_the_state_s_active_and_target(probes, monkeypatch):
    state = eis.EmbeddingIndexState(
        active=Generation("", EmbedderSpec("ollama", "nomic-embed-text:v1.5")).as_dict(),
        target=Generation("demo", DEFAULT_EMBEDDER).as_dict(),
        status="building",
    )

    body = await _route_status(monkeypatch, state)

    embedding = [r for r in body.required if r.purpose == "embedding"]
    assert [(r.name, r.in_use) for r in embedding] == [("nomic-embed-text:v1.5", True)]


async def test_an_unreadable_state_keeps_the_configured_embedder_rule(probes, monkeypatch):
    body = await _route_status(monkeypatch, None)

    embedding = [r for r in body.required if r.purpose == "embedding"]
    assert [(r.name, r.in_use) for r in embedding] == [("nomic-embed-text:v1.5", None)]
