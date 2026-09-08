"""Bounded M11 cutover helpers for live data persisted before sanitization."""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from pymongo import ReplaceOne
from redis.exceptions import WatchError
from sqlalchemy import or_, select

from app.models.postgres import TestCase, TestRun
from app.services.ingestion_sanitization import (
    LIVE_SANITIZATION_VERSION,
    LIVE_SANITIZATION_VERSION_FIELD,
    sanitize_test_result_payload,
)
from app.services.redaction_service import REDACTED

M11_SANITIZATION_VERSION = LIVE_SANITIZATION_VERSION
M11_MONGO_MARKER = LIVE_SANITIZATION_VERSION_FIELD


def sanitize_event_archive(archive: object) -> list[Any]:
    """Return a safe archive while preserving event order and cardinality."""
    if not isinstance(archive, list):
        return [{"_redacted": REDACTED}]
    return [
        sanitize_test_result_payload(event)
        if isinstance(event, Mapping)
        else {"_redacted": REDACTED}
        for event in archive
    ]


async def scrub_postgres_archive_batch(
    db,
    *,
    after_id: uuid.UUID | None = None,
    batch_size: int = 200,
) -> tuple[int, uuid.UUID | None]:
    """Sanitize one keyset page of non-null TestRun archives."""
    stmt = (
        select(TestRun)
        .where(TestRun.event_archive.is_not(None))
        .order_by(TestRun.id)
        .limit(max(1, batch_size))
    )
    if after_id is not None:
        stmt = stmt.where(TestRun.id > after_id)
    rows = list((await db.execute(stmt)).scalars().all())
    for run in rows:
        safe_archive = sanitize_event_archive(run.event_archive)
        if safe_archive != run.event_archive:
            run.event_archive = safe_archive
    return len(rows), rows[-1].id if rows else after_id


async def scrub_postgres_test_case_batch(
    db,
    *,
    after_id: uuid.UUID | None = None,
    batch_size: int = 200,
) -> tuple[int, uuid.UUID | None]:
    """Sanitize one keyset page of TestCase rows belonging to live runs."""
    stmt = (
        select(TestCase)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            or_(
                TestRun.trigger_source == "live_stream",
                TestRun.ingestion_source == "live",
            )
        )
        .order_by(TestCase.id)
        .limit(max(1, batch_size))
    )
    if after_id is not None:
        stmt = stmt.where(TestCase.id > after_id)
    rows = list((await db.execute(stmt)).scalars().all())
    for test_case in rows:
        safe = sanitize_test_result_payload({
            "error_message": test_case.error_message,
            "stack_trace": test_case.stack_trace,
            "tags": test_case.tags,
        })
        test_case.error_message = safe["error_message"]
        test_case.stack_trace = safe["stack_trace"]
        test_case.tags = safe["tags"]
    return len(rows), rows[-1].id if rows else after_id


async def scrub_mongo_live_events(
    collection,
    *,
    batch_size: int = 200,
) -> int:
    """Stream and replace every legacy live-event document in bounded writes."""
    # The legacy endpoint accepted arbitrary document keys, including the
    # version marker. Never trust that client-controlled value to skip a row.
    cursor = collection.find({}).sort("_id", 1)
    operations = []
    total = 0
    async for document in cursor:
        document_id = document["_id"]
        safe = sanitize_test_result_payload({
            key: value for key, value in document.items() if key != "_id"
        })
        safe["_id"] = document_id
        safe[M11_MONGO_MARKER] = M11_SANITIZATION_VERSION
        operations.append(ReplaceOne({"_id": document_id}, safe))
        total += 1
        if len(operations) >= max(1, batch_size):
            await collection.bulk_write(operations, ordered=False)
            operations = []
    if operations:
        await collection.bulk_write(operations, ordered=False)
    return total


def _redis_field(row: Mapping[Any, Any], name: str) -> Any:
    return row.get(name, row.get(name.encode()))


def _redis_text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def purge_drained_live_stream(
    redis,
    stream_key: str,
    *,
    group_name: str,
) -> int:
    """Purge an idle consumed stream and recreate its group without replay."""
    try:
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.watch(stream_key)
            if not await pipe.exists(stream_key):
                pipe.multi()
                pipe.xgroup_create(stream_key, group_name, id="$", mkstream=True)
                await pipe.execute()
                return 0

            groups = await pipe.xinfo_groups(stream_key)
            unexpected = [
                _redis_field(group, "name")
                for group in groups
                if _redis_text(_redis_field(group, "name")) != group_name
            ]
            target = next(
                (
                    group for group in groups
                    if _redis_text(_redis_field(group, "name")) == group_name
                ),
                None,
            )
            if unexpected or target is None:
                raise RuntimeError(
                    "live stream consumer-group topology is not the expected one"
                )
            if int(_redis_field(target, "pending") or 0) != 0:
                raise RuntimeError("live stream has pending entries; cutover refused")
            lag = _redis_field(target, "lag")
            if lag is None:
                raise RuntimeError("live stream lag is unknown; cutover refused")
            if int(lag) != 0:
                raise RuntimeError("live stream has undelivered entries; cutover refused")

            removed = int(await pipe.xlen(stream_key))
            pipe.multi()
            pipe.delete(stream_key)
            pipe.xgroup_create(stream_key, group_name, id="$", mkstream=True)
            await pipe.execute()
            return removed
    except WatchError as exc:
        raise RuntimeError("live stream changed during cutover; purge refused") from exc


async def scrub_unconsumed_redis_stream(
    redis,
    stream_key: str,
    *,
    batch_size: int = 200,
) -> int:
    """Rebuild an unconsumed stream with safe fields and the original IDs."""
    temp_key = f"{stream_key}:m11-scrub:{uuid.uuid4().hex}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.watch(stream_key)
            if not await pipe.exists(stream_key):
                return 0
            if await pipe.xinfo_groups(stream_key):
                raise RuntimeError(
                    "stream has consumer groups; ID-preserving scrub refused"
                )
            high_rows = await pipe.xrevrange(stream_key, count=1)
            if not high_rows:
                return 0
            stop_id = high_rows[0][0]
            after_id: str | None = None
            total = 0
            temp_has_ttl = False
            while True:
                minimum = f"({after_id}" if after_id else "-"
                rows = await pipe.xrange(
                    stream_key,
                    min=minimum,
                    max=stop_id,
                    count=max(1, batch_size),
                )
                if not rows:
                    break
                for message_id, fields in rows:
                    await redis.xadd(
                        temp_key,
                        sanitize_test_result_payload(fields),
                        id=message_id,
                    )
                    total += 1
                    if not temp_has_ttl:
                        await redis.pexpire(temp_key, 3_600_000)
                        temp_has_ttl = True
                after_id = rows[-1][0]
            pipe.multi()
            pipe.persist(temp_key)
            pipe.rename(temp_key, stream_key)
            await pipe.execute()
            return total
    except WatchError as exc:
        await redis.delete(temp_key)
        raise RuntimeError("stream changed during cutover; scrub refused") from exc
    except Exception:
        await redis.delete(temp_key)
        raise


def sanitize_buffer_entry(raw: str) -> str:
    """Sanitize one legacy Redis LIST entry, failing closed if malformed."""
    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        return json.dumps({"_redacted": REDACTED})
    if not isinstance(event, Mapping):
        return json.dumps({"_redacted": REDACTED})
    return json.dumps(sanitize_test_result_payload(event), separators=(",", ":"))


async def scrub_redis_list(
    redis,
    list_key: str,
    *,
    batch_size: int = 200,
) -> int:
    """Replace a legacy live buffer through a temporary key with its TTL."""
    temp_key = f"{list_key}:m11-scrub:{uuid.uuid4().hex}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.watch(list_key)
            ttl_ms = await pipe.pttl(list_key)
            if ttl_ms == -2:
                return 0

            offset = 0
            total = 0
            while True:
                rows = await pipe.lrange(
                    list_key,
                    offset,
                    offset + max(1, batch_size) - 1,
                )
                if not rows:
                    break
                safe_rows = [sanitize_buffer_entry(row) for row in rows]
                await redis.rpush(temp_key, *safe_rows)
                if total == 0:
                    await redis.pexpire(
                        temp_key,
                        ttl_ms if ttl_ms > 0 else 3_600_000,
                    )
                total += len(rows)
                offset += len(rows)
            if not total:
                await redis.delete(temp_key)
                return 0

            pipe.multi()
            if ttl_ms > 0:
                pipe.pexpire(temp_key, ttl_ms)
            else:
                pipe.persist(temp_key)
            pipe.rename(temp_key, list_key)
            await pipe.execute()
            return total
    except WatchError as exc:
        await redis.delete(temp_key)
        raise RuntimeError("live buffer changed during cutover; scrub refused") from exc
    except Exception:
        await redis.delete(temp_key)
        raise
