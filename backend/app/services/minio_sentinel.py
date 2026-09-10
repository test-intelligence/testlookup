"""The upload sentinel, read from storage by the ingestion task (re-audit N10).

``POST /webhooks/minio`` is told that an object was created. It queues the
object's key and nothing else, and ``ingest_test_run`` reads the sentinel here:

* off the API process, and inside the task's retries. The webhook used to read
  it itself and answer 200 "ignored" on any read error -- and MinIO treats a
  200 as delivered, so one transient 503 lost the upload for good;
* through a size cap. A sentinel is a few hundred bytes, and anyone who can
  write to the bucket could otherwise make a worker hold an object of any size;
* with the project taken from the object key -- the prefix the run's result
  files are read from -- never from the notification or the sentinel's content.

A sentinel that cannot be used is refused with :class:`SentinelRefused`, which
the task does not retry. Any other failure propagates, and the task retries it.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.db.storage import get_storage_provider
from app.models.schemas import SentinelFile

SENTINEL_NAME = "upload_complete.json"
MAX_SENTINEL_BYTES = 64 * 1024


class SentinelRefused(Exception):
    """The sentinel is unusable, and reading it again cannot change that."""


def _is_missing(exc: BaseException) -> bool:
    """A definitive "no such object", from either storage backend."""
    if isinstance(exc, FileNotFoundError):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code", ""))
        return code in {"NoSuchKey", "404", "NotFound"}
    return False


async def _read_capped(key: str) -> bytes:
    """The object's bytes, refusing it as soon as it passes the cap."""
    stream = get_storage_provider().stream_object(key)
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in stream:
            size += len(chunk)
            if size > MAX_SENTINEL_BYTES:
                raise SentinelRefused(
                    f"{key} is larger than {MAX_SENTINEL_BYTES} bytes; a sentinel "
                    "is a few hundred"
                )
            chunks.append(chunk)
    finally:
        await stream.aclose()
    return b"".join(chunks)


async def read_sentinel(key: str) -> SentinelFile:
    """The sentinel stored at ``key``, with its project taken from the key.

    Raises :class:`SentinelRefused` for a sentinel that is missing, oversized,
    not a JSON object or invalid. Any other error -- a timeout, a 503 --
    propagates so the caller can retry it.
    """
    parts = key.split("/")
    if not key.endswith(SENTINEL_NAME) or len(parts) < 3:
        raise SentinelRefused(f"{key} is not a sentinel key")
    try:
        raw = await _read_capped(key)
    except SentinelRefused:
        raise
    except Exception as exc:
        if _is_missing(exc):
            raise SentinelRefused(f"{key} does not exist") from exc
        raise
    try:
        data: Any = json.loads(raw)
    except ValueError as exc:  # includes JSONDecodeError and UnicodeDecodeError
        raise SentinelRefused(f"{key} is not JSON") from exc
    if not isinstance(data, dict):
        raise SentinelRefused(f"{key} is not a JSON object")
    # The project comes from WHERE THE DATA IS, never from what it claims:
    # process_sentinel reads the run's result files from this key's prefix,
    # so binding the project to the key keeps the two from disagreeing.
    data = {**data, "project_id": parts[0]}
    data.setdefault("build_number", parts[2])
    try:
        return SentinelFile(**data)
    except (ValidationError, TypeError) as exc:
        raise SentinelRefused(f"{key} is not a valid sentinel: {exc}") from exc
