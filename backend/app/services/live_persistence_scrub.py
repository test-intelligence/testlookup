"""Bounded M11 cutover helpers for live data persisted before sanitization."""
from __future__ import annotations

import hashlib
import json
import time
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
from app.streams import LIVE_BATCH_DEDUP_KEY

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


def _canonical_event_json(value: Any) -> str:
    """Serialize evidence with the same canonical form as the live writer."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _legacy_evidence_session_id(list_key: str) -> str:
    """Return a bounded, stable producer identity for one legacy LIST."""
    digest = hashlib.sha256(list_key.encode("utf-8")).hexdigest()
    return f"legacy-migration-{digest}"


def _stable_event_id(session_id: str, batch_id: str, index: int) -> str:
    preimage = f"{session_id}\0{batch_id}\0{index}".encode("utf-8")
    return hashlib.sha256(preimage).hexdigest()


def _migration_progress_key(stream_key: str) -> str:
    return f"{stream_key}:legacy-list-migration:v1"


def _parse_migration_progress(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(_redis_text(raw))
    except (TypeError, ValueError):
        raise RuntimeError("legacy migration checkpoint is malformed") from None
    required = {"source_key", "source_length", "next_index", "session_id"}
    if not isinstance(parsed, dict) or not required.issubset(parsed):
        raise RuntimeError("legacy migration checkpoint is malformed")
    return parsed


async def migrate_redis_list_to_evidence_stream(
    redis,
    list_key: str,
    stream_key: str,
    *,
    run_id: str,
    batch_size: int = 200,
) -> int:
    """Checkpoint a frozen legacy LIST into the stable evidence Stream.

    Each committed page appends one batch manifest and advances the checkpoint in one
    Redis transaction. A retry therefore resumes at the first uncommitted LIST
    index and never appends a duplicate event. The source LIST is made
    persistent until :func:`remove_drained_legacy_redis_list` proves the new
    persistence consumer has acknowledged the migrated evidence.
    """
    page_size = max(1, batch_size)
    progress_key = _migration_progress_key(stream_key)
    dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
    migrated = 0

    try:
        while True:
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(list_key, stream_key, dedupe_key, progress_key)
                source_length = int(await pipe.llen(list_key))
                progress = _parse_migration_progress(await pipe.get(progress_key))

                if progress is None:
                    if source_length == 0:
                        return migrated
                    session_id = _legacy_evidence_session_id(list_key)
                    progress = {
                        "version": 1,
                        "source_key": list_key,
                        "source_length": source_length,
                        "next_index": 0,
                        "session_id": session_id,
                    }
                elif (
                    progress["source_key"] != list_key
                    or int(progress["source_length"]) != source_length
                ):
                    raise RuntimeError(
                        "legacy evidence LIST changed after migration began"
                    )

                start = int(progress["next_index"])
                if start < 0 or start > source_length:
                    raise RuntimeError("legacy migration checkpoint is out of bounds")
                if start == source_length:
                    return migrated

                rows = await pipe.lrange(
                    list_key,
                    start,
                    min(source_length - 1, start + page_size - 1),
                )
                if not rows:
                    raise RuntimeError("legacy evidence LIST ended before its checkpoint")

                session_id = str(progress["session_id"])
                safe_events = [json.loads(sanitize_buffer_entry(row)) for row in rows]
                canonical_events = _canonical_event_json(safe_events)
                batch_id = "legacy-" + hashlib.sha256(
                    f"{session_id}\0{run_id}\0{start}\0{canonical_events}".encode()
                ).hexdigest()
                batch_digest = hashlib.sha256(canonical_events.encode()).hexdigest()
                event_ids = [
                    _stable_event_id(session_id, batch_id, index)
                    for index in range(len(safe_events))
                ]
                tail = await pipe.xrevrange(stream_key, count=1)
                last_ms = int(_redis_text(tail[0][0]).split("-", 1)[0]) if tail else 0
                stream_id = f"{max(int(time.time() * 1000), last_ms + 1)}-0"
                dedupe_field = "batch:" + hashlib.sha256(
                    f"{session_id}\0{batch_id}".encode()
                ).hexdigest()
                receipt = _canonical_event_json({
                    "digest": batch_digest,
                    "count": len(safe_events),
                    "state": "accepted",
                    "stream_id": stream_id,
                })
                pipe.multi()
                pipe.xadd(stream_key, {
                    "batch_id": batch_id,
                    "batch_digest": batch_digest,
                    "event_count": str(len(safe_events)),
                    "session_id": session_id,
                    "run_id": run_id,
                    "events_json": _canonical_event_json(
                        [{**event, "run_id": run_id} for event in safe_events]
                    ),
                    "event_ids_json": _canonical_event_json(event_ids),
                    "trim_legacy": "0",
                }, id=stream_id)
                pipe.hset(dedupe_key, dedupe_field, receipt)
                pipe.sadd(f"{dedupe_key}:pending", *event_ids)
                pipe.sadd(f"{dedupe_key}:received", *event_ids)
                for outcome in ("passed", "failed", "skipped", "broken", "unknown"):
                    outcome_ids = [
                        event_ids[index]
                        for index, event in enumerate(safe_events)
                        if str(event.get("status") or "UNKNOWN").lower() == outcome
                    ]
                    if outcome_ids:
                        pipe.sadd(f"{dedupe_key}:{outcome}", *outcome_ids)
                progress["next_index"] = start + len(rows)
                pipe.set(progress_key, _canonical_event_json(progress))
                pipe.persist(list_key)
                pipe.persist(progress_key)
                pipe.persist(dedupe_key)
                await pipe.execute()
                migrated += len(rows)
    except WatchError as exc:
        raise RuntimeError(
            "legacy evidence LIST changed during migration; retry refused"
        ) from exc


async def remove_drained_legacy_redis_list(
    redis,
    list_key: str,
    stream_key: str,
    *,
    group_name: str,
) -> int:
    """Remove a migrated LIST only after the new consumer has drained it."""
    progress_key = _migration_progress_key(stream_key)
    run_id = stream_key.rsplit(":", 1)[-1]
    dedupe_key = LIVE_BATCH_DEDUP_KEY.format(run_id=run_id)
    try:
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.watch(
                list_key, stream_key, dedupe_key,
                f"{dedupe_key}:pending", progress_key,
            )
            if not await pipe.exists(list_key):
                pipe.multi()
                pipe.delete(progress_key)
                await pipe.execute()
                return 0
            source_length = int(await pipe.llen(list_key))
            progress = _parse_migration_progress(await pipe.get(progress_key))
            if (
                progress is None
                or progress["source_key"] != list_key
                or int(progress["source_length"]) != source_length
                or int(progress["next_index"]) != source_length
            ):
                raise RuntimeError("legacy evidence LIST migration is incomplete")

            groups = await pipe.xinfo_groups(stream_key)
            target = next(
                (
                    group
                    for group in groups
                    if _redis_text(_redis_field(group, "name")) == group_name
                ),
                None,
            )
            if target is None:
                raise RuntimeError("evidence persistence consumer group is missing")
            lag = _redis_field(target, "lag")
            if lag is None or int(lag) != 0:
                raise RuntimeError("migrated evidence has not been fully delivered")
            if int(_redis_field(target, "pending") or 0) != 0:
                raise RuntimeError("migrated evidence has not been acknowledged")
            outstanding = await pipe.scard(f"{dedupe_key}:pending")
            if int(outstanding or 0) != 0:
                raise RuntimeError("migrated evidence is still outstanding")

            pipe.multi()
            pipe.delete(list_key)
            pipe.delete(progress_key)
            pipe.expire(dedupe_key, 15 * 24 * 60 * 60)
            for suffix in ("pending", "passed", "failed", "skipped", "broken", "unknown", "received"):
                pipe.expire(f"{dedupe_key}:{suffix}", 15 * 24 * 60 * 60)
            results = await pipe.execute()
            return int(results[0] or 0)
    except WatchError as exc:
        raise RuntimeError(
            "evidence stream changed during legacy cleanup; retry refused"
        ) from exc


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
