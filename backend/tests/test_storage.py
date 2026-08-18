from pathlib import Path
import shutil
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.core.config import settings
from app.db.storage import LocalStorageProvider

@pytest.fixture
def temp_storage_path(monkeypatch):
    storage_root = Path.cwd() / "backend" / "tmp_storage_test"
    shutil.rmtree(storage_root, ignore_errors=True)
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(storage_root))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    try:
        yield str(storage_root)
    finally:
        shutil.rmtree(storage_root, ignore_errors=True)

@pytest.mark.asyncio
async def test_local_storage_provider(temp_storage_path):
    provider = LocalStorageProvider()
    
    bucket = "test-bucket"
    key = "test-folder/test-file.txt"
    content = b"Hello Storage!"

    # Test put_object
    await provider.put_object(key=key, content=content, bucket=bucket)
    
    # Verify file was written
    expected_path = Path(temp_storage_path) / bucket / key
    assert expected_path.exists()
    assert expected_path.read_bytes() == content

    # Test get_object_content
    retrieved_content = await provider.get_object_content(key=key, bucket=bucket)
    assert retrieved_content == content

    # Test list_objects
    objects = await provider.list_objects(prefix="test-folder", bucket=bucket)
    assert len(objects) == 1
    assert objects[0]["Key"] == "test-folder/test-file.txt"
    assert objects[0]["Size"] == len(content)

    # Test stream_object
    chunks = []
    async for chunk in provider.stream_object(key=key, bucket=bucket):
        chunks.append(chunk)
    assert b"".join(chunks) == content

    # Test get_presigned_url — use Path.as_uri() for cross-platform comparison
    url = await provider.get_presigned_url(key=key, bucket=bucket)
    assert url == expected_path.resolve().as_uri()


@pytest.mark.asyncio
async def test_local_storage_rejects_key_traversal(temp_storage_path):
    provider = LocalStorageProvider()

    with pytest.raises(ValueError):
        await provider.put_object(
            key="../outside.txt",
            content=b"blocked",
            bucket="test-bucket",
        )

    assert not (Path(temp_storage_path) / "outside.txt").exists()


@pytest.mark.asyncio
async def test_local_storage_rejects_bucket_traversal(temp_storage_path):
    provider = LocalStorageProvider()

    with pytest.raises(ValueError):
        await provider.get_object_content(
            key="object.txt",
            bucket="../outside-bucket",
        )


@pytest.mark.asyncio
async def test_dynamic_provider_consumes_changed_runtime_config(monkeypatch):
    """Saved endpoint/backend/bucket values must reach real storage operations."""
    from app.db import storage
    from app.services import storage_config_service

    configs = iter([
        {
            "storage_backend": "minio",
            "minio_endpoint": "minio-a:9000",
            "minio_use_ssl": False,
            "minio_bucket_name": "bucket-a",
        },
        {
            "storage_backend": "s3",
            "minio_endpoint": "s3.example.test",
            "minio_use_ssl": True,
            "minio_bucket_name": "bucket-b",
        },
    ])

    async def resolve_config():
        return next(configs)

    first = MagicMock()
    first.put_object = AsyncMock()
    second = MagicMock()
    second.put_object = AsyncMock()
    factory = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(storage_config_service, "get_effective_storage_config", resolve_config)
    monkeypatch.setattr(storage, "_get_concrete_storage_provider", factory)

    provider = storage.DynamicStorageProvider()
    await provider.put_object("one.json", b"one")
    await provider.put_object("two.json", b"two")

    factory.assert_has_calls([
        call("minio", "minio-a:9000", False, "bucket-a"),
        call("s3", "s3.example.test", True, "bucket-b"),
    ])
    first.put_object.assert_awaited_once_with("one.json", b"one", "application/json", None)
    second.put_object.assert_awaited_once_with("two.json", b"two", "application/json", None)
