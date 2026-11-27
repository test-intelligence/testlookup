"""Read-only logical queue depths, including Redis transport priority bins."""
from __future__ import annotations

from typing import Any

import structlog

from app.worker.queue_inventory import all_task_queue_names, redis_queue_keys

logger = structlog.get_logger(__name__)


async def read_celery_queue_depths() -> dict[str, int | None]:
    """One pipeline over the declared inventory; a failed bin makes that queue unknown.

    Counts cover broker-pending messages only, excluding worker-reserved and
    active deliveries. They do not establish task age, throughput, consumer
    availability or durable completion. Call within the scrape collector budget.
    Work is linear in the configured queue/priority count, not an absolute
    fixed-size bound; large shard configurations require profile sizing.
    """
    from app.db.redis_client import get_broker_redis

    queues = all_task_queue_names()
    unknown: dict[str, int | None] = dict.fromkeys(queues)
    try:
        keys = {queue: redis_queue_keys(queue) for queue in queues}
        redis = get_broker_redis()
        async with redis.pipeline(transaction=False) as pipeline:
            for physical_keys in keys.values():
                for key in physical_keys:
                    pipeline.llen(key)
            results: list[Any] = await pipeline.execute(raise_on_error=False)
        if len(results) != sum(len(value) for value in keys.values()):
            return unknown
        offset = 0
        for queue, physical_keys in keys.items():
            values = results[offset:offset + len(physical_keys)]
            offset += len(physical_keys)
            if all(type(value) is int and value >= 0 for value in values):
                unknown[queue] = sum(values)
        return unknown
    except Exception as exc:  # noqa: BLE001 -- never turn a failed read into zero
        logger.debug("celery_queue_depth_read_failed", error_type=type(exc).__name__)
        return unknown
