"""HON-08: /search/index-status tells "no ChromaDB here" apart from "ChromaDB failed".

The /search right rail turned any non-healthy status into six entity rows all
marked "Down", although five of those entities are searched by SQL keyword
and have no index at all. An install without ChromaDB (``make dev`` starts
none) is not down: nothing answers at the configured address, so the status
is ``not_configured``. A server that answered and then failed stays
``unavailable``.

The first test drives the real chromadb HttpClient against a closed port, so
the classification rests on what the client library actually raises, not on
an exception this file invented.
"""
from __future__ import annotations

import socket
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytestmark = pytest.mark.regression


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _no_cursor():
    redis_client = MagicMock()
    redis_client.get.return_value = None
    return patch("redis.Redis.from_url", return_value=redis_client)


@pytest.mark.asyncio
async def test_no_chroma_server_at_the_configured_address_is_not_configured():
    # Drives the REAL client library, so it needs the package (CI and every image
    # install it; a dev Python without it skips rather than misreading the
    # ImportError as "unavailable").
    pytest.importorskip("chromadb")
    from app.db.chroma import get_chroma_client
    from app.services import semantic_search

    port = _closed_port()

    async def _real_client_to_nowhere():
        return get_chroma_client(host="127.0.0.1", port=port)

    with patch.object(semantic_search, "_get_chroma_client", _real_client_to_nowhere), _no_cursor():
        result = await semantic_search.get_index_status(allowed_project_ids={uuid.uuid4()})

    assert result["status"] == "not_configured"
    assert result["document_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("[Errno 111] Connection refused"),
        ConnectionRefusedError(111, "Connection refused"),
    ],
    ids=["httpx-connect-error", "connection-refused"],
)
async def test_a_refused_connection_is_not_configured(error):
    from app.services import semantic_search

    with patch.object(
        semantic_search, "_get_or_create_collection", AsyncMock(side_effect=error),
    ), _no_cursor():
        result = await semantic_search.get_index_status(allowed_project_ids={uuid.uuid4()})

    assert result["status"] == "not_configured"


@pytest.mark.asyncio
async def test_a_server_that_answers_and_fails_is_unavailable():
    from app.services import semantic_search

    collection = MagicMock()
    collection.get.side_effect = RuntimeError("collection metadata is corrupt")
    with patch.object(
        semantic_search, "_get_or_create_collection", AsyncMock(return_value=collection),
    ), _no_cursor():
        result = await semantic_search.get_index_status(allowed_project_ids={uuid.uuid4()})

    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_a_reachable_index_is_healthy_however_long_ago_it_was_indexed():
    """Idle is not down: the 5-minute staleness rule lived in the SPA, and the
    service never judged freshness. Pin that it still does not."""
    from app.services import semantic_search

    collection = MagicMock()
    collection.get.return_value = {"ids": ["a", "b", "c"]}
    redis_client = MagicMock()
    redis_client.get.return_value = b"2026-01-01T00:00:00+00:00"
    with patch.object(
        semantic_search, "_get_or_create_collection", AsyncMock(return_value=collection),
    ), patch("redis.Redis.from_url", return_value=redis_client):
        result = await semantic_search.get_index_status(allowed_project_ids={uuid.uuid4()})

    assert result == {
        "status": "healthy",
        "document_count": 3,
        "last_indexed_at": "2026-01-01T00:00:00+00:00",
    }
