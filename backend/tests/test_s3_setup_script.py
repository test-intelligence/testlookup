"""backend/scripts/s3_setup.py: bucket setup + one-off S3 migration, no network.

The script replaced the ``mc``-based setup (pgsty/mc, AGPL-3.0) when the bundled
object store moved to versitygw. A fake boto3 client stands in for S3 here; the
real-server half lives in tests/integration/test_s3_storage_versitygw.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import re
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "s3_setup.py"
SPEC = importlib.util.spec_from_file_location("s3_setup", SCRIPT)
s3_setup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(s3_setup)

ENV = {
    "MINIO_ENDPOINT": "minio:9000",
    "MINIO_ACCESS_KEY": "test-access",
    "MINIO_SECRET_KEY": "test-secret-not-real",
}
DEFAULT_BUCKETS = ["test-telemetry", "knowledge-docs", "training-data", "pipeline-artifacts", "compliance-packs"]


def _client_error(code: str, status: int = 400, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op)


class FakeS3:
    """Just enough of a boto3 S3 client for the script."""

    def __init__(self, buckets=None, list_errors=None, page_size=2):
        self.buckets: dict[str, dict[str, dict]] = {name: {} for name in (buckets or [])}
        self.list_errors = list(list_errors or [])
        self.page_size = page_size
        self.created: list[str] = []
        self.uploads: list[tuple[str, str]] = []
        self.fail_upload_keys: set[str] = set()
        self.race_on_create: set[str] = set()

    def put(self, bucket, key, data: bytes, content_type="application/json", metadata=None, etag=None):
        self.buckets.setdefault(bucket, {})[key] = {
            "data": data, "ContentType": content_type, "Metadata": dict(metadata or {}),
            "ETag": etag or hashlib.md5(data).hexdigest(),  # noqa: S324 - S3 ETag semantics
        }

    def list_buckets(self):
        if self.list_errors:
            raise self.list_errors.pop(0)
        return {"Buckets": [{"Name": name} for name in self.buckets]}

    def create_bucket(self, Bucket):  # noqa: N803 - boto3 keyword names
        if Bucket in self.race_on_create:
            self.buckets[Bucket] = {}
            raise _client_error("BucketAlreadyOwnedByYou", 409, "CreateBucket")
        if Bucket in self.buckets:
            raise _client_error("BucketAlreadyOwnedByYou", 409, "CreateBucket")
        self.buckets[Bucket] = {}
        self.created.append(Bucket)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class _Paginator:
            def paginate(self, Bucket):  # noqa: N803
                if Bucket not in fake.buckets:
                    raise _client_error("NoSuchBucket", 404, "ListObjectsV2")
                keys = sorted(fake.buckets[Bucket])
                for i in range(0, max(len(keys), 1), fake.page_size):
                    chunk = keys[i:i + fake.page_size]
                    yield {"Contents": [
                        {"Key": k, "Size": len(fake.buckets[Bucket][k]["data"]),
                         "ETag": '"' + fake.buckets[Bucket][k]["ETag"] + '"'}
                        for k in chunk
                    ]} if chunk else {}

        return _Paginator()

    def head_object(self, Bucket, Key):  # noqa: N803
        obj = self.buckets.get(Bucket, {}).get(Key)
        if obj is None:
            raise _client_error("404", 404, "HeadObject")
        return {"ContentLength": len(obj["data"]), "ETag": '"' + obj["ETag"] + '"', "Metadata": obj["Metadata"]}

    def get_object(self, Bucket, Key):  # noqa: N803
        obj = self.buckets[Bucket][Key]
        return {"Body": io.BytesIO(obj["data"]), "ContentType": obj["ContentType"], "Metadata": dict(obj["Metadata"])}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):  # noqa: N803
        if key in self.fail_upload_keys:
            raise _client_error("InternalError", 500, "PutObject")
        extra = ExtraArgs or {}
        data = fileobj.read()
        # A multipart upload's ETag is not the MD5 of the body: model that.
        self.put(bucket, key, data, extra.get("ContentType", "binary/octet-stream"),
                 extra.get("Metadata"), etag=hashlib.md5(data).hexdigest() + "-2")  # noqa: S324
        self.uploads.append((bucket, key))


def _factory(clients: dict[str, FakeS3]):
    seen: list[tuple[str, str, str, str]] = []

    def factory(endpoint, access, secret, region):
        seen.append((endpoint, access, secret, region))
        return clients[endpoint]

    factory.seen = seen  # type: ignore[attr-defined]
    return factory


def _run(argv, clients, env=None, sleeps=None):
    return s3_setup.run(
        argv, env=dict(ENV if env is None else env), client_factory=_factory(clients),
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
    )


# ── Bucket list ────────────────────────────────────────────────────────────────

def test_default_buckets_are_every_bucket_the_backend_writes_to():
    assert s3_setup.required_buckets({}) == DEFAULT_BUCKETS


def test_bucket_names_follow_the_backend_settings():
    env = {"MINIO_BUCKET_NAME": "uploads", "KNOWLEDGE_DOCS_BUCKET": "kbase", "FINETUNE_EXPORT_BUCKET": "uploads"}
    assert s3_setup.required_buckets(env, ["extra-one", "kbase"]) == [
        "uploads", "kbase", "pipeline-artifacts", "compliance-packs", "extra-one",
    ]


@pytest.mark.parametrize("bad", ["UPPER", "ab", "a_b", "-lead", "203.0.113.7", "a..b", "x" * 64])
def test_invalid_bucket_names_are_refused(bad):
    with pytest.raises(s3_setup.SetupError):
        s3_setup.required_buckets({}, [bad])


def test_script_buckets_match_the_backend_defaults_and_constants():
    """A bucket the app writes to but nobody creates fails every write with
    NoSuchBucket. Only test-telemetry (+ knowledge-docs on k8s) used to be
    created; pipeline-artifacts, compliance-packs and training-data were not."""
    from app.core.config import Settings
    from app.services import artifact_store, compliance_pack_service, retention_service

    fields = Settings.model_fields
    configured = {var: fields[var].default for var, _ in s3_setup.CONFIGURABLE_BUCKETS}
    assert configured == dict(s3_setup.CONFIGURABLE_BUCKETS)
    constants = {
        artifact_store._ARTIFACT_BUCKET,
        compliance_pack_service.COMPLIANCE_BUCKET,
        retention_service._PIPELINE_BUCKET,
        retention_service._COMPLIANCE_BUCKET,
    }
    assert constants <= set(s3_setup.FIXED_BUCKETS)


def test_every_bucket_literal_in_the_backend_is_created():
    """Source scan, so a new hard-coded bucket cannot be added silently: a
    ``*_BUCKET = "..."`` constant, or a ``bucket="..."`` literal passed to a
    storage call (other ``bucket=`` keywords, e.g. the gap-detection agent's
    result buckets, are not object-store buckets)."""
    constant = re.compile(r"""_BUCKET\s*(?::\s*str\s*)?=\s*["']([a-z0-9][a-z0-9.-]+)["']""")
    call = re.compile(
        r"""(?s)\b(?:put_object|get_object_content|stream_object|list_objects|delete_object|"""
        r"""delete_prefix|get_presigned_url)\((?:(?!\n\s*\n).){0,400}?\bbucket\s*=\s*["']([a-z0-9][a-z0-9.-]+)["']"""
    )
    found: dict[str, str] = {}
    for path in (BACKEND / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in constant.findall(text) + call.findall(text):
            found.setdefault(name, path.relative_to(BACKEND).as_posix())
    assert "compliance-packs" in found, "the scan missed a known literal: it no longer guards anything"
    missing = {name: where for name, where in found.items() if name not in s3_setup.required_buckets({})}
    assert missing == {}


def test_the_unused_public_attachments_bucket_is_gone():
    """testlookup-attachments was created with an anonymous-download policy and
    nothing in backend/app ever used it."""
    assert "testlookup-attachments" not in s3_setup.required_buckets({})
    for path in (BACKEND / "app").rglob("*.py"):
        assert "testlookup-attachments" not in path.read_text(encoding="utf-8"), path


# ── Endpoint handling ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, ssl, url", [
    ("minio:9000", False, "http://minio:9000"),
    ("minio:9000", True, "https://minio:9000"),
    ("http://old:9000/", True, "http://old:9000"),
    ("https://s3.example.com", False, "https://s3.example.com"),
])
def test_endpoint_url(raw, ssl, url):
    assert s3_setup.endpoint_url(raw, use_ssl=ssl) == url


@pytest.mark.parametrize("raw", ["", "ftp://old:21"])
def test_endpoint_url_refuses_nonsense(raw):
    with pytest.raises(s3_setup.SetupError):
        s3_setup.endpoint_url(raw, use_ssl=False)


# ── Bucket creation ────────────────────────────────────────────────────────────

def test_creates_every_missing_bucket_once_and_is_idempotent():
    target = FakeS3(buckets=["test-telemetry"])
    clients = {"http://minio:9000": target}
    assert _run([], clients) == 0
    assert target.created == DEFAULT_BUCKETS[1:]
    assert _run([], clients) == 0
    assert target.created == DEFAULT_BUCKETS[1:], "a second run created something again"


def test_uses_the_backend_connection_settings():
    clients = {"https://s3.internal:443": FakeS3()}
    factory = _factory(clients)
    env = {**ENV, "MINIO_ENDPOINT": "s3.internal:443", "MINIO_USE_SSL": "true"}
    assert s3_setup.run([], env=env, client_factory=factory, sleep=lambda _s: None) == 0
    assert factory.seen == [("https://s3.internal:443", "test-access", "test-secret-not-real", "us-east-1")]


def test_a_lost_creation_race_counts_as_success():
    target = FakeS3()
    target.race_on_create = {"knowledge-docs"}
    assert _run([], {"http://minio:9000": target}) == 0
    assert set(target.buckets) == set(DEFAULT_BUCKETS)


def test_dry_run_creates_nothing():
    target = FakeS3()
    assert _run(["--dry-run"], {"http://minio:9000": target}) == 0
    assert target.created == [] and target.buckets == {}


def test_missing_credentials_fail_without_contacting_anything():
    env = {"MINIO_ENDPOINT": "minio:9000"}
    assert _run([], {}, env=env) == 1


def test_waits_while_the_endpoint_is_starting():
    sleeps: list[float] = []
    target = FakeS3(list_errors=[
        EndpointConnectionError(endpoint_url="http://minio:9000"),
        _client_error("ServiceUnavailable", 503, "ListBuckets"),
    ])
    assert _run(["--wait-interval", "0.5"], {"http://minio:9000": target}, sleeps=sleeps) == 0
    assert sleeps == [0.5, 0.5]
    assert set(target.buckets) == set(DEFAULT_BUCKETS)


def test_wrong_credentials_fail_at_once():
    sleeps: list[float] = []
    target = FakeS3(list_errors=[_client_error("InvalidAccessKeyId", 403, "ListBuckets")])
    assert _run([], {"http://minio:9000": target}, sleeps=sleeps) == 1
    assert sleeps == [], "retrying cannot fix a wrong key"
    assert target.created == []


def test_gives_up_after_the_wait_budget():
    errors = [EndpointConnectionError(endpoint_url="x") for _ in range(10)]
    clock = iter(range(0, 1000, 30))
    with pytest.raises(s3_setup.SetupError, match="not reachable"):
        s3_setup.wait_for_endpoint(
            FakeS3(list_errors=errors), label="target", timeout=60, interval=1,
            sleep=lambda _s: None, clock=lambda: next(clock),
        )


# ── Migration (--copy-from-*) ──────────────────────────────────────────────────

def _old_store() -> FakeS3:
    old = FakeS3(buckets=["test-telemetry", "legacy-extra"])
    old.put("test-telemetry", "p1/runs/1/results.json", b'{"a": 1}')
    old.put("test-telemetry", "p1/runs/1/upload_complete.json", b"{}")
    old.put("test-telemetry", "p1/runs/2/report.xml", b"<x/>", "application/xml", {"origin": "ci"})
    old.put("legacy-extra", "blob.bin", b"\x00\x01", "application/octet-stream")
    return old


COPY_ARGS = [
    "--copy-from-endpoint", "http://old-minio:9000",
    "--copy-from-access-key", "old-access", "--copy-from-secret-key", "old-secret-not-real",
]


def test_copies_every_object_with_its_type_and_metadata():
    old, new = _old_store(), FakeS3()
    assert _run(COPY_ARGS, {"http://minio:9000": new, "http://old-minio:9000": old}) == 0
    for bucket, objects in old.buckets.items():
        for key, obj in objects.items():
            copied = new.buckets[bucket][key]
            assert copied["data"] == obj["data"]
            assert copied["ContentType"] == obj["ContentType"]
            for name, value in obj["Metadata"].items():
                assert copied["Metadata"][name] == value
            assert copied["Metadata"][s3_setup.SOURCE_ETAG_META] == obj["ETag"]
    assert set(DEFAULT_BUCKETS) <= set(new.buckets), "the app buckets are still created"


def test_a_rerun_skips_identical_objects_and_recopies_changed_ones():
    old, new = _old_store(), FakeS3()
    clients = {"http://minio:9000": new, "http://old-minio:9000": old}
    assert _run(COPY_ARGS, clients) == 0
    first = len(new.uploads)
    assert first == 4
    assert _run(COPY_ARGS, clients) == 0
    assert len(new.uploads) == first, "identical objects were copied again"

    old.put("test-telemetry", "p1/runs/1/results.json", b'{"a": 2, "b": 3}')
    assert _run(COPY_ARGS, clients) == 0
    assert new.uploads[first:] == [("test-telemetry", "p1/runs/1/results.json")]
    assert new.buckets["test-telemetry"]["p1/runs/1/results.json"]["data"] == b'{"a": 2, "b": 3}'


def test_an_object_that_already_matches_by_etag_is_skipped():
    old = _old_store()
    new = FakeS3(buckets=["test-telemetry"])
    obj = old.buckets["test-telemetry"]["p1/runs/1/results.json"]
    new.put("test-telemetry", "p1/runs/1/results.json", obj["data"], etag=obj["ETag"])
    assert _run(COPY_ARGS + ["--copy-bucket", "test-telemetry"],
                {"http://minio:9000": new, "http://old-minio:9000": old}) == 0
    assert ("test-telemetry", "p1/runs/1/results.json") not in new.uploads
    assert "legacy-extra" not in new.buckets, "--copy-bucket limits the copy"


def test_the_new_stores_own_directory_is_not_copied_back():
    """A legacy MinIO/Silo started on the same volume lists versitygw's data
    directory `s3` (2 characters: never a valid bucket) as if it were one."""
    old, new = _old_store(), FakeS3()
    old.buckets["s3"] = {}
    old.put("s3", "test-telemetry/x.json", b"{}")
    assert _run(COPY_ARGS, {"http://minio:9000": new, "http://old-minio:9000": old}) == 0
    assert "s3" not in new.buckets
    assert len(new.uploads) == 4


def test_a_failed_object_fails_the_run_but_the_rest_are_copied():
    old, new = _old_store(), FakeS3()
    new.fail_upload_keys = {"p1/runs/2/report.xml"}
    assert _run(COPY_ARGS, {"http://minio:9000": new, "http://old-minio:9000": old}) == 1
    assert len(new.uploads) == 3


def test_copy_dry_run_writes_nothing():
    old, new = _old_store(), FakeS3()
    assert _run(COPY_ARGS + ["--dry-run"], {"http://minio:9000": new, "http://old-minio:9000": old}) == 0
    assert new.uploads == [] and new.buckets == {}


def test_copy_credentials_can_come_from_the_environment():
    old, new = _old_store(), FakeS3()
    env = {**ENV, "S3_COPY_FROM_ENDPOINT": "http://old-minio:9000",
           "S3_COPY_FROM_ACCESS_KEY": "old-access", "S3_COPY_FROM_SECRET_KEY": "old-secret-not-real"}
    assert _run([], {"http://minio:9000": new, "http://old-minio:9000": old}, env=env) == 0
    assert len(new.uploads) == 4


@pytest.mark.parametrize("argv", [
    ["--copy-from-endpoint", "http://minio:9000", "--copy-from-access-key", "a", "--copy-from-secret-key", "b"],
    ["--copy-from-endpoint", "http://old-minio:9000"],
    COPY_ARGS + ["--copy-bucket", "not-on-the-source"],
])
def test_copy_refuses_bad_requests(argv):
    old, new = _old_store(), FakeS3()
    assert _run(argv, {"http://minio:9000": new, "http://old-minio:9000": old}) == 1
    assert new.uploads == []


# ── Packaging ──────────────────────────────────────────────────────────────────

def test_the_script_ships_in_the_production_image():
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=builder /app/scripts ./scripts" in dockerfile


def test_help_works_without_contacting_anything(capsys):
    with pytest.raises(SystemExit) as exc:
        s3_setup.run(["--help"], env={}, client_factory=lambda *a: pytest.fail("connected"))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--copy-from-endpoint", "--copy-from-access-key", "--copy-from-secret-key", "--bucket", "--dry-run"):
        assert flag in out
