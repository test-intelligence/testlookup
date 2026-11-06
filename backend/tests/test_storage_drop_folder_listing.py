"""``list_common_prefixes`` and ``head_object`` on every storage provider (W34 STRAT-13).

The drop-folder watcher lists the build "folders" under ``<project>/runs/``
every minute and HEADs one sentinel per new folder, so both calls must be
bounded: the local provider scans ONE directory (a deep tree below it is never
walked) and the S3 provider sends a paged ``Delimiter="/"`` listing and keeps
only its ``CommonPrefixes``. The S3 half runs against a real socket (aiohttp)
so botocore's own request building and response parsing are exercised; the
opt-in versitygw module (``tests/integration/test_s3_drop_folder_versitygw.py``)
repeats it against a real S3 server in CI.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web

from app.core.config import settings
from app.db import storage
from app.db.storage import (
    DynamicStorageProvider,
    LocalStorageProvider,
    ObjectHead,
    S3StorageProvider,
    StorageProvider,
)

BUCKET = "drop-bucket"
STAMP = datetime(2026, 11, 5, 9, 30, 15, tzinfo=timezone.utc)


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LocalStorageProvider:
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    return LocalStorageProvider(default_bucket=BUCKET)


def _write(root: Path, key: str, body: bytes = b"{}") -> Path:
    path = root / BUCKET / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


# ── LocalStorageProvider ─────────────────────────────────────────────────────


async def test_local_lists_exactly_the_immediate_sub_folders(local: LocalStorageProvider, tmp_path: Path) -> None:
    _write(tmp_path, "proj/runs/10/allure/a-result.json")
    _write(tmp_path, "proj/runs/2/upload_complete.json")
    _write(tmp_path, "proj/runs/3/deep/er/still/x.json")
    _write(tmp_path, "proj/runs/notes.txt")                  # an object AT the prefix: not a folder
    _write(tmp_path, "proj/other/9/upload_complete.json")    # a sibling of runs/: not listed
    _write(tmp_path, "other/runs/1/upload_complete.json")    # another project: not listed

    assert await local.list_common_prefixes("proj/runs/") == ["proj/runs/10/", "proj/runs/2/", "proj/runs/3/"]
    assert await local.list_common_prefixes("proj/") == ["proj/other/", "proj/runs/"]
    assert await local.list_common_prefixes("") == ["other/", "proj/"]


async def test_local_scans_one_directory_and_never_walks_the_tree(
    local: LocalStorageProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for build in range(5):
        _write(tmp_path, f"proj/runs/{build}/a/b/c/d/e/{build}.json")
    scanned: list[str] = []
    real_scandir = os.scandir

    def _scandir(path):  # type: ignore[no-untyped-def]
        scanned.append(Path(path).as_posix())
        return real_scandir(path)

    def _no_walk(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("list_common_prefixes walked the tree")

    monkeypatch.setattr(storage.os, "scandir", _scandir)
    monkeypatch.setattr(storage.os, "walk", _no_walk)
    listed = await local.list_common_prefixes("proj/runs/")
    assert listed == [f"proj/runs/{b}/" for b in range(5)]
    assert scanned == [(tmp_path / BUCKET / "proj" / "runs").resolve().as_posix()]


async def test_local_missing_directory_or_bucket_lists_nothing(local: LocalStorageProvider, tmp_path: Path) -> None:
    assert await local.list_common_prefixes("nobody/runs/") == []
    assert await local.list_common_prefixes("x/runs/", bucket="absent-bucket") == []
    _write(tmp_path, "proj/runs")                             # a FILE where the folder would be
    assert await local.list_common_prefixes("proj/runs/") == []


async def test_local_listing_refuses_a_partial_name_and_a_traversal(local: LocalStorageProvider) -> None:
    with pytest.raises(ValueError, match="ending in '/'"):
        await local.list_common_prefixes("proj/ru")
    with pytest.raises(ValueError, match="Invalid storage object path"):
        await local.list_common_prefixes("../../outside/")


async def test_local_head_reports_size_etag_and_an_aware_mtime(local: LocalStorageProvider, tmp_path: Path) -> None:
    path = _write(tmp_path, "proj/runs/7/upload_complete.json", b'{"build_number": "7"}')
    os.utime(path, (STAMP.timestamp(), STAMP.timestamp()))
    head = await local.head_object("proj/runs/7/upload_complete.json")
    assert isinstance(head, ObjectHead)
    assert head.key == "proj/runs/7/upload_complete.json"
    assert head.size == len(b'{"build_number": "7"}')
    assert head.last_modified == STAMP and head.last_modified.tzinfo is not None
    assert head.etag and head.etag == (await local.head_object("proj/runs/7/upload_complete.json")).etag

    path.write_bytes(b'{"build_number": "7", "x": 1}')         # rewritten: a new change token
    os.utime(path, (STAMP.timestamp() + 60, STAMP.timestamp() + 60))
    assert (await local.head_object("proj/runs/7/upload_complete.json")).etag != head.etag


async def test_local_head_of_a_missing_key_or_a_folder_is_none(local: LocalStorageProvider, tmp_path: Path) -> None:
    _write(tmp_path, "proj/runs/7/allure/a-result.json")
    assert await local.head_object("proj/runs/8/upload_complete.json") is None
    assert await local.head_object("proj/runs/7/allure") is None          # a directory is not an object
    assert await local.head_object("proj/runs/7/allure/a-result.json/x") is None   # below a FILE
    with pytest.raises(ValueError, match="Invalid storage object path"):
        await local.head_object("../../outside/upload_complete.json")


# ── S3StorageProvider over a real socket ─────────────────────────────────────

_NS = "http://s3.amazonaws.com/doc/2006-03-01/"
_PAGE = 2  # entries (folders + objects) per ListObjectsV2 page: forces paging


class _DelimiterS3:
    """A small S3 endpoint that implements ListObjectsV2 with ``delimiter`` and
    continuation tokens, and HEAD. It records every listing's query."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.list_queries: list[dict[str, str]] = []
        self.heads: list[str] = []
        self._runner: web.AppRunner | None = None
        self.endpoint = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{bucket}", self._bucket)
        app.router.add_route("*", "/{bucket}/{key:.*}", self._object)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.endpoint = f"127.0.0.1:{sock.getsockname()[1]}"
        await web.SockSite(self._runner, sock).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @staticmethod
    def _xml(body: str, status: int = 200) -> web.Response:
        return web.Response(status=status, body=('<?xml version="1.0" encoding="UTF-8"?>' + body).encode(),
                            content_type="application/xml")

    async def _bucket(self, request: web.Request) -> web.Response:
        if request.match_info["bucket"] != BUCKET:
            return self._xml("<Error><Code>NoSuchBucket</Code><Message>x</Message></Error>", status=404)
        query = dict(request.query)
        self.list_queries.append(query)
        prefix, delimiter = query.get("prefix", ""), query.get("delimiter", "")
        entries: dict[str, str] = {}
        for key in sorted(k for k in self.objects if k.startswith(prefix)):
            rest = key[len(prefix):]
            if delimiter and delimiter in rest:
                entries[prefix + rest.split(delimiter, 1)[0] + delimiter] = "folder"
            else:
                entries[key] = "object"
        ordered = sorted(entries)
        start = int(query.get("continuation-token") or 0)
        page = ordered[start:start + _PAGE]
        truncated = start + _PAGE < len(ordered)
        body = "".join(
            f"<CommonPrefixes><Prefix>{name}</Prefix></CommonPrefixes>" if entries[name] == "folder" else
            f"<Contents><Key>{name}</Key><LastModified>2026-11-05T09:30:15.000Z</LastModified>"
            f"<ETag>&quot;e&quot;</ETag><Size>{len(self.objects[name])}</Size></Contents>"
            for name in page
        )
        token = f"<NextContinuationToken>{start + _PAGE}</NextContinuationToken>" if truncated else ""
        return self._xml(
            f'<ListBucketResult xmlns="{_NS}"><Name>{BUCKET}</Name><Prefix>{prefix}</Prefix>'
            f"<KeyCount>{len(page)}</KeyCount><MaxKeys>1000</MaxKeys>"
            f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>{token}{body}</ListBucketResult>"
        )

    async def _object(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        if request.method != "HEAD":
            return web.Response(status=405)
        self.heads.append(key)
        if key.startswith("forbidden/"):
            return web.Response(status=403)
        if key not in self.objects:
            return web.Response(status=404)
        return web.Response(status=200, headers={
            "ETag": '"0123abcd"', "Content-Length": str(len(self.objects[key])),
            "Last-Modified": "Thu, 05 Nov 2026 09:30:15 GMT", "Content-Type": "application/json",
        })


@pytest.fixture
async def s3(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "stub-access")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "stub-secret")
    stub = _DelimiterS3()
    await stub.start()
    try:
        yield stub, S3StorageProvider(endpoint=stub.endpoint, use_ssl=False, default_bucket=BUCKET)
    finally:
        await stub.stop()


async def test_s3_lists_folders_with_a_paged_delimiter_listing(s3) -> None:
    stub, provider = s3
    for build in ("1", "10", "2", "3", "4"):
        stub.objects[f"proj/runs/{build}/allure/a-result.json"] = b"{}"
        stub.objects[f"proj/runs/{build}/deep/x/y/z.json"] = b"{}"
    stub.objects["proj/runs/notes.txt"] = b"x"                  # an object AT the prefix: not a folder
    stub.objects["other/runs/9/upload_complete.json"] = b"{}"

    assert await provider.list_common_prefixes("proj/runs/") == [
        "proj/runs/1/", "proj/runs/10/", "proj/runs/2/", "proj/runs/3/", "proj/runs/4/",
    ]
    assert len(stub.list_queries) == 3                            # 6 entries, 2 per page
    assert all(q["delimiter"] == "/" and q["prefix"] == "proj/runs/" for q in stub.list_queries)
    assert [q.get("continuation-token") for q in stub.list_queries] == [None, "2", "4"]


async def test_s3_listing_of_a_missing_bucket_is_empty_and_a_partial_name_is_refused(s3) -> None:
    _stub, provider = s3
    assert await provider.list_common_prefixes("proj/runs/", bucket="absent") == []
    with pytest.raises(ValueError, match="ending in '/'"):
        await provider.list_common_prefixes("proj/ru")


async def test_s3_head_reports_the_object_and_none_when_absent(s3) -> None:
    stub, provider = s3
    stub.objects["proj/runs/7/upload_complete.json"] = b'{"a": 1}'
    head = await provider.head_object("proj/runs/7/upload_complete.json")
    assert head == ObjectHead(key="proj/runs/7/upload_complete.json", etag="0123abcd", size=8, last_modified=STAMP)
    assert head.last_modified.tzinfo is not None
    assert await provider.head_object("proj/runs/8/upload_complete.json") is None
    assert stub.heads == ["proj/runs/7/upload_complete.json", "proj/runs/8/upload_complete.json"]


async def test_s3_head_raises_when_it_could_not_look(s3) -> None:
    from botocore.exceptions import ClientError

    _stub, provider = s3
    with pytest.raises(ClientError):
        await provider.head_object("forbidden/runs/1/upload_complete.json")


# ── The dynamic facade and the base class ────────────────────────────────────


async def test_dynamic_provider_delegates_both_calls(
    local: LocalStorageProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import storage_config_service

    async def _config(*_args, **_kwargs) -> dict:
        return {"storage_backend": "local", "minio_endpoint": "x", "minio_use_ssl": False,
                "minio_bucket_name": BUCKET}

    monkeypatch.setattr(storage_config_service, "get_effective_storage_config", _config)
    monkeypatch.setattr(storage, "_get_concrete_storage_provider", lambda *_a: local)
    path = _write(tmp_path, "proj/runs/5/upload_complete.json")
    os.utime(path, (STAMP.timestamp(), STAMP.timestamp()))
    dynamic = DynamicStorageProvider()
    assert await dynamic.list_common_prefixes("proj/runs/") == ["proj/runs/5/"]
    head = await dynamic.head_object("proj/runs/5/upload_complete.json")
    assert head is not None and head.last_modified == STAMP
    assert await dynamic.head_object("proj/runs/6/upload_complete.json") is None


class _Double(StorageProvider):
    """A provider double written before W34: only the abstract methods."""

    async def list_objects(self, prefix, bucket=None):
        return []

    async def get_object_content(self, key, bucket=None):
        return b""

    async def stream_object(self, key, bucket=None):
        yield b""

    async def put_object(self, key, content, content_type="application/json", bucket=None):
        return None

    async def get_presigned_url(self, key, expiry=3600, bucket=None):
        return ""

    async def delete_object(self, key, bucket=None):
        return None

    async def delete_prefix(self, prefix, bucket=None):
        return 0


async def test_a_provider_double_without_the_calls_still_instantiates() -> None:
    double = _Double()
    with pytest.raises(NotImplementedError, match="cannot list sub-folders"):
        await double.list_common_prefixes("p/runs/")
    with pytest.raises(NotImplementedError, match="cannot head an object"):
        await double.head_object("p/runs/1/upload_complete.json")
