"""W12 SRCH-01: ``GET /search/index-status`` says whether Hybrid / Semantic can answer.

The page enables the two modes from ``semantic_available`` and shows
``semantic_reason_text`` as the disabled chips' reason. ONE pure helper
decides (``core/search_vocab.semantic_availability``) from the fields
``get_index_status`` returns; the route only annotates the service's dict in
place, so every existing key and the scope rule are untouched.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.search_vocab import SEMANTIC_REASON_TEXT, SEMANTIC_UNAVAILABLE_REASONS, semantic_availability


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"status": "healthy", "document_count": 12, "embedder_available": True}, (True, None)),
        ({"status": "healthy", "document_count": 12, "embedder_available": None}, (True, None)),
        ({"status": "healthy", "document_count": 12}, (True, None)),
        ({"status": "not_configured", "document_count": 0}, (False, "not_configured")),
        ({"status": "unavailable", "document_count": 0}, (False, "unavailable")),
        ({"status": "unknown", "document_count": 0}, (False, "unknown")),
        ({"document_count": 5}, (False, "unknown")),
        ({"status": "healthy", "document_count": 12, "embedder_available": False}, (False, "embedder_missing")),
        ({"status": "healthy", "document_count": 0, "embedder_available": True}, (False, "index_empty")),
    ],
)
def test_the_availability_table(status, expected):
    assert semantic_availability(status) == expected
    if expected[1] is not None:
        assert expected[1] in SEMANTIC_UNAVAILABLE_REASONS


def _db(project_id):
    result = MagicMock()
    result.scalars.return_value.all.return_value = [project_id]
    return SimpleNamespace(execute=AsyncMock(return_value=result))


#: What G-C's generation reader answers for the legacy generation with the weights on disk.
LEGACY_FIELDS = {
    "collection": "test_case_search", "generation": "",
    "embedder": {"provider": "default", "model": "all-MiniLM-L6-v2"},
    "embedder_available": True, "reembed": None,
}


async def _route(status: dict, generation_fields: dict | None = None):
    """The real handler; the service and G-C's generation reader patched. Returns
    ``(body, service mock, reader mock)``."""
    from app.routers.search import get_index_status

    project_id = uuid.uuid4()
    fields = AsyncMock(return_value=dict(LEGACY_FIELDS if generation_fields is None else generation_fields))
    with (
        patch("app.routers.search.resolve_project_scope", AsyncMock(return_value=(project_id, None))),
        patch("app.services.semantic_search.get_index_status", AsyncMock(return_value=status)) as service,
        patch("app.services.semantic_search._generation_status_fields", fields),
    ):
        body = await get_index_status(project_id=str(project_id), db=_db(project_id), current_user=MagicMock())
    return body, service, fields


async def test_a_healthy_index_with_documents_offers_the_modes():
    status = {"status": "healthy", "document_count": 7, "last_indexed_at": None, "collection": "test_case_search",
              "generation": "", "embedder": {"provider": "default", "model": "all-MiniLM-L6-v2"},
              "embedder_available": True, "reembed": None}
    result, _, _ = await _route(status)
    assert result is status, "the body is the service's dict, annotated in place"
    assert result["semantic_available"] is True
    assert result["semantic_reason"] is None and result["semantic_reason_text"] is None
    assert result["document_count"] == 7 and result["collection"] == "test_case_search"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ({"status": "not_configured", "document_count": 0, "last_indexed_at": None}, "not_configured"),
        ({"status": "unavailable", "document_count": 0, "last_indexed_at": None}, "unavailable"),
        ({"status": "healthy", "document_count": 0, "last_indexed_at": None}, "index_empty"),
        ({"status": "healthy", "document_count": 3, "last_indexed_at": None, "embedder_available": False},
         "embedder_missing"),
    ],
)
async def test_an_unavailable_index_says_why_in_a_sentence(status, reason):
    result, _, _ = await _route(status)
    assert result["semantic_available"] is False
    assert result["semantic_reason"] == reason
    assert result["semantic_reason_text"] == SEMANTIC_REASON_TEXT[reason]


async def test_every_new_key_is_present_even_when_nothing_could_be_read():
    unreadable = dict.fromkeys(LEGACY_FIELDS)  # what G-C's reader answers when it can read nothing
    result, _, _ = await _route({"status": "not_configured", "document_count": 0, "last_indexed_at": None}, unreadable)
    for key in ("collection", "generation", "embedder", "embedder_available", "reembed",
                "semantic_available", "semantic_reason", "semantic_reason_text"):
        assert key in result, key
    assert result["collection"] is None and result["embedder"] is None
    assert result["semantic_reason_text"] == "Needs the semantic index: no ChromaDB server answers here."


# ── The five generation keys reach the route (W12 SRCH-02, G-C's reader) ────


async def test_the_generation_keys_are_read_and_a_missing_embedder_is_reported():
    """The service answers its three keys by default (its call shape is pinned by
    ``tests/regression/test_search_index_status_scope.py``); the route fills the five
    generation keys from G-C's reader -- without them ``embedder_missing`` is never
    reported and the page cannot say which embedder is in use."""
    status = {"status": "healthy", "document_count": 12, "last_indexed_at": None}
    fields = {
        "collection": "demo_test_case_search", "generation": "e3f9a1c2",
        "embedder": {"provider": "default", "model": "all-MiniLM-L6-v2"},
        "embedder_available": False,
        "reembed": {"status": "building", "target_provider": "ollama", "target_model": "nomic-embed-text:v1.5",
                    "processed": 3, "total": 12},
    }
    result, service, reader = await _route(status, fields)
    reader.assert_awaited_once_with()
    for key, value in fields.items():
        assert result[key] == value, key
    assert result["semantic_available"] is False
    assert result["semantic_reason"] == "embedder_missing"
    assert result["semantic_reason_text"] == SEMANTIC_REASON_TEXT["embedder_missing"]
    assert set(service.await_args.kwargs) == {"project_id", "allowed_project_ids"}, "the pinned call shape changed"


async def test_keys_the_service_already_answered_are_not_read_again():
    status = {"status": "healthy", "document_count": 4, "last_indexed_at": None, **LEGACY_FIELDS,
              "collection": "from_the_service"}
    result, _, reader = await _route(status, {**LEGACY_FIELDS, "collection": "from_the_reader"})
    reader.assert_not_awaited()
    assert result["collection"] == "from_the_service"
    assert result["semantic_available"] is True
