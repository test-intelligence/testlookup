"""Read-only resource observations from the actual application and broker Redis."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import re

from app.core.metrics import (
    redis_memory_counted_for_evict_bytes,
    redis_memory_limit_configured,
    redis_memory_max_bytes,
    redis_memory_not_counted_for_evict_bytes,
    redis_memory_rss_bytes,
    redis_memory_used_bytes,
)

STORE_ROLES = ("application", "broker")


@dataclass(frozen=True)
class MemoryObservation:
    used: int
    maximum: int
    rss: int
    excluded_from_eviction: int


def _memory_observation(info: object) -> MemoryObservation:
    if not isinstance(info, Mapping):
        raise ValueError("Redis INFO memory did not return a mapping")

    def count(field: str) -> int:
        value = info.get(field)
        if isinstance(value, bool):
            raise ValueError("Redis INFO memory has an invalid field")
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            return int(value)
        # Missing fields/ACL-restricted views are unknown, never healthy zeros.
        raise ValueError("Redis INFO memory has a missing or invalid field")

    return MemoryObservation(
        used=count("used_memory"), maximum=count("maxmemory"),
        rss=count("used_memory_rss"), excluded_from_eviction=count("mem_not_counted_for_evict"),
    )


async def refresh_redis_resource_metrics() -> bool:
    """Publish both configured roles only after a complete validated observation.

    This collector uses INFO memory only: no CONFIG, key scans, payload reads,
    or mutations. collect_bounded("redis", ...) owns timeout/freshness. The
    existing client policy owns socket timeouts, credentials, TLS and retries.
    """
    from app.db.redis_client import get_broker_redis, get_redis

    clients = {"application": get_redis(), "broker": get_broker_redis()}
    unique = {id(client): client for client in clients.values()}

    async def read_memory(identity: int) -> object:
        # redis-py's command annotation permits a general Awaitable. Wrap it
        # in an actual coroutine before scheduling it in a TaskGroup.
        return await unique[identity].info(section="memory")

    # A shared client is queried once. Different logical databases can still
    # refer to one physical server: expose each role, never sum their memory.
    async with asyncio.TaskGroup() as group:
        reads = {
            identity: group.create_task(read_memory(identity))
            for identity in unique
        }
    observations = {
        role: _memory_observation(reads[id(client)].result())
        for role, client in clients.items()
    }
    for role, observation in observations.items():
        labels = {"store_role": role}
        redis_memory_used_bytes.labels(**labels).set(observation.used)
        redis_memory_max_bytes.labels(**labels).set(observation.maximum)
        redis_memory_rss_bytes.labels(**labels).set(observation.rss)
        redis_memory_not_counted_for_evict_bytes.labels(**labels).set(observation.excluded_from_eviction)
        redis_memory_counted_for_evict_bytes.labels(**labels).set(max(0, observation.used - observation.excluded_from_eviction))
        redis_memory_limit_configured.labels(**labels).set(int(observation.maximum > 0))
    return True
