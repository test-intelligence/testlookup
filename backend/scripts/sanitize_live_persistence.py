"""One-time M11 cutover scrub for live evidence written before PR #1038.

Run with API and live-stream consumers paused. The operation is bounded and
idempotent, and can be rerun after interruption.
"""
from __future__ import annotations

import argparse
import asyncio

from app.db.mongo import Collections, close_mongo, get_mongo_db
from app.db.postgres import get_session_factory
from app.db.redis_client import close_redis, get_redis
from app.services.live_persistence_scrub import (
    migrate_redis_list_to_evidence_stream,
    purge_drained_live_stream,
    remove_drained_legacy_redis_list,
    scrub_mongo_live_events,
    scrub_postgres_archive_batch,
    scrub_postgres_test_case_batch,
    scrub_unconsumed_redis_stream,
)
from app.services.ingestion_sanitization import validate_live_identifier
from app.streams import (
    DLQ_STREAM,
    LIVE_EVENTS_STREAM,
    LIVE_EVIDENCE_GROUP,
    LIVE_EVIDENCE_STREAM_KEY,
    LIVE_GROUP,
    LIVE_TESTCASES_KEY,
)


async def _scrub_postgres(batch_size: int) -> tuple[int, int]:
    archive_total = 0
    archive_after_id = None
    session_factory = get_session_factory()
    async with session_factory() as db:
        while True:
            count, next_id = await scrub_postgres_archive_batch(
                db,
                after_id=archive_after_id,
                batch_size=batch_size,
            )
            if not count:
                break
            await db.commit()
            archive_total += count
            archive_after_id = next_id

        case_total = 0
        case_after_id = None
        while True:
            count, next_id = await scrub_postgres_test_case_batch(
                db,
                after_id=case_after_id,
                batch_size=batch_size,
            )
            if not count:
                break
            await db.commit()
            case_total += count
            case_after_id = next_id
    return archive_total, case_total


async def _scrub_mongo(batch_size: int) -> int:
    collection = get_mongo_db()[Collections.LIVE_EXECUTION_EVENTS]
    return await scrub_mongo_live_events(collection, batch_size=batch_size)


async def _scrub_chroma(batch_size: int) -> tuple[int, int]:
    from app.services.semantic_cache import purge_all_m11_semantic_cache_collections
    from app.services.semantic_search import rebuild_test_case_search_for_m11

    purged_cache_collections = await purge_all_m11_semantic_cache_collections()
    session_factory = get_session_factory()
    async with session_factory() as db:
        reindexed_rows = await rebuild_test_case_search_for_m11(
            db,
            batch_size=batch_size,
        )
    return reindexed_rows, purged_cache_collections


def _run_id_from_legacy_list_key(list_key: str) -> str:
    prefix, suffix = LIVE_TESTCASES_KEY.split("{run_id}", 1)
    if not list_key.startswith(prefix) or (suffix and not list_key.endswith(suffix)):
        raise RuntimeError(f"unexpected legacy live-evidence key: {list_key!r}")
    end = len(list_key) - len(suffix) if suffix else len(list_key)
    run_id = list_key[len(prefix):end]
    if not run_id:
        raise RuntimeError(f"legacy live-evidence key has no run ID: {list_key!r}")
    return validate_live_identifier("run_id", run_id)


async def _scrub_redis(
    batch_size: int,
    *,
    cleanup_drained_legacy_lists: bool = False,
    legacy_lists_only: bool = False,
) -> tuple[int, int]:
    redis = get_redis()
    if cleanup_drained_legacy_lists:
        removed_lists = 0
        list_pattern = LIVE_TESTCASES_KEY.format(run_id="*")
        # Redis SCAN may skip keys when the keyspace changes during iteration.
        # Repeat complete passes until one sees no remaining legacy LIST.
        while True:
            found_list = False
            async for raw_list_key in redis.scan_iter(
                match=list_pattern,
                count=batch_size,
            ):
                found_list = True
                list_key = (
                    raw_list_key.decode()
                    if isinstance(raw_list_key, bytes)
                    else str(raw_list_key)
                )
                run_id = _run_id_from_legacy_list_key(list_key)
                removed_lists += await remove_drained_legacy_redis_list(
                    redis,
                    list_key,
                    LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id),
                    group_name=LIVE_EVIDENCE_GROUP,
                )
            if not found_list:
                break
        return 0, removed_lists

    stream_rows = 0
    if not legacy_lists_only:
        stream_rows = await purge_drained_live_stream(
            redis,
            LIVE_EVENTS_STREAM,
            group_name=LIVE_GROUP,
        )
        stream_rows += await scrub_unconsumed_redis_stream(
            redis,
            DLQ_STREAM,
            batch_size=batch_size,
        )

    migrated_rows = 0
    list_pattern = LIVE_TESTCASES_KEY.format(run_id="*")
    # Creating evidence Streams and checkpoints mutates the keyspace while
    # SCAN runs. Repeat until a complete pass migrates no rows; that final,
    # read-only pass guarantees every frozen legacy LIST was visited.
    while True:
        pass_migrated = 0
        async for raw_list_key in redis.scan_iter(
            match=list_pattern,
            count=batch_size,
        ):
            list_key = (
                raw_list_key.decode()
                if isinstance(raw_list_key, bytes)
                else str(raw_list_key)
            )
            run_id = _run_id_from_legacy_list_key(list_key)
            pass_migrated += await migrate_redis_list_to_evidence_stream(
                redis,
                list_key,
                LIVE_EVIDENCE_STREAM_KEY.format(run_id=run_id),
                run_id=run_id,
                batch_size=batch_size,
            )
        migrated_rows += pass_migrated
        if pass_migrated == 0:
            break
    return stream_rows, migrated_rows


async def main(
    batch_size: int,
    *,
    cleanup_drained_legacy_lists: bool = False,
    migrate_legacy_lists_only: bool = False,
) -> None:
    try:
        if cleanup_drained_legacy_lists:
            _stream_rows, removed_lists = await _scrub_redis(
                batch_size,
                cleanup_drained_legacy_lists=True,
            )
            print(
                "Live evidence legacy cleanup complete: "
                f"redis_lists_removed={removed_lists}"
            )
            return
        if migrate_legacy_lists_only:
            _stream_rows, migrated_rows = await _scrub_redis(
                batch_size,
                legacy_lists_only=True,
            )
            print(
                "Live evidence legacy migration complete: "
                f"redis_evidence_rows_migrated={migrated_rows}"
            )
            return

        postgres_archives, postgres_cases = await _scrub_postgres(batch_size)
        mongo_rows = await _scrub_mongo(batch_size)
        chroma_rows, chroma_cache_collections = await _scrub_chroma(batch_size)
        redis_stream_rows, redis_migrated_rows = await _scrub_redis(batch_size)
        print(
            "M11 live persistence scrub complete: "
            f"postgres_archives={postgres_archives} "
            f"postgres_test_cases={postgres_cases} mongo_documents={mongo_rows} "
            f"chroma_search_rows={chroma_rows} "
            f"chroma_cache_collections={chroma_cache_collections} "
            f"redis_stream_rows={redis_stream_rows} "
            f"redis_evidence_rows_migrated={redis_migrated_rows}"
        )
    finally:
        await close_redis()
        await close_mongo()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument(
        "--confirm-live-writers-paused",
        action="store_true",
        help="required safety acknowledgement for Redis key replacement",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--migrate-legacy-live-lists-only",
        action="store_true",
        help="checkpoint-migrate legacy live LISTs without rerunning other M11 stores",
    )
    modes.add_argument(
        "--cleanup-drained-legacy-lists",
        action="store_true",
        help=(
            "remove migrated LISTs only after the live-persistence-v1 group "
            "has zero lag and zero pending entries"
        ),
    )
    args = parser.parse_args()
    if not args.confirm_live_writers_paused:
        parser.error("pause API/live consumers and pass --confirm-live-writers-paused")
    asyncio.run(
        main(
            max(1, args.batch_size),
            cleanup_drained_legacy_lists=args.cleanup_drained_legacy_lists,
            migrate_legacy_lists_only=args.migrate_legacy_live_lists_only,
        )
    )
