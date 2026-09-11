"""Re-audit R15: the MinIO webhook handles the event MinIO really sends.

Three differences from the shape the N10 tests used, each of which lost every
real upload:

* **The top-level ``Key`` is ``<bucket>/<object>``.** The handler took its
  first segment as the project, so the bucket name became the project, and the
  task then read a bucket-relative key that does not exist: refused.
* **``Records[].s3.object.key`` is URL-encoded** (Go's ``url.QueryEscape``:
  ``/`` is ``%2F``, a space is ``+``). The object key is taken from the record
  and decoded -- and only then checked, so ``%2E%2E`` is a ``..`` segment.
* **Authentication.** MinIO cannot add a custom header; its webhook target's
  ``auth_token`` arrives as ``Authorization: Bearer <token>``. The endpoint
  only knew ``X-Webhook-Secret``, so every real notification was a 403.

The event below is MinIO's own layout (``eventVersion`` 2.0, ``minio:s3``,
``s3SchemaVersion`` 1.0), as a ``put`` notification delivers it.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from urllib.parse import quote_plus

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.deps import verify_webhook_secret
from app.routers.webhooks import minio_webhook

PROJECT = "3f1c2a9e-7d4b-4c61-9a0e-2b8f5d6c7e10"
BUCKET = "test-telemetry"
SECRET = "the-minio-auth-token"


def _escaped(object_key: str) -> str:
    """How MinIO writes Records[].s3.object.key (Go url.QueryEscape)."""
    return quote_plus(object_key, safe="")


def minio_event(object_key: str, bucket: str = BUCKET) -> dict:
    return {
        "EventName": "s3:ObjectCreated:Put",
        "Key": f"{bucket}/{object_key}",
        "Records": [{
            "eventVersion": "2.0",
            "eventSource": "minio:s3",
            "awsRegion": "",
            "eventTime": "2026-09-11T10:15:02.117Z",
            "eventName": "s3:ObjectCreated:Put",
            "userIdentity": {"principalId": "ci-uploader"},
            "requestParameters": {
                "principalId": "ci-uploader", "region": "", "sourceIPAddress": "10.42.0.17",
            },
            "responseElements": {
                "x-amz-id-2": "dd9025bab4ad464b049177c95eb6ebf374d3b3fd1af9251148b658df7ac2e3e8",
                "x-amz-request-id": "1812D5E3A2B9C8F4",
                "x-minio-deployment-id": "9f1a1c3e-6f24-4bd4-9c10-5a0d6e1c2b7a",
                "x-minio-origin-endpoint": "http://10.42.0.9:9000",
            },
            "s3": {
                "s3SchemaVersion": "1.0",
                "configurationId": "Config",
                "bucket": {
                    "name": bucket,
                    "ownerIdentity": {"principalId": "ci-uploader"},
                    "arn": f"arn:aws:s3:::{bucket}",
                },
                "object": {
                    "key": _escaped(object_key),
                    "size": 214,
                    "eTag": "5f363e0e58a95f06cbe9bbc662c5dfb6",
                    "contentType": "application/json",
                    "userMetadata": {"content-type": "application/json"},
                    "sequencer": "1812D5E3A2D6F0B1",
                },
            },
            "source": {"host": "10.42.0.17", "port": "", "userAgent": "MinIO (linux; amd64) minio-go/v7.0.70"},
        }],
    }


def _request(body: dict):
    async def _json():
        return body

    return SimpleNamespace(json=_json)


@pytest.fixture
def queued(monkeypatch):
    calls: list[dict] = []

    def _delay(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="task-r15")

    monkeypatch.setattr("app.worker.tasks.ingest_test_run.delay", _delay)
    monkeypatch.setattr(settings, "MINIO_BUCKET_NAME", BUCKET)
    return calls


async def _notify(body: dict) -> dict:
    return await minio_webhook(_request(body), background_tasks=SimpleNamespace())


# ── The key ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_real_event_queues_the_object_key_not_bucket_slash_object(queued):
    object_key = f"{PROJECT}/runs/nightly-42/upload_complete.json"
    result = await _notify(minio_event(object_key))
    assert result["status"] == "queued", result
    assert result["project_id"] == PROJECT, "the bucket name was taken as the project"
    assert queued == [{
        "sentinel_key": object_key,
        "minio_prefix": f"{PROJECT}/runs/nightly-42/",
    }]


@pytest.mark.asyncio
async def test_an_encoded_key_is_decoded(queued):
    """A build number with a space and a plus, as CI systems write them."""
    object_key = f"{PROJECT}/runs/release 1.2+hotfix/upload_complete.json"
    event = minio_event(object_key)
    assert "%2F" in event["Records"][0]["s3"]["object"]["key"]
    assert "+" in event["Records"][0]["s3"]["object"]["key"]  # the space

    result = await _notify(event)
    assert result["status"] == "queued", result
    assert queued[0]["sentinel_key"] == object_key
    assert queued[0]["minio_prefix"] == f"{PROJECT}/runs/release 1.2+hotfix/"


@pytest.mark.asyncio
async def test_an_encoded_dot_dot_is_refused_after_decoding(queued):
    object_key = f"{PROJECT}/runs/../../other/runs/7/upload_complete.json"
    event = minio_event(object_key)
    event["Records"][0]["s3"]["object"]["key"] = event["Records"][0]["s3"]["object"]["key"].replace(
        ".", "%2E"
    )
    result = await _notify(event)
    assert result == {"status": "ignored", "reason": "unexpected_key_format"}
    assert queued == []


@pytest.mark.asyncio
async def test_another_buckets_event_is_ignored(queued):
    result = await _notify(minio_event(f"{PROJECT}/runs/1/upload_complete.json", bucket="elsewhere"))
    assert result["status"] == "ignored"
    assert queued == []


@pytest.mark.asyncio
async def test_a_non_sentinel_object_is_ignored(queued):
    result = await _notify(minio_event(f"{PROJECT}/runs/1/results/TEST-a.json"))
    assert result == {"status": "ignored", "reason": "not_sentinel"}
    assert queued == []


@pytest.mark.asyncio
async def test_a_record_without_a_bucket_name_uses_the_top_level_key_only_inside_our_bucket(queued):
    """No Records at all: the top-level Key is <bucket>/<object>."""
    object_key = f"{PROJECT}/runs/9/upload_complete.json"
    body = {"EventName": "s3:ObjectCreated:Put", "Key": f"{BUCKET}/{object_key}"}
    result = await _notify(body)
    assert result["status"] == "queued", result
    assert queued[0]["sentinel_key"] == object_key

    other = {"EventName": "s3:ObjectCreated:Put", "Key": f"elsewhere/{object_key}"}
    assert (await _notify(other))["status"] == "ignored"
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_a_multi_record_event_queues_each_sentinel(queued):
    first = minio_event(f"{PROJECT}/runs/1/upload_complete.json")
    second = minio_event(f"{PROJECT}/runs/2/upload_complete.json")
    body = copy.deepcopy(first)
    body["Records"].append(second["Records"][0])
    result = await _notify(body)
    assert result["status"] == "queued"
    assert [call["sentinel_key"] for call in queued] == [
        f"{PROJECT}/runs/1/upload_complete.json",
        f"{PROJECT}/runs/2/upload_complete.json",
    ]


# ── Authentication: MinIO's auth_token ───────────────────────────────────


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", SECRET)


@pytest.mark.asyncio
async def test_minio_auth_token_is_accepted_as_a_bearer(secret):
    await verify_webhook_secret(x_webhook_secret=None, authorization=f"Bearer {SECRET}")


@pytest.mark.asyncio
async def test_the_legacy_header_still_works(secret):
    await verify_webhook_secret(x_webhook_secret=SECRET, authorization=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("header, authorization", [
    (None, None),
    (None, "Bearer wrong"),
    (None, f"Basic {SECRET}"),
    (None, "Bearer "),
    ("wrong", None),
    ("wrong", "Bearer also-wrong"),
], ids=["nothing", "wrong-bearer", "not-bearer", "empty-bearer", "wrong-header", "both-wrong"])
async def test_anything_else_is_refused(secret, header, authorization):
    with pytest.raises(HTTPException) as exc:
        await verify_webhook_secret(x_webhook_secret=header, authorization=authorization)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_unset_secret_matches_nothing(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "")
    with pytest.raises(HTTPException):
        await verify_webhook_secret(x_webhook_secret="", authorization="Bearer ")


def test_setup_minio_sets_the_auth_token():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[3] / "scripts" / "setup-minio.sh").read_text(encoding="utf-8")
    assert 'auth_token="${WEBHOOK_SECRET}"' in script
