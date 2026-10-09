from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


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
