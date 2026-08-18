from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_chat_search_consumes_persisted_chroma_collection(monkeypatch):
    """The editable collection field must select the collection queried by chat."""
    from app.agents.conversation import ConversationAgent
    from app.db import chroma
    from app.services import llm_factory, storage_config_service

    monkeypatch.setattr(
        storage_config_service,
        "get_effective_storage_config",
        AsyncMock(return_value={
            "chroma_host": "saved-chroma",
            "chroma_port": 9123,
            "chroma_collection": "saved-vectors",
        }),
    )
    collection = MagicMock()
    collection.count.return_value = 1
    collection.query.return_value = {"documents": [["grounded result"]]}
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(chroma, "get_chroma_client", client_factory)
    monkeypatch.setattr(
        llm_factory,
        "get_embedding_model",
        lambda: SimpleNamespace(embed_query=lambda _query: [0.1, 0.2]),
    )

    result = await ConversationAgent._semantic_search(object(), "timeout", "project-1")

    client_factory.assert_called_once_with(host="saved-chroma", port=9123)
    client.get_or_create_collection.assert_called_once_with("saved-vectors")
    assert result == "- grounded result"


@pytest.mark.asyncio
async def test_health_probes_the_effective_storage_endpoints(monkeypatch):
    """Operations health must probe the same endpoints runtime clients use."""
    from app.core import http_client
    from app.routers import health
    from app.services import integration_probe_service, storage_config_service

    monkeypatch.setattr(
        storage_config_service,
        "get_effective_storage_config",
        AsyncMock(return_value={
            "minio_endpoint": "saved-minio:9443",
            "minio_use_ssl": True,
            "chroma_host": "saved-chroma",
            "chroma_port": 9123,
        }),
    )
    client = MagicMock()
    client.get = AsyncMock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(http_client, "get_http_client", lambda: client)
    monkeypatch.setattr(integration_probe_service, "get_http_client", lambda: client)

    assert await health._check_minio() == {"status": "ok"}
    assert await health._check_chromadb() == {"status": "ok"}
    probe = await integration_probe_service.probe_chromadb()
    assert probe.status == "healthy"
    assert [call.args[0] for call in client.get.await_args_list] == [
        "https://saved-minio:9443/minio/health/live",
        "http://saved-chroma:9123/api/v2/heartbeat",
        "http://saved-chroma:9123/api/v2/heartbeat",
    ]
