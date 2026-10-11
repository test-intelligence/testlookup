#!/usr/bin/env python3
"""Prepare TestLookup's S3 object store: wait for it, create the buckets.

Replaces the old ``mc``-based bucket setup. Talks plain S3 through boto3 (it
ships in the backend image with aioboto3), so it works against the bundled
versitygw server, a legacy MinIO/Silo, or any other S3-compatible store.

    python scripts/s3_setup.py                  # wait, then create the buckets
    python scripts/s3_setup.py --dry-run        # print the plan only

One-off migration from an old S3 endpoint (e.g. the MinIO/Silo an existing
install ran before), copying every object into the store configured above.
Re-runnable: an object already copied with the same content is skipped.

    export S3_COPY_FROM_ACCESS_KEY=<old access key>
    export S3_COPY_FROM_SECRET_KEY=<old secret key>
    python scripts/s3_setup.py --copy-from-endpoint http://old-minio:9000

Connection settings come from the same variables the backend reads
(MINIO_ENDPOINT, MINIO_USE_SSL, MINIO_ACCESS_KEY, MINIO_SECRET_KEY) unless a
flag overrides them. The bucket names come from MINIO_BUCKET_NAME,
KNOWLEDGE_DOCS_BUCKET and FINETUNE_EXPORT_BUCKET (same defaults as
backend/app/core/config.py) plus the fixed ``pipeline-artifacts`` and
``compliance-packs`` buckets the backend writes to.

Exit status: 0 success, 1 failure, 2 usage error.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any, Callable, Iterable, Sequence

#: (environment variable, default) for every configurable bucket the backend
#: writes to. Defaults mirror backend/app/core/config.py.
CONFIGURABLE_BUCKETS: tuple[tuple[str, str], ...] = (
    ("MINIO_BUCKET_NAME", "test-telemetry"),
    ("KNOWLEDGE_DOCS_BUCKET", "knowledge-docs"),
    ("FINETUNE_EXPORT_BUCKET", "training-data"),
)
#: Buckets whose names are constants in the backend
#: (services/artifact_store.py, services/compliance_pack_service.py).
FIXED_BUCKETS: tuple[str, ...] = ("pipeline-artifacts", "compliance-packs")

#: User-metadata key recording the source ETag of a migrated object, so a
#: re-run can recognise an object it already copied even when the copy got a
#: different ETag (multipart uploads do).
SOURCE_ETAG_META = "testlookup-source-etag"

_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_IP_ADDRESS = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_EXISTS_CODES = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}
_AUTH_CODES = {
    "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch",
    "InvalidClientTokenId", "AuthorizationHeaderMalformed",
}


class SetupError(Exception):
    """A failure that ends the run with exit status 1."""


def log(message: str) -> None:
    print(f"[s3-setup] {message}", flush=True)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def endpoint_url(raw: str, *, use_ssl: bool) -> str:
    """``host:port`` (the backend's MINIO_ENDPOINT form) or a full URL -> URL."""
    raw = (raw or "").strip()
    if not raw:
        raise SetupError("no S3 endpoint configured (set MINIO_ENDPOINT or pass --endpoint)")
    if "://" in raw:
        if not raw.lower().startswith(("http://", "https://")):
            raise SetupError(f"unsupported endpoint scheme: {raw!r}")
        return raw.rstrip("/")
    return f"{'https' if use_ssl else 'http'}://{raw.rstrip('/')}"


def validate_bucket_name(name: str) -> str:
    if not _BUCKET_NAME.match(name) or ".." in name or _IP_ADDRESS.match(name):
        raise SetupError(
            f"invalid bucket name {name!r}: use 3-63 lowercase letters, digits, '.' or '-'"
        )
    return name


def required_buckets(env: dict[str, str] | None = None, extra: Iterable[str] = ()) -> list[str]:
    """Every bucket the backend writes to, in a stable order, without duplicates."""
    env = dict(os.environ if env is None else env)
    names = [env.get(var) or default for var, default in CONFIGURABLE_BUCKETS]
    names += list(FIXED_BUCKETS) + [b for b in extra if b]
    seen: list[str] = []
    for name in names:
        name = name.strip()
        if name and name not in seen:
            seen.append(validate_bucket_name(name))
    return seen


def make_client(endpoint: str, access_key: str, secret_key: str, region: str) -> Any:
    import boto3  # noqa: PLC0415 - imported lazily so --help works without boto3
    from botocore.client import Config  # noqa: PLC0415

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None) or {}
    return str((response.get("Error") or {}).get("Code") or "")


def _http_status(exc: BaseException) -> int:
    response = getattr(exc, "response", None) or {}
    try:
        return int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
    except (TypeError, ValueError):
        return 0


def _is_transient(exc: BaseException) -> bool:
    """Endpoint not up yet (connection refused/reset, timeout, 5xx)."""
    try:
        from botocore.exceptions import (  # noqa: PLC0415
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        )
        transient: tuple[type[BaseException], ...] = (
            EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError,
            ConnectionClosedError, ConnectionError, TimeoutError,
        )
    except ImportError:  # pragma: no cover - boto3 always brings botocore
        transient = (ConnectionError, TimeoutError)
    if isinstance(exc, transient):
        return True
    return _http_status(exc) >= 500 or _error_code(exc) in {"ServiceUnavailable", "SlowDown"}


def wait_for_endpoint(
    client: Any,
    *,
    label: str,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Poll ``ListBuckets`` until the endpoint answers; return the bucket names.

    Retries only while the server is not up (connection errors, timeouts, 5xx).
    Wrong credentials fail at once: retrying cannot fix them.
    """
    start = clock()
    while True:
        try:
            response = client.list_buckets()
            names = [b["Name"] for b in response.get("Buckets", [])]
            log(f"{label}: endpoint ready after {clock() - start:.0f}s")
            return names
        except Exception as exc:  # noqa: BLE001 - classified below
            if _error_code(exc) in _AUTH_CODES:
                raise SetupError(
                    f"{label}: the endpoint refused the credentials ({_error_code(exc)})"
                ) from exc
            if not _is_transient(exc):
                raise SetupError(f"{label}: {type(exc).__name__}: {exc}") from exc
            elapsed = clock() - start
            if elapsed >= timeout:
                raise SetupError(
                    f"{label}: not reachable after {elapsed:.0f}s ({type(exc).__name__})"
                ) from exc
            log(f"{label}: not ready yet ({type(exc).__name__}); retrying in {interval:g}s")
            sleep(interval)


def ensure_buckets(client: Any, wanted: Sequence[str], existing: Iterable[str], *, dry_run: bool) -> list[str]:
    """Create every bucket in ``wanted`` that does not exist; return those created."""
    present = set(existing)
    created: list[str] = []
    for name in wanted:
        if name in present:
            log(f"bucket {name}: exists")
            continue
        if dry_run:
            log(f"bucket {name}: would create")
            created.append(name)
            continue
        try:
            client.create_bucket(Bucket=name)
        except Exception as exc:  # noqa: BLE001 - a lost creation race is success
            if _error_code(exc) not in _EXISTS_CODES:
                raise SetupError(f"bucket {name}: create failed: {_error_code(exc) or exc}") from exc
            log(f"bucket {name}: exists")
            continue
        log(f"bucket {name}: created")
        present.add(name)
        created.append(name)
    return created


class CopyReport:
    """Counts for one migration run."""

    def __init__(self) -> None:
        self.copied = 0
        self.skipped = 0
        self.failed: list[str] = []


def _strip_etag(value: str | None) -> str:
    return str(value or "").strip().strip('"')


def _already_copied(dest: Any, bucket: str, key: str, size: int, etag: str) -> bool:
    try:
        head = dest.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - only "not there" means copy it
        if _error_code(exc) in {"404", "NoSuchKey", "NotFound"} or _http_status(exc) == 404:
            return False
        raise
    if int(head.get("ContentLength", -1)) != int(size):
        return False
    metadata = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
    return etag != "" and etag in {_strip_etag(head.get("ETag")), metadata.get(SOURCE_ETAG_META, "")}


def copy_objects(
    source: Any,
    dest: Any,
    buckets: Sequence[str],
    *,
    dry_run: bool,
    report: CopyReport | None = None,
) -> CopyReport:
    """Copy every object of ``buckets`` from ``source`` into ``dest``.

    Streams each object (never held in memory whole), keeps its Content-Type
    and user metadata, and skips keys whose size and ETag already match.
    """
    report = report or CopyReport()
    for bucket in buckets:
        paginator = source.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key, size = obj["Key"], int(obj.get("Size", 0))
                etag = _strip_etag(obj.get("ETag"))
                where = f"{bucket}/{key}"
                try:
                    if _already_copied(dest, bucket, key, size, etag):
                        report.skipped += 1
                        continue
                    if dry_run:
                        log(f"would copy {where} ({size} bytes)")
                        report.copied += 1
                        continue
                    response = source.get_object(Bucket=bucket, Key=key)
                    metadata = dict(response.get("Metadata") or {})
                    if etag:
                        metadata[SOURCE_ETAG_META] = etag
                    extra: dict[str, Any] = {"Metadata": metadata}
                    if response.get("ContentType"):
                        extra["ContentType"] = response["ContentType"]
                    body = response["Body"]
                    try:
                        dest.upload_fileobj(body, bucket, key, ExtraArgs=extra)
                    finally:
                        close = getattr(body, "close", None)
                        if callable(close):
                            close()
                    report.copied += 1
                except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                    report.failed.append(f"{where}: {_error_code(exc) or type(exc).__name__}")
                    log(f"FAILED {where}: {_error_code(exc) or exc}")
        log(f"bucket {bucket}: {report.copied} copied, {report.skipped} skipped so far")
    return report


def _parser(env: dict[str, str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s3_setup.py",
        description="Wait for TestLookup's S3 store, create its buckets, optionally migrate objects.",
    )
    parser.add_argument("--endpoint", help="S3 endpoint, URL or host:port (default: MINIO_ENDPOINT)")
    parser.add_argument("--access-key", help="default: MINIO_ACCESS_KEY")
    parser.add_argument("--secret-key", help="default: MINIO_SECRET_KEY (prefer the env var)")
    parser.add_argument("--region", default=env.get("S3_REGION", "us-east-1"))
    parser.add_argument("--bucket", action="append", default=[], metavar="NAME",
                        help="create this bucket too (repeatable)")
    parser.add_argument("--wait-seconds", type=float,
                        default=float(env.get("S3_SETUP_WAIT_SECONDS", "120")))
    parser.add_argument("--wait-interval", type=float,
                        default=float(env.get("S3_SETUP_WAIT_INTERVAL", "2")))
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    migrate = parser.add_argument_group("one-off migration from an old S3 endpoint")
    migrate.add_argument("--copy-from-endpoint", default=env.get("S3_COPY_FROM_ENDPOINT"),
                         help="source endpoint URL, e.g. http://old-minio:9000 (env S3_COPY_FROM_ENDPOINT)")
    migrate.add_argument("--copy-from-access-key", default=env.get("S3_COPY_FROM_ACCESS_KEY"),
                         help="env S3_COPY_FROM_ACCESS_KEY")
    migrate.add_argument("--copy-from-secret-key", default=env.get("S3_COPY_FROM_SECRET_KEY"),
                         help="env S3_COPY_FROM_SECRET_KEY (prefer the env var: argv is visible to ps)")
    migrate.add_argument("--copy-from-region", default=env.get("S3_COPY_FROM_REGION", "us-east-1"))
    migrate.add_argument("--copy-bucket", action="append", default=[], metavar="NAME",
                         help="copy only this source bucket (repeatable; default: every source bucket)")
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    client_factory: Callable[..., Any] = make_client,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    env = dict(os.environ if env is None else env)
    args = _parser(env).parse_args(argv)
    try:
        dest_url = endpoint_url(args.endpoint or env.get("MINIO_ENDPOINT", ""),
                                use_ssl=_truthy(env.get("MINIO_USE_SSL")))
        access = args.access_key or env.get("MINIO_ACCESS_KEY", "")
        secret = args.secret_key or env.get("MINIO_SECRET_KEY", "")
        if not access or not secret:
            raise SetupError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set (or pass --access-key/--secret-key)")
        wanted = required_buckets(env, args.bucket)
        log(f"target {dest_url}; buckets: {', '.join(wanted)}" + (" (dry run)" if args.dry_run else ""))

        dest = client_factory(dest_url, access, secret, args.region)
        existing = wait_for_endpoint(dest, label="target", timeout=args.wait_seconds,
                                     interval=args.wait_interval, sleep=sleep)
        ensure_buckets(dest, wanted, existing, dry_run=args.dry_run)

        if args.copy_from_endpoint:
            src_url = endpoint_url(args.copy_from_endpoint, use_ssl=False)
            if src_url == dest_url:
                raise SetupError("--copy-from-endpoint is the target endpoint itself")
            if not args.copy_from_access_key or not args.copy_from_secret_key:
                raise SetupError("--copy-from-endpoint needs --copy-from-access-key and --copy-from-secret-key")
            source = client_factory(src_url, args.copy_from_access_key,
                                    args.copy_from_secret_key, args.copy_from_region)
            source_buckets = wait_for_endpoint(source, label="source", timeout=args.wait_seconds,
                                               interval=args.wait_interval, sleep=sleep)
            if args.copy_bucket:
                buckets = [validate_bucket_name(b) for b in args.copy_bucket]
            else:
                # A legacy server run on the SAME volume reports the new store's
                # own data directory (`s3`, deliberately not a valid bucket
                # name) and similar non-buckets: copy only real bucket names.
                buckets = []
                for name in sorted(source_buckets):
                    try:
                        buckets.append(validate_bucket_name(name))
                    except SetupError:
                        log(f"source bucket {name!r}: not a valid S3 bucket name, skipped")
            missing = sorted(set(buckets) - set(source_buckets))
            if missing:
                raise SetupError(f"source has no bucket(s): {', '.join(missing)}")
            ensure_buckets(dest, buckets, existing + list(wanted), dry_run=args.dry_run)
            log(f"copying {len(buckets)} bucket(s) from {src_url}")
            report = copy_objects(source, dest, buckets, dry_run=args.dry_run)
            log(f"migration: {report.copied} copied, {report.skipped} already present, "
                f"{len(report.failed)} failed")
            if report.failed:
                raise SetupError(f"{len(report.failed)} object(s) failed to copy; re-run to retry them")
    except SetupError as exc:
        log(f"ERROR: {exc}")
        return 1
    log("done")
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
