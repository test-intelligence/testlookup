"""Storage Provider abstraction for S3, MinIO, and Local File System."""
import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator, cast

import aioboto3
from botocore.client import Config

from app.core.config import settings

class StorageProvider(ABC):
    """Abstract base class for storage operations."""

    @abstractmethod
    async def list_objects(self, prefix: str, bucket: str | None = None) -> list[dict]:
        raise NotImplementedError()

    @abstractmethod
    async def get_object_content(self, key: str, bucket: str | None = None) -> bytes:
        raise NotImplementedError()

    @abstractmethod
    async def stream_object(self, key: str, bucket: str | None = None) -> AsyncGenerator[bytes, None]:
        yield b""
        raise NotImplementedError()

    @abstractmethod
    async def put_object(self, key: str, content: bytes, content_type: str = "application/json", bucket: str | None = None) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def get_presigned_url(self, key: str, expiry: int = 3600, bucket: str | None = None) -> str:
        raise NotImplementedError()


class S3StorageProvider(StorageProvider):
    """S3/MinIO compatible storage provider with connection pooling."""

    def __init__(self):
        # Single session reused across all requests — aioboto3 manages the pool internally
        self._session = aioboto3.Session()
        self._endpoint = f"{'https' if settings.MINIO_USE_SSL else 'http'}://{settings.MINIO_ENDPOINT}"
        self._config = Config(
            signature_version="s3v4",
            max_pool_connections=settings.S3_MAX_POOL_CONNECTIONS,
        )

    def get_client_context(self):
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=self._config,
            region_name="us-east-1",
        )

    async def list_objects(self, prefix: str, bucket: str | None = None) -> list[dict]:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        objects = []
        async with self.get_client_context() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    objects.append(obj)
        return objects

    async def get_object_content(self, key: str, bucket: str | None = None) -> bytes:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        async with self.get_client_context() as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            async with response["Body"] as stream:
                return cast(bytes, await stream.read())

    async def stream_object(self, key: str, bucket: str | None = None) -> AsyncGenerator[bytes, None]:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        async with self.get_client_context() as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            async with response["Body"] as stream:
                while chunk := await stream.read(65536):
                    yield chunk

    async def put_object(self, key: str, content: bytes, content_type: str = "application/json", bucket: str | None = None) -> None:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        async with self.get_client_context() as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )

    async def get_presigned_url(self, key: str, expiry: int = 3600, bucket: str | None = None) -> str:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        async with self.get_client_context() as s3:
            return cast(str, await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiry,
            ))


class LocalStorageProvider(StorageProvider):
    """Local file system storage provider."""

    def __init__(self):
        self.base_path = Path(settings.LOCAL_STORAGE_PATH)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, key: str, bucket: str | None = None) -> Path:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        # Ensure path stays within base_path
        full_path = self.base_path / bucket / key
        return cast(Path, full_path.resolve())

    async def list_objects(self, prefix: str, bucket: str | None = None) -> list[dict]:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        bucket_dir = self.base_path / bucket
        if not bucket_dir.exists():
            return []

        def _list():
            objects = []
            for root, _, files in os.walk(bucket_dir):
                for file in files:
                    full_path = Path(root) / file
                    # Calculate relative key
                    key = full_path.relative_to(bucket_dir).as_posix()
                    if key.startswith(prefix):
                        objects.append({
                            "Key": key,
                            "Size": full_path.stat().st_size,
                            "LastModified": full_path.stat().st_mtime
                        })
            return objects

        return await asyncio.to_thread(_list)

    async def get_object_content(self, key: str, bucket: str | None = None) -> bytes:
        full_path = self._get_full_path(key, bucket)
        if not full_path.exists():
            raise FileNotFoundError(f"Object not found: {key}")

        def _read():
            with open(full_path, "rb") as f:
                return f.read()

        return cast(bytes, await asyncio.to_thread(_read))

    async def stream_object(self, key: str, bucket: str | None = None) -> AsyncGenerator[bytes, None]:
        full_path = self._get_full_path(key, bucket)
        if not full_path.exists():
            raise FileNotFoundError(f"Object not found: {key}")

        f = await asyncio.to_thread(open, full_path, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(f.read, 65536)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    async def put_object(self, key: str, content: bytes, content_type: str = "application/json", bucket: str | None = None) -> None:
        full_path = self._get_full_path(key, bucket)
        
        def _write():
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)

        await asyncio.to_thread(_write)

    async def get_presigned_url(self, key: str, expiry: int = 3600, bucket: str | None = None) -> str:
        # Local storage doesn't really have presigned URLs in the same way,
        # but could return a generic local path or API route representing it.
        # For our ingestion use case, it's rarely used to redirect clients.
        return cast(str, self._get_full_path(key, bucket).as_uri())


def get_storage_provider() -> StorageProvider:
    """Factory to return the configured storage provider."""
    backend_type = settings.STORAGE_BACKEND.lower()
    if backend_type in ("minio", "s3"):
        return S3StorageProvider()
    elif backend_type == "local":
        return LocalStorageProvider()
    else:
        raise ValueError(f"Unknown STORAGE_BACKEND: {backend_type}")
