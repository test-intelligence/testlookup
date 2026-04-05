"""Async Redis client with connection pooling."""
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings

_pool: Optional[aioredis.ConnectionPool] = None
_client: Optional[aioredis.Redis] = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=50,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    """Return a Redis client backed by the shared connection pool."""
    global _client
    if _client is None:
        _client = aioredis.Redis(connection_pool=get_redis_pool())
    return _client


async def close_redis() -> None:
    global _pool, _client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None
