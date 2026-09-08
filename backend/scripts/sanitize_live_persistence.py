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
    purge_drained_live_stream,
    scrub_mongo_live_events,
    scrub_postgres_archive_batch,
    scrub_postgres_test_case_batch,
    scrub_redis_list,
    scrub_unconsumed_redis_stream,
)
from app.streams import DLQ_STREAM, LIVE_EVENTS_STREAM, LIVE_GROUP, LIVE_TESTCASES_KEY


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


async def _scrub_redis(batch_size: int) -> tuple[int, int]:
    redis = get_redis()
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

    list_rows = 0
    list_pattern = LIVE_TESTCASES_KEY.format(run_id="*")
    async for list_key in redis.scan_iter(match=list_pattern, count=batch_size):
        list_rows += await scrub_redis_list(
            redis,
            list_key,
            batch_size=batch_size,
        )
    return stream_rows, list_rows


async def main(batch_size: int) -> None:
    try:
        postgres_archives, postgres_cases = await _scrub_postgres(batch_size)
        mongo_rows = await _scrub_mongo(batch_size)
        chroma_rows, chroma_cache_collections = await _scrub_chroma(batch_size)
        redis_stream_rows, redis_list_rows = await _scrub_redis(batch_size)
        print(
            "M11 live persistence scrub complete: "
            f"postgres_archives={postgres_archives} "
            f"postgres_test_cases={postgres_cases} mongo_documents={mongo_rows} "
            f"chroma_search_rows={chroma_rows} "
            f"chroma_cache_collections={chroma_cache_collections} "
            f"redis_stream_rows={redis_stream_rows} redis_list_rows={redis_list_rows}"
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
    args = parser.parse_args()
    if not args.confirm_live_writers_paused:
        parser.error("pause API/live consumers and pass --confirm-live-writers-paused")
    asyncio.run(main(max(1, args.batch_size)))
