"""Export / import every MinIO bucket as one tar.gz (re-audit M25).

Runs in the backend image (boto3 comes with aioboto3), with the backend's own
MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_USE_SSL.

    python minio_sync.py dump    /staging/minio_data.tar.gz
    python minio_sync.py restore /staging/minio_data.tar.gz

Archive layout: ``<bucket>/`` directory entries (so empty buckets survive) and
``<bucket>/<key>`` file entries. Objects are streamed, never held in memory
whole. ``restore`` creates missing buckets and overwrites objects; objects
written after the backup was taken are left in place.
"""
from __future__ import annotations

import os
import sys
import tarfile
import time
import urllib.request


def client():
    import boto3
    from botocore.client import Config

    scheme = "https" if os.environ.get("MINIO_USE_SSL", "false").lower() == "true" else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{os.environ['MINIO_ENDPOINT']}",
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def minio_ready() -> bool:
    """MinIO's liveness endpoint, on the endpoint the export will use."""
    scheme = "https" if os.environ.get("MINIO_USE_SSL", "false").lower() == "true" else "http"
    url = f"{scheme}://{os.environ['MINIO_ENDPOINT']}/minio/health/live"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def wait_for(store: str, probe, limit: float | None = None, interval: float | None = None) -> None:
    """Poll ``probe`` until it returns True; exit non-zero after the deadline.

    Same contract as lib.sh's wait_for (BACKUP_WAIT_SECONDS, default 120;
    BACKUP_WAIT_INTERVAL, default 2): a new pod's NetworkPolicy allow rules
    are programmed after it starts, so the first connections can be refused.
    """
    limit = float(os.environ.get("BACKUP_WAIT_SECONDS", "120")) if limit is None else limit
    interval = float(os.environ.get("BACKUP_WAIT_INTERVAL", "2")) if interval is None else interval
    start = time.monotonic()
    while True:
        if probe():
            print(f"{store}: accepting connections after {time.monotonic() - start:.0f}s")
            return
        elapsed = time.monotonic() - start
        if elapsed >= limit:
            raise SystemExit(f"{store}: not reachable after {elapsed:.0f}s (BACKUP_WAIT_SECONDS={limit:g})")
        print(f"{store}: not accepting connections yet ({elapsed:.0f}s elapsed); retrying in {interval:g}s")
        time.sleep(interval)


def dump(target: str, s3=None) -> int:
    s3 = s3 or client()
    tmp = f"{target}.tmp"
    count = 0
    with tarfile.open(tmp, "w:gz") as tar:
        for bucket in sorted(b["Name"] for b in s3.list_buckets()["Buckets"]):
            directory = tarfile.TarInfo(f"{bucket}/")
            directory.type = tarfile.DIRTYPE
            tar.addfile(directory)
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"]
                    info = tarfile.TarInfo(f"{bucket}/{obj['Key']}")
                    info.size = obj["Size"]
                    tar.addfile(info, body)
                    count += 1
    os.replace(tmp, target)
    print(f"minio: exported {count} objects")
    return count


def restore(source: str, s3=None) -> int:
    if not os.path.isfile(source) or os.path.getsize(source) == 0:
        raise SystemExit(f"minio: no archive at {source}")
    s3 = s3 or client()
    existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    count = 0
    with tarfile.open(source, "r:gz") as tar:
        for member in tar:
            bucket, _, key = member.name.partition("/")
            if bucket not in existing:
                s3.create_bucket(Bucket=bucket)
                existing.add(bucket)
            if member.isdir() or not key:
                continue
            s3.upload_fileobj(tar.extractfile(member), bucket, key)
            count += 1
    print(f"minio: restored {count} objects")
    return count


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("dump", "restore"):
        print("usage: minio_sync.py dump|restore <archive>", file=sys.stderr)
        return 2
    wait_for("minio", minio_ready)
    (dump if argv[1] == "dump" else restore)(argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
