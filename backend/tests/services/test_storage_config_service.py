from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_db_overrides_are_cached_as_effective_storage_config(monkeypatch):
    from app.db import postgres, redis_client
    from app.services import storage_config_service as service

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)

    row = SimpleNamespace(value={
        "storage_backend": "s3",
        "minio_endpoint": "s3.example.test",
        "minio_bucket_name": "runtime-artifacts",
        "minio_use_ssl": True,
    })
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: SessionContext())

    config = await service.get_effective_storage_config()

    assert config["storage_backend"] == "s3"
    assert config["minio_endpoint"] == "s3.example.test"
    assert config["minio_bucket_name"] == "runtime-artifacts"
    cached = json.loads(redis.setex.await_args.args[2])
    assert cached == config


@pytest.mark.asyncio
async def test_shared_cache_avoids_process_local_db_authority(monkeypatch):
    from app.db import postgres, redis_client
    from app.services import storage_config_service as service

    cached = {
        "storage_backend": "local",
        "minio_endpoint": "unused",
        "minio_bucket_name": "shared-bucket",
        "minio_use_ssl": False,
        "chroma_host": "chroma",
        "chroma_port": 8000,
        "chroma_collection": "vectors",
    }
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps(cached))
    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    db_factory = MagicMock()
    monkeypatch.setattr(postgres, "AsyncSessionLocal", db_factory)

    assert await service.get_effective_storage_config() == cached
    db_factory.assert_not_called()
