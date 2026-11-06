"""The drop-folder watcher's two S3 calls against a REAL S3 server (versitygw in CI; W34 STRAT-13).

``S3StorageProvider.list_common_prefixes`` (a paged ``Delimiter="/"`` listing)
and ``head_object`` drive the polling watcher on the bundled object store, so
they are exercised here against the server the product ships with: paging past
one page, only the folders (never the objects below them), a missing bucket,
and HEAD's ETag / size / aware ``LastModified`` and its "absent" answer.

Opt-in like ``test_s3_storage_versitygw.py``: skipped unless
``TESTLOOKUP_S3_TEST_ENDPOINT`` / ``_ACCESS_KEY`` / ``_SECRET_KEY`` are set
(CI's backend-test job runs versitygw as a service container and sets them).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest

from app.core.config import settings
from app.db.storage import S3StorageProvider

pytestmark = [pytest.mark.integration]

ENDPOINT = os.environ.get("TESTLOOKUP_S3_TEST_ENDPOINT", "").strip()
ACCESS_KEY = os.environ.get("TESTLOOKUP_S3_TEST_ACCESS_KEY", "").strip()
SECRET_KEY = os.environ.get("TESTLOOKUP_S3_TEST_SECRET_KEY", "").strip()

requires_server = pytest.mark.skipif(
    not (ENDPOINT and ACCESS_KEY and SECRET_KEY),
    reason="TESTLOOKUP_S3_TEST_ENDPOINT/_ACCESS_KEY/_SECRET_KEY not set (no S3 server)",
)


def _host_port_and_ssl() -> tuple[str, bool]:
    if "://" not in ENDPOINT:
        return ENDPOINT, False
    parts = urlsplit(ENDPOINT)
    return parts.netloc, parts.scheme == "https"


@pytest.fixture
async def provider(monkeypatch: pytest.MonkeyPatch):
    """A provider on a fresh bucket, emptied and removed afterwards."""
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", ACCESS_KEY)
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", SECRET_KEY)
    host, ssl = _host_port_and_ssl()
    bucket = f"drop-{uuid.uuid4().hex[:20]}"
    s3_provider = S3StorageProvider(endpoint=host, use_ssl=ssl, default_bucket=bucket)
    async with s3_provider.get_client_context() as s3:
        await s3.create_bucket(Bucket=bucket)
    try:
        yield s3_provider
    finally:
        async with s3_provider.get_client_context() as s3:
            listed = await s3.list_objects_v2(Bucket=bucket)
            for obj in listed.get("Contents", []):
                await s3.delete_object(Bucket=bucket, Key=obj["Key"])
            await s3.delete_bucket(Bucket=bucket)


@requires_server
async def test_folders_are_listed_past_one_page_and_nothing_below_them(provider) -> None:
    builds = [f"{n}" for n in range(1, 8)]
    for build in builds:
        await provider.put_object(f"proj/runs/{build}/allure/{build}-result.json", b"{}")
        await provider.put_object(f"proj/runs/{build}/deep/a/b/c.json", b"{}")
    await provider.put_object("proj/runs/notes.txt", b"x")             # an object AT the prefix
    await provider.put_object("other/runs/9/upload_complete.json", b"{}")

    expected = sorted(f"proj/runs/{b}/" for b in builds)
    assert await provider.list_common_prefixes("proj/runs/") == expected

    # The same listing, three folders a page, to prove the paging the provider relies on.
    async with provider.get_client_context() as s3:
        paginator = s3.get_paginator("list_objects_v2")
        pages = [page async for page in paginator.paginate(
            Bucket=provider._default_bucket, Prefix="proj/runs/", Delimiter="/",
            PaginationConfig={"PageSize": 3},
        )]
    assert len(pages) >= 3
    folders = [p["Prefix"] for page in pages for p in page.get("CommonPrefixes", [])]
    contents = [o["Key"] for page in pages for o in page.get("Contents", [])]
    assert folders == expected and contents == ["proj/runs/notes.txt"]


@requires_server
async def test_a_missing_prefix_or_bucket_lists_nothing(provider) -> None:
    assert await provider.list_common_prefixes("nobody/runs/") == []
    assert await provider.list_common_prefixes("x/runs/", bucket=f"absent-{uuid.uuid4().hex[:12]}") == []


@requires_server
async def test_head_reports_etag_size_and_an_aware_last_modified(provider) -> None:
    body = b'{"build_number": "7"}'
    key = "proj/runs/7/upload_complete.json"
    before = datetime.now(timezone.utc) - timedelta(minutes=5)
    await provider.put_object(key, body)
    head = await provider.head_object(key)
    assert head is not None and head.key == key and head.size == len(body)
    async with provider.get_client_context() as s3:
        listed = await s3.list_objects_v2(Bucket=provider._default_bucket, Prefix=key)
    (obj,) = listed["Contents"]
    assert head.etag and head.etag == obj["ETag"].strip('"')            # the server's ETag, unquoted
    assert head.last_modified.tzinfo is not None
    assert before <= head.last_modified <= datetime.now(timezone.utc) + timedelta(minutes=5)
    assert await provider.head_object("proj/runs/8/upload_complete.json") is None
