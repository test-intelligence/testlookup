"""Storage Provider abstraction for S3, MinIO, and Local File System."""
import asyncio
import logging
import os
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncGenerator, cast

from app.core.config import settings

logger = logging.getLogger("db.storage")

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

    @abstractmethod
    async def delete_object(self, key: str, bucket: str | None = None) -> None:
        """Delete a single object. Missing objects are a no-op (idempotent —
        the retention purge must be safely re-runnable)."""
        raise NotImplementedError()

    @abstractmethod
    async def delete_prefix(self, prefix: str, bucket: str | None = None) -> int:
        """Delete every object under ``prefix``; returns the number deleted.

        ``prefix`` must be non-empty (an empty/"/" prefix would wipe the
        whole bucket — callers must never get that for free).
        """
        raise NotImplementedError()


def _is_missing_bucket(exc: Exception) -> bool:
    """True for S3's "bucket does not exist" error.

    An absent bucket is not a failure for a *read*: it holds no objects, so
    the honest answers are ``[]`` and ``0``. Reported live as an HTTP 500 from
    the read-only retention preview, and — worse — as an abort partway through
    an execute-mode purge, after the Mongo deletes had already run.

    Narrow on purpose: AccessDenied, network failures and everything else
    still raise, because "nothing there" and "we could not look" must not
    render identically.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    if not isinstance(exc, ClientError):
        return False
    code = (exc.response or {}).get("Error", {}).get("Code")
    return code in ("NoSuchBucket", "404")


def _require_prefix(prefix: str) -> None:
    """Refuse bucket-wiping prefixes for delete_prefix implementations."""
    if not prefix or not prefix.strip("/").strip():
        raise ValueError("delete_prefix requires a non-empty prefix")


class S3StorageProvider(StorageProvider):
    """S3/MinIO compatible storage provider with connection pooling."""

    def __init__(self):
        import aioboto3
        from botocore.client import Config

        # Single session reused across all requests — aioboto3 manages the pool internally
        self._session = aioboto3.Session()
        self._endpoint = f"{'https' if settings.MINIO_USE_SSL else 'http'}://{settings.MINIO_ENDPOINT}"
        self._config = Config(
            signature_version="s3v4",
            max_pool_connections=settings.S3_MAX_POOL_CONNECTIONS,
        )

    def get_client_context(self) -> Any:
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
        try:
            async with self.get_client_context() as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        objects.append(obj)
        except Exception as exc:  # noqa: BLE001 — re-raised unless missing bucket
            if not _is_missing_bucket(exc):
                raise
            logger.debug("list_objects: bucket %s does not exist", bucket)
            return []
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

    async def delete_object(self, key: str, bucket: str | None = None) -> None:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        async with self.get_client_context() as s3:
            # S3 DeleteObject is idempotent — deleting a missing key succeeds.
            await s3.delete_object(Bucket=bucket, Key=key)

    async def delete_prefix(self, prefix: str, bucket: str | None = None) -> int:
        _require_prefix(prefix)
        bucket = bucket or settings.MINIO_BUCKET_NAME
        deleted = 0
        try:
            async with self.get_client_context() as s3:
                paginator = s3.get_paginator("list_objects_v2")
                keys: list[str] = []
                async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    keys.extend(obj["Key"] for obj in page.get("Contents", []))
                # DeleteObjects caps at 1000 keys per request.
                for i in range(0, len(keys), 1000):
                    batch = keys[i:i + 1000]
                    await s3.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                    )
                    deleted += len(batch)
        except Exception as exc:  # noqa: BLE001 — re-raised unless missing bucket
            if not _is_missing_bucket(exc):
                raise
            logger.debug("delete_prefix: bucket %s does not exist", bucket)
            return 0
        return deleted


class LocalStorageProvider(StorageProvider):
    """Local file system storage provider."""

    def __init__(self):
        self.base_path = Path(settings.LOCAL_STORAGE_PATH).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_bucket_path(self, bucket: str | None = None) -> Path:
        bucket = bucket or settings.MINIO_BUCKET_NAME
        bucket_path = (self.base_path / bucket).resolve()
        try:
            bucket_path.relative_to(self.base_path)
        except ValueError as exc:
            raise ValueError("Invalid storage bucket path") from exc
        return bucket_path

    def _get_full_path(self, key: str, bucket: str | None = None) -> Path:
        bucket_path = self._get_bucket_path(bucket)
        full_path = (bucket_path / key).resolve()
        try:
            full_path.relative_to(bucket_path)
        except ValueError as exc:
            raise ValueError("Invalid storage object path") from exc
        return cast(Path, full_path)

    async def list_objects(self, prefix: str, bucket: str | None = None) -> list[dict]:
        bucket_dir = self._get_bucket_path(bucket)
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

    async def delete_object(self, key: str, bucket: str | None = None) -> None:
        # _get_full_path runs the traversal guard — deleting with an
        # unvalidated key would be worse than reading with one.
        full_path = self._get_full_path(key, bucket)

        def _delete():
            if full_path.is_file():
                full_path.unlink()

        await asyncio.to_thread(_delete)

    async def delete_prefix(self, prefix: str, bucket: str | None = None) -> int:
        _require_prefix(prefix)
        # list_objects yields keys RELATIVE to the bucket dir (so a
        # traversal-shaped prefix simply matches nothing); each key is
        # still funneled through the _get_full_path guard before unlink.
        objects = await self.list_objects(prefix, bucket)
        deleted = 0
        for obj in objects:
            full_path = self._get_full_path(obj["Key"], bucket)

            def _delete(p=full_path) -> bool:
                if p.is_file():
                    p.unlink()
                    return True
                return False

            if await asyncio.to_thread(_delete):
                deleted += 1
        return deleted


@lru_cache(maxsize=1)
def get_storage_provider() -> StorageProvider:
    """Return the configured storage provider (singleton).

    The provider is created once and reused for the lifetime of the process,
    avoiding repeated S3 session creation on every call.
    """
    backend_type = settings.STORAGE_BACKEND.lower()
    if backend_type in ("minio", "s3"):
        return S3StorageProvider()
    elif backend_type == "local":
        return LocalStorageProvider()
    else:
        raise ValueError(f"Unknown STORAGE_BACKEND: {backend_type}")
