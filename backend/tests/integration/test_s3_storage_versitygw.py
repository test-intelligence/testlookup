"""The S3 storage provider against a REAL S3 server (versitygw in CI).

The bundled object store moved from PGSTY Silo (a MinIO fork, AGPL-3.0) to
versitygw (Apache-2.0). Every S3 call the backend makes goes through
``app.db.storage.S3StorageProvider``; this module drives each of them, plus the
bucket setup script, against a live server so an incompatibility shows up here
and not on a customer's install.

Opt-in: skipped unless ``TESTLOOKUP_S3_TEST_ENDPOINT`` is set. CI's backend-test
job runs ``ghcr.io/versity/versitygw:v1.8.0`` as a service container and sets:

    TESTLOOKUP_S3_TEST_ENDPOINT     http://localhost:9000   (URL or host:port)
    TESTLOOKUP_S3_TEST_ACCESS_KEY   the server's root access key
    TESTLOOKUP_S3_TEST_SECRET_KEY   the server's root secret key
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
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

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "s3_setup.py"


def _load_setup_script():
    spec = importlib.util.spec_from_file_location("s3_setup_integration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _host_port_and_ssl() -> tuple[str, bool]:
    if "://" not in ENDPOINT:
        return ENDPOINT, False
    parts = urlsplit(ENDPOINT)
    return parts.netloc, parts.scheme == "https"


@pytest.fixture(scope="module")
def bucket() -> str:
    """A fresh bucket per run, created by the setup script (which also creates
    the application's own buckets, exactly as a deployment would)."""
    name = f"it-{uuid.uuid4().hex[:20]}"
    host, ssl = _host_port_and_ssl()
    env = {
        "MINIO_ENDPOINT": host, "MINIO_USE_SSL": "true" if ssl else "false",
        "MINIO_ACCESS_KEY": ACCESS_KEY, "MINIO_SECRET_KEY": SECRET_KEY,
    }
    setup = _load_setup_script()
    assert setup.run(["--bucket", name, "--wait-seconds", "60"], env=env) == 0
    # Idempotent: the second run must succeed and change nothing.
    assert setup.run(["--bucket", name, "--wait-seconds", "60"], env=env) == 0
    return name


@pytest.fixture
def provider(monkeypatch, bucket) -> S3StorageProvider:
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", ACCESS_KEY)
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", SECRET_KEY)
    host, ssl = _host_port_and_ssl()
    return S3StorageProvider(endpoint=host, use_ssl=ssl, default_bucket=bucket)


@requires_server
async def test_setup_created_every_application_bucket(provider, bucket):
    setup = _load_setup_script()
    async with provider.get_client_context() as s3:
        names = {b["Name"] for b in (await s3.list_buckets())["Buckets"]}
    assert set(setup.required_buckets({})) | {bucket} <= names


@requires_server
async def test_put_get_stream_and_list_round_trip(provider):
    prefix = f"proj-{uuid.uuid4().hex[:8]}/runs/1/"
    payload = b'{"tests": [1, 2, 3]}'
    big = os.urandom(200 * 1024)  # several _STREAM_CHUNK_SIZE reads
    await provider.put_object(prefix + "results.json", payload)
    await provider.put_object(prefix + "attachments/trace.bin", big, content_type="application/octet-stream")

    assert await provider.get_object_content(prefix + "results.json") == payload
    streamed = b"".join([chunk async for chunk in provider.stream_object(prefix + "attachments/trace.bin")])
    assert streamed == big

    listed = {o["Key"]: o["Size"] for o in await provider.list_objects(prefix)}
    assert listed == {prefix + "results.json": len(payload), prefix + "attachments/trace.bin": len(big)}

    async with provider.get_client_context() as s3:
        head = await s3.head_object(Bucket=provider._default_bucket, Key=prefix + "results.json")
    assert head["ContentType"] == "application/json"


@requires_server
async def test_list_paginates_past_one_page(provider):
    prefix = f"many-{uuid.uuid4().hex[:8]}/"
    async with provider.get_client_context() as s3:
        for i in range(7):
            await s3.put_object(Bucket=provider._default_bucket, Key=f"{prefix}{i:02d}.json", Body=b"{}")
        paginator = s3.get_paginator("list_objects_v2")
        pages = [page async for page in paginator.paginate(
            Bucket=provider._default_bucket, Prefix=prefix, PaginationConfig={"PageSize": 3},
        )]
    assert len(pages) >= 3
    assert len(await provider.list_objects(prefix)) == 7


@requires_server
async def test_presigned_get_url_downloads_without_credentials(provider):
    key = f"presigned-{uuid.uuid4().hex[:8]}/report.pdf"
    body = b"%PDF-1.4 not really a pdf"
    await provider.put_object(key, body, content_type="application/pdf")
    url = await provider.get_presigned_url(key, expiry=300)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
    assert response.status_code == 200, response.text[:300]
    assert response.content == body


@requires_server
async def test_delete_object_is_idempotent(provider):
    key = f"del-{uuid.uuid4().hex[:8]}/one.json"
    await provider.put_object(key, b"{}")
    await provider.delete_object(key)
    await provider.delete_object(key)  # missing key: still no error
    assert await provider.list_objects(key) == []


@requires_server
async def test_delete_prefix_removes_exactly_the_prefix(provider):
    run = f"purge-{uuid.uuid4().hex[:8]}"
    keep = f"keep-{uuid.uuid4().hex[:8]}/x.json"
    for i in range(5):
        await provider.put_object(f"{run}/runs/{i}/r.json", b"{}")
    await provider.put_object(keep, b"{}")
    assert await provider.delete_prefix(f"{run}/") == 5
    assert await provider.list_objects(f"{run}/") == []
    assert [o["Key"] for o in await provider.list_objects(keep)] == [keep]
    with pytest.raises(ValueError):
        await provider.delete_prefix("/")


@requires_server
async def test_a_missing_bucket_reads_as_empty(provider):
    absent = f"absent-{uuid.uuid4().hex[:12]}"
    assert await provider.list_objects("", bucket=absent) == []
    assert await provider.delete_prefix("anything/", bucket=absent) == 0


@requires_server
async def test_the_health_probe_reports_the_server_up(monkeypatch):
    from app.routers import health
    from app.services import storage_config_service

    host, ssl = _host_port_and_ssl()

    async def _config(*, use_cache: bool = True):
        return {"minio_endpoint": host, "minio_use_ssl": ssl}

    monkeypatch.setattr(storage_config_service, "get_effective_storage_config", _config)
    assert await health._check_minio() == {"status": "ok"}
