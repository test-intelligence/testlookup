"""W12 SRCH-02: the index status's generation fields.

``get_index_status(..., with_generation=False)`` (the default every internal
probe and ``test_search_index_status_not_configured`` use) keeps its THREE
keys and never reads the generation; ``_generation_status_fields()`` (what
``GET /search/index-status`` merges in) answers the FIVE keys from the cached
generation read only, and never raises.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import chroma_collections as cc
from app.services import semantic_search
from app.services.embedding_function import DEFAULT_EMBEDDER, EmbedderSpec

FIVE = {"collection", "generation", "embedder", "embedder_available", "reembed"}
DEMO = cc.Generation("demo", DEFAULT_EMBEDDER)
OLLAMA = cc.Generation("", EmbedderSpec("ollama", "nomic-embed-text:v1.5"))


def _no_cursor():
    redis_client = MagicMock()
    redis_client.get.return_value = None
    return patch("redis.Redis.from_url", return_value=redis_client)


def _collection(ids):
    collection = MagicMock()
    collection.get.return_value = {"ids": ids}
    collection.count.return_value = len(ids)
    return collection


@pytest.fixture
def generations(monkeypatch):
    holder = {"value": cc.LEGACY_GENERATIONS, "calls": 0}

    async def current():
        holder["calls"] += 1
        if isinstance(holder["value"], Exception):
            raise holder["value"]
        return holder["value"]

    async def ef_for(generation):
        return None if generation.embedder.provider == "default" else object()

    monkeypatch.setattr(cc, "current_generations", current)
    monkeypatch.setattr(cc, "embedding_function_for", ef_for)
    monkeypatch.setattr("app.services.local_embedder_guard.embedder_status", lambda: {"available": True})
    return holder


async def test_the_default_answer_keeps_its_three_keys_and_reads_no_generation(generations):
    with patch.object(semantic_search, "_get_or_create_collection", AsyncMock(return_value=_collection(["a", "b"]))), \
            _no_cursor():
        result = await semantic_search.get_index_status(allowed_project_ids={uuid.uuid4()})

    assert result == {"status": "healthy", "document_count": 2, "last_indexed_at": None}
    assert generations["calls"] == 0


async def test_with_generation_adds_the_five_keys_in_the_not_configured_branch_too(generations):
    refused = AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused"))
    with patch.object(semantic_search, "_get_or_create_collection", refused), _no_cursor():
        result = await semantic_search.get_index_status(
            allowed_project_ids={uuid.uuid4()}, with_generation=True
        )

    assert set(result) == {"status", "document_count", "last_indexed_at"} | FIVE
    assert result["status"] == "not_configured"
    assert result["collection"] == "test_case_search" and result["generation"] == ""


async def test_the_legacy_generation_reads_today_s_collection_and_the_bundled_embedder(generations):
    fields = await semantic_search._generation_status_fields()

    assert fields == {
        "collection": "test_case_search",
        "generation": "",
        "embedder": {"provider": "default", "model": "all-MiniLM-L6-v2"},
        "embedder_available": True,
        "reembed": None,
    }


async def test_a_build_in_progress_reports_its_target_and_progress(generations):
    generations["value"] = cc.Generations(DEMO, OLLAMA, (), status="building", processed=1200, total=2290)

    fields = await semantic_search._generation_status_fields()

    assert fields["collection"] == "demo_test_case_search"          # reads stay on the ACTIVE generation
    assert fields["generation"] == DEMO.tag
    assert fields["reembed"] == {
        "status": "building",
        "target_provider": "ollama",
        "target_model": "nomic-embed-text:v1.5",
        "processed": 1200,
        "total": 2290,
    }


async def test_an_ollama_active_embedder_is_not_probed_on_a_read(generations, monkeypatch):
    def must_not_probe():
        raise AssertionError("the bundled-embedder guard was consulted for an Ollama generation")

    monkeypatch.setattr("app.services.local_embedder_guard.embedder_status", must_not_probe)
    generations["value"] = cc.Generations(OLLAMA, None, (), status="idle")

    fields = await semantic_search._generation_status_fields()

    assert fields["embedder"] == {"provider": "ollama", "model": "nomic-embed-text:v1.5"}
    assert fields["embedder_available"] is None and fields["reembed"] is None
    assert fields["collection"] == cc.collection_name("test_case_search", generation=OLLAMA)


async def test_an_unreadable_generation_degrades_to_five_nones(generations):
    generations["value"] = RuntimeError("redis and postgres both down")

    fields = await semantic_search._generation_status_fields()

    assert fields == dict.fromkeys(FIVE)
