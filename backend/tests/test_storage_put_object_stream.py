"""W22b ACT-03 E-4 — ``StorageProvider.put_object_stream``.

A retention archive may be hundreds of MiB, so it is never one ``bytes``
object: S3 uploads it with a multipart ``upload_fileobj`` straight from the
spooled file, the local provider copies it in 1 MiB chunks. The ABC method is
NOT abstract -- its default reads the file and calls ``put_object`` -- so every
existing provider double that implements only ``put_object`` keeps working.
"""
from __future__ import annotations

import inspect
import io
import tempfile

import pytest

from app.db import storage as storage_module
from app.db.storage import LocalStorageProvider, S3StorageProvider, StorageProvider


class _Minimal(StorageProvider):
    """Implements the abstract surface only (an existing double's shape)."""

    def __init__(self):
        self.put = []

    async def list_objects(self, prefix, bucket=None):
        return []

    async def get_object_content(self, key, bucket=None):
        return b""

    async def stream_object(self, key, bucket=None):
        yield b""

    async def put_object(self, key, content, content_type="application/json", bucket=None):
        self.put.append((key, content, content_type, bucket))

    async def get_presigned_url(self, key, expiry=3600, bucket=None):
        return ""

    async def delete_object(self, key, bucket=None):
        return None

    async def delete_prefix(self, prefix, bucket=None):
        return 0


def test_the_method_is_not_abstract():
    assert "put_object_stream" not in StorageProvider.__abstractmethods__
    assert "put_object" in StorageProvider.__abstractmethods__


@pytest.mark.asyncio
async def test_the_default_reads_from_the_start_and_calls_put_object():
    provider = _Minimal()
    fileobj = io.BytesIO(b"archive-bytes")
    fileobj.seek(5)   # the default must not depend on where the caller left it
    await provider.put_object_stream("exports/a.zip", fileobj, size=13, bucket="compliance-packs")
    assert provider.put == [("exports/a.zip", b"archive-bytes", "application/zip", "compliance-packs")]


class _S3Client:
    def __init__(self):
        self.uploads = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        self.uploads.append((fileobj, fileobj.tell(), bucket, key, ExtraArgs))

    async def put_object(self, **_kw):
        raise AssertionError("a stream must not be buffered into put_object")


@pytest.mark.asyncio
async def test_s3_uploads_the_file_object_once_with_its_content_type(monkeypatch):
    client = _S3Client()
    provider = S3StorageProvider.__new__(S3StorageProvider)
    provider._default_bucket = "artifacts"
    monkeypatch.setattr(provider, "get_client_context", lambda: client, raising=False)
    spool = tempfile.SpooledTemporaryFile(max_size=16)
    spool.write(b"x" * 100)

    await provider.put_object_stream("exports/a.zip", spool, size=100, bucket="compliance-packs")

    assert len(client.uploads) == 1
    fileobj, position, bucket, key, extra = client.uploads[0]
    assert fileobj is spool and position == 0, "the SAME file object, rewound"
    assert (bucket, key, extra) == ("compliance-packs", "exports/a.zip", {"ContentType": "application/zip"})


@pytest.mark.asyncio
async def test_local_copies_in_chunks_under_the_traversal_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    provider = LocalStorageProvider(default_bucket="artifacts")
    chunks = []
    real_copy = storage_module.shutil.copyfileobj

    def _copy(src, dst, length=0):
        chunks.append(length)
        return real_copy(src, dst, length)

    monkeypatch.setattr(storage_module.shutil, "copyfileobj", _copy)
    payload = bytes(range(256)) * 9000            # > 2 MiB
    await provider.put_object_stream("exports/2026/a.zip", io.BytesIO(payload), size=len(payload),
                                     bucket="compliance-packs")

    assert (tmp_path / "compliance-packs" / "exports" / "2026" / "a.zip").read_bytes() == payload
    assert chunks == [1 << 20]
    assert [c async for c in provider.stream_object("exports/2026/a.zip", bucket="compliance-packs")]
    with pytest.raises(ValueError):
        await provider.put_object_stream("../escape.zip", io.BytesIO(b"x"), size=1, bucket="compliance-packs")


@pytest.mark.asyncio
async def test_the_dynamic_provider_delegates(monkeypatch):
    inner = _Minimal()
    proxy = storage_module.DynamicStorageProvider()

    async def _provider():
        return inner

    monkeypatch.setattr(proxy, "_provider", _provider)
    await proxy.put_object_stream("k", io.BytesIO(b"abc"), size=3, content_type="text/plain", bucket="b")
    assert inner.put == [("k", b"abc", "text/plain", "b")]


def test_no_provider_signs_or_hands_out_a_url_for_the_stream():
    for cls in (S3StorageProvider, LocalStorageProvider):
        source = inspect.getsource(cls.put_object_stream)
        assert "presigned" not in source and "generate_presigned_url" not in source
