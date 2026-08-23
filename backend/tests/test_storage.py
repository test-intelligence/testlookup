import os
import re
import shutil
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from aiohttp import web

from app.core.config import settings
from app.db.storage import (
    _STREAM_CHUNK_SIZE,
    DynamicStorageProvider,
    LocalStorageProvider,
    S3StorageProvider,
)

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


# ---------------------------------------------------------------------------
# A real S3 endpoint, served over a real socket.
# ---------------------------------------------------------------------------

_S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


class _S3Stub:
    """A tiny but *real* S3 endpoint, spoken over a real socket by aiohttp.

    Deliberately NOT a ``unittest.mock``. ``S3StorageProvider.stream_object``
    shipped broken for its entire life because the only test that exercised
    ``stream_object`` ran under ``LocalStorageProvider``; a mocked S3 client
    would have been exactly as blind, because the defect lives in the objects
    aiobotocore builds *around a real* ``aiohttp.ClientResponse`` -- the body
    proxy is discarded by ``async with body as stream``, and only a real
    response object has the ``read(self)`` signature that then raises. Driving
    real HTTP is what gives this test the ability to fail.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.endpoint = ""
        self._runner: web.AppRunner | None = None

    async def start(self) -> str:
        app = web.Application()
        app.router.add_route("*", "/{bucket}", self._handle_bucket)
        app.router.add_route("*", "/{bucket}/{key:.*}", self._handle_object)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        # A literal IP endpoint keeps botocore on path-style addressing, so the
        # bucket arrives as the first path segment rather than a DNS label.
        self.endpoint = "127.0.0.1:{}".format(sock.getsockname()[1])
        await web.SockSite(self._runner, sock).start()
        return self.endpoint

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @staticmethod
    def _xml(body: str, status: int = 200) -> web.Response:
        payload = '<?xml version="1.0" encoding="UTF-8"?>' + body
        return web.Response(status=status, body=payload.encode(), content_type="application/xml")

    def _error(self, code: str, status: int) -> web.Response:
        return self._xml(
            "<Error><Code>{}</Code><Message>{}</Message></Error>".format(code, code), status=status
        )

    async def _handle_bucket(self, request: web.Request) -> web.Response:
        bucket = request.match_info["bucket"]
        if request.method == "PUT":  # CreateBucket
            return web.Response(status=200)
        if request.method == "POST" and "delete" in request.query:  # DeleteObjects
            keys = re.findall("<Key>(.*?)</Key>", await request.text())
            for key in keys:
                self.objects.pop((bucket, key), None)
            deleted = "".join("<Deleted><Key>{}</Key></Deleted>".format(k) for k in keys)
            return self._xml('<DeleteResult xmlns="{}">{}</DeleteResult>'.format(_S3_NS, deleted))
        if request.method == "GET":  # ListObjectsV2
            prefix = request.query.get("prefix", "")
            rows = sorted(
                (k, v) for (b, k), v in self.objects.items() if b == bucket and k.startswith(prefix)
            )
            contents = "".join(
                "<Contents><Key>{}</Key>"
                "<LastModified>2026-08-23T00:00:00.000Z</LastModified>"
                "<ETag>&quot;{}&quot;</ETag><Size>{}</Size>"
                "<StorageClass>STANDARD</StorageClass></Contents>".format(k, len(v), len(v))
                for k, v in rows
            )
            return self._xml(
                '<ListBucketResult xmlns="{}"><Name>{}</Name><Prefix>{}</Prefix>'
                "<KeyCount>{}</KeyCount><MaxKeys>1000</MaxKeys>"
                "<IsTruncated>false</IsTruncated>{}</ListBucketResult>".format(
                    _S3_NS, bucket, prefix, len(rows), contents
                )
            )
        return self._error("MethodNotAllowed", 405)

    async def _handle_object(self, request: web.Request) -> web.Response:
        bucket, key = request.match_info["bucket"], request.match_info["key"]
        if request.method == "PUT":
            self.objects[(bucket, key)] = await request.read()
            return web.Response(status=200, headers={"ETag": '"stub"'})
        if request.method == "DELETE":
            self.objects.pop((bucket, key), None)
            return web.Response(status=204)
        if request.method in ("GET", "HEAD"):
            if (bucket, key) not in self.objects:
                return self._error("NoSuchKey", 404)
            body = self.objects[(bucket, key)]
            return web.Response(
                status=200,
                body=b"" if request.method == "HEAD" else body,
                headers={"ETag": '"stub"'},
                content_type="application/octet-stream",
            )
        return self._error("MethodNotAllowed", 405)


@pytest.fixture
async def s3_stub(monkeypatch):
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "stub-access-key")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "stub-secret-key")
    stub = _S3Stub()
    await stub.start()
    try:
        yield stub
    finally:
        await stub.stop()


@pytest.fixture
def s3_provider(s3_stub):
    return S3StorageProvider(
        endpoint=s3_stub.endpoint, use_ssl=False, default_bucket="stub-bucket"
    )


# ---------------------------------------------------------------------------
# The regression: stream_object on the S3 provider specifically.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_stream_object_streams_a_multi_chunk_object(s3_provider):
    """``S3StorageProvider.stream_object`` must actually yield the object.

    Guards the shape that made it raise
    ``TypeError: ClientResponse.read() takes 1 positional argument but 2 were
    given`` on every call: ``async with response["Body"] as stream`` rebinds
    ``stream`` from aiobotocore's ``StreamingBody`` proxy (which supports
    ``read(amt)`` and ``iter_chunks``) to the bare ``aiohttp.ClientResponse``
    (which supports neither).
    """
    bucket, key = "stub-bucket", "streams/multi-chunk.bin"
    payload = bytes(range(256)) * 1300  # 332_800 bytes, > 5 chunks

    await s3_provider.put_object(key=key, content=payload, bucket=bucket)

    chunks = [chunk async for chunk in s3_provider.stream_object(key=key, bucket=bucket)]

    assert b"".join(chunks) == payload
    # More than one chunk, and none larger than the requested size -- that is
    # the whole point of streaming, and it is what a whole-body read would
    # quietly stop doing. Sizes are *not* asserted exactly: the reader returns
    # whatever is buffered, so a short leading chunk is normal.
    assert len(chunks) > 1
    assert max(len(chunk) for chunk in chunks) <= _STREAM_CHUNK_SIZE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "size",
    [0, 1, _STREAM_CHUNK_SIZE - 1, _STREAM_CHUNK_SIZE, _STREAM_CHUNK_SIZE + 1],
    ids=["empty", "single-byte", "under-chunk", "exact-chunk", "over-chunk"],
)
async def test_s3_stream_object_handles_chunk_boundaries(s3_provider, size):
    """Sizes either side of the chunk boundary must round-trip byte-exact."""
    bucket, key = "stub-bucket", "streams/boundary-{}.bin".format(size)
    payload = os.urandom(size)

    await s3_provider.put_object(key=key, content=payload, bucket=bucket)

    chunks = [chunk async for chunk in s3_provider.stream_object(key=key, bucket=bucket)]
    assert b"".join(chunks) == payload


@pytest.mark.asyncio
async def test_s3_stream_object_raises_for_a_missing_key(s3_provider):
    """A missing key must raise, not yield zero chunks like an empty object."""
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError) as excinfo:
        _ = [c async for c in s3_provider.stream_object(key="streams/absent.bin")]

    assert excinfo.value.response["Error"]["Code"] in ("NoSuchKey", "404")


# ---------------------------------------------------------------------------
# One contract, both providers.
# ---------------------------------------------------------------------------


@pytest.fixture(params=["local", "s3"])
def provider(request, temp_storage_path, s3_stub):
    """Every ``StorageProvider`` implementation, behind one fixture.

    The S3 ``stream_object`` defect survived because ``test_storage.py``
    only ever built a ``LocalStorageProvider``: the suite was green while
    half of the abstract contract was unexecuted. Any new abstract method
    is now driven against both backends by construction.
    """
    if request.param == "local":
        return LocalStorageProvider(default_bucket="stub-bucket")
    return S3StorageProvider(endpoint=s3_stub.endpoint, use_ssl=False, default_bucket="stub-bucket")


@pytest.mark.asyncio
async def test_storage_provider_contract(provider):
    """The StorageProvider ABC, exercised identically on both backends."""
    bucket = "stub-bucket"
    key = "contract/nested/object.bin"
    payload = os.urandom(_STREAM_CHUNK_SIZE * 2 + 11)

    await provider.put_object(key=key, content=payload, bucket=bucket)

    assert await provider.get_object_content(key=key, bucket=bucket) == payload

    streamed = [chunk async for chunk in provider.stream_object(key=key, bucket=bucket)]
    assert b"".join(streamed) == payload
    assert max(len(chunk) for chunk in streamed) <= _STREAM_CHUNK_SIZE

    objects = await provider.list_objects(prefix="contract/", bucket=bucket)
    assert [obj["Key"] for obj in objects] == [key]
    assert objects[0]["Size"] == len(payload)

    assert isinstance(await provider.get_presigned_url(key=key, bucket=bucket), str)

    await provider.delete_object(key=key, bucket=bucket)
    assert await provider.list_objects(prefix="contract/", bucket=bucket) == []


@pytest.mark.asyncio
async def test_storage_provider_delete_prefix_contract(provider):
    bucket = "stub-bucket"
    for name in ("purge/a.json", "purge/b.json", "keep/c.json"):
        await provider.put_object(key=name, content=b"{}", bucket=bucket)

    assert await provider.delete_prefix("purge/", bucket=bucket) == 2
    remaining = await provider.list_objects(prefix="", bucket=bucket)
    assert [obj["Key"] for obj in remaining] == ["keep/c.json"]

    # A bucket-wiping prefix is refused on every backend.
    with pytest.raises(ValueError):
        await provider.delete_prefix("", bucket=bucket)


@pytest.mark.asyncio
async def test_dynamic_provider_facade_streams_on_both_backends(provider, monkeypatch):
    """The facade re-yields chunks; that loop needs both backends under it too."""
    from app.db import storage
    from app.services import storage_config_service

    bucket, key = "stub-bucket", "facade/object.bin"
    payload = os.urandom(_STREAM_CHUNK_SIZE + 7)
    await provider.put_object(key=key, content=payload, bucket=bucket)

    async def resolve_config():
        # Values are placeholders: the factory below is stubbed to hand back the
        # parametrised provider, so what is under test is the facade's re-yield
        # loop, not the config lookup (covered by the last test in this file).
        return {
            "storage_backend": "ignored",
            "minio_endpoint": "ignored",
            "minio_use_ssl": False,
            "minio_bucket_name": bucket,
        }

    monkeypatch.setattr(storage_config_service, "get_effective_storage_config", resolve_config)
    monkeypatch.setattr(storage, "_get_concrete_storage_provider", lambda *a: provider)

    facade = DynamicStorageProvider()
    chunks = [chunk async for chunk in facade.stream_object(key=key, bucket=bucket)]

    assert b"".join(chunks) == payload
    assert await facade.get_object_content(key=key, bucket=bucket) == payload


# ---------------------------------------------------------------------------
# Opt-in: the same streaming contract against a real MinIO.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.live
@pytest.mark.asyncio
async def test_s3_stream_object_against_live_minio(monkeypatch):
    """Same assertions, real MinIO. Opt-in via env vars; skipped otherwise.

    Set TESTLOOKUP_TEST_MINIO_ENDPOINT / _ACCESS_KEY / _SECRET_KEY, e.g.
    ``TESTLOOKUP_TEST_MINIO_ENDPOINT=127.0.0.1:9000``.
    """
    endpoint = os.environ.get("TESTLOOKUP_TEST_MINIO_ENDPOINT")
    access_key = os.environ.get("TESTLOOKUP_TEST_MINIO_ACCESS_KEY")
    secret_key = os.environ.get("TESTLOOKUP_TEST_MINIO_SECRET_KEY")
    if not (endpoint and access_key and secret_key):
        pytest.skip("TESTLOOKUP_TEST_MINIO_* not set; live MinIO test not requested")

    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", access_key)
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", secret_key)
    bucket = "testlookup-stream-object-regression"
    provider = S3StorageProvider(endpoint=endpoint, use_ssl=False, default_bucket=bucket)
    async with provider.get_client_context() as s3:
        try:
            await s3.create_bucket(Bucket=bucket)
        except Exception:  # noqa: BLE001 -- "already exists" is the normal case
            pass

    key = "streams/live-multi-chunk.bin"
    payload = os.urandom(_STREAM_CHUNK_SIZE * 3 + 123)
    try:
        await provider.put_object(key=key, content=payload, bucket=bucket)
        chunks = [chunk async for chunk in provider.stream_object(key=key, bucket=bucket)]
        assert b"".join(chunks) == payload
        assert len(chunks) > 1
        assert max(len(chunk) for chunk in chunks) <= _STREAM_CHUNK_SIZE
    finally:
        await provider.delete_object(key=key, bucket=bucket)


# ---------------------------------------------------------------------------
# Local-provider specifics.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_storage_writes_to_the_expected_path(temp_storage_path):
    provider = LocalStorageProvider()
    bucket, key, content = "test-bucket", "test-folder/test-file.txt", b"Hello Storage!"

    await provider.put_object(key=key, content=content, bucket=bucket)

    expected_path = Path(temp_storage_path) / bucket / key
    assert expected_path.exists()
    assert expected_path.read_bytes() == content
    # Use Path.as_uri() for cross-platform comparison
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
