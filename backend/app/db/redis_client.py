"""Redis clients with connection pooling, and the policy every Redis client the app builds shares."""
from typing import Any, Optional

import redis
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import NoBackoff
from redis.retry import Retry as SyncRetry

from app.core.config import REDIS_SOCKET_TIMEOUT_SECONDS, settings

_pool: Optional[aioredis.ConnectionPool] = None
_client: Optional[aioredis.Redis] = None


def _single_attempt_options(retry_class: type) -> dict[str, Any]:
    """Every client's timeouts and retry policy: one attempt, bounded by REDIS_SOCKET_TIMEOUT_SECONDS.

    The LLM slot lease floor (2 x the socket timeout + 5 s, app.core.config) assumes one Redis call -- a slot renew,
    say -- fails within that timeout. redis-py 8 changed the defaults (W47): a client built with ``Redis(host=...)``
    retries a timed-out command up to 10 times with backoff, while ``from_url`` and an explicit pool do not, and every
    connection defaults to a 5 s timeout. So the policy is stated here rather than inherited from the library.
    """
    return {
        "socket_timeout": REDIS_SOCKET_TIMEOUT_SECONDS,
        "socket_connect_timeout": REDIS_SOCKET_TIMEOUT_SECONDS,
        "retry": retry_class(NoBackoff(), 0),
    }


def get_redis_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=50,
            decode_responses=True,
            **_single_attempt_options(AsyncRetry),
        )
    return _pool


def get_redis() -> aioredis.Redis:
    """Return a Redis client backed by the shared connection pool."""
    global _client
    if _client is None:
        _client = aioredis.Redis(connection_pool=get_redis_pool())
    return _client


def sync_redis_from_url(url: str) -> redis.Redis:
    """A synchronous client with the same timeouts and single-attempt policy (semantic search's index cursor)."""
    return redis.Redis.from_url(url, **_single_attempt_options(SyncRetry))


async def close_redis() -> None:
    global _pool, _client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None
