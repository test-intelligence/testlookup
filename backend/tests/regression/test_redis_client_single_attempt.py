"""One Redis call is one attempt, bounded by the app's socket timeout (W47, redis-py 8).

The LLM slot lease floor is ``2 x REDIS_SOCKET_TIMEOUT_SECONDS + 5`` (app.core.config): it assumes one Redis call --
a slot renew, say -- fails within that timeout. redis-py 8 changed its defaults: a client built with the constructor
(``Redis(host=...)``) retries a command that times out or loses its connection up to 10 times, with exponential
backoff, and every connection now defaults to a 5 s socket timeout (5.2.1: none). Against a hung Redis each attempt
waits the full socket timeout, so a retrying renew could outlive the lease while its caller still believes it holds
the slot. app.db.redis_client states the policy for every client the app builds, whatever the construction style.

These tests drive the real redis-py command path against a "hung" server: the connection opens, the command is
sent, and reading the reply times out. They count how many times the reply was awaited.
"""
from __future__ import annotations

import pytest
import redis
import redis.asyncio as aioredis
import redis.asyncio.connection as async_connection
import redis.connection as sync_connection

from app.core.config import REDIS_SOCKET_TIMEOUT_SECONDS
from app.db import redis_client

UNUSED_URL = "redis://127.0.0.1:6399/0"


def _hang_sync(monkeypatch) -> list[int]:
    reads = [0]

    def read_response(self, *args, **kwargs):
        reads[0] += 1
        raise redis.exceptions.TimeoutError("Timeout reading from socket")

    monkeypatch.setattr(sync_connection.Connection, "connect", lambda self: None)
    monkeypatch.setattr(sync_connection.Connection, "can_read", lambda self, timeout=0: False)
    monkeypatch.setattr(sync_connection.Connection, "send_packed_command", lambda self, *a, **k: None)
    monkeypatch.setattr(sync_connection.Connection, "read_response", read_response)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)  # the library's backoff, if any
    return reads


def _hang_async(monkeypatch) -> list[int]:
    reads = [0]

    async def nothing(self, *args, **kwargs):
        return None

    async def not_readable(self, *args, **kwargs):
        return False

    async def read_response(self, *args, **kwargs):
        reads[0] += 1
        raise redis.exceptions.TimeoutError("Timeout reading from socket")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(async_connection.Connection, "connect", nothing)
    monkeypatch.setattr(async_connection.Connection, "can_read", not_readable)
    monkeypatch.setattr(async_connection.Connection, "send_packed_command", nothing)
    monkeypatch.setattr(async_connection.Connection, "read_response", read_response)
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    return reads


async def test_the_shared_pool_awaits_one_reply_per_call(monkeypatch):
    monkeypatch.setattr(redis_client, "_pool", None)
    monkeypatch.setattr(redis_client, "_client", None)
    monkeypatch.setattr(redis_client.settings, "REDIS_URL", UNUSED_URL)
    reads = _hang_async(monkeypatch)
    client = redis_client.get_redis()
    try:
        with pytest.raises(redis.exceptions.TimeoutError):
            await client.get("w47:any")
    finally:
        await redis_client.close_redis()

    assert reads[0] == 1


def test_the_shared_pool_names_its_timeouts_size_and_retry_policy(monkeypatch):
    monkeypatch.setattr(redis_client, "_pool", None)
    monkeypatch.setattr(redis_client.settings, "REDIS_URL", UNUSED_URL)
    pool = redis_client.get_redis_pool()
    kwargs = pool.connection_kwargs
    monkeypatch.setattr(redis_client, "_pool", None)

    assert kwargs["socket_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS
    assert kwargs["socket_connect_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS
    assert isinstance(kwargs["retry"], aioredis.retry.Retry)  # redis.asyncio needs its own (awaitable) Retry
    assert kwargs["retry"].get_retries() == 0
    assert pool.max_connections == 50


def test_the_sync_client_awaits_one_reply_per_call(monkeypatch):
    reads = _hang_sync(monkeypatch)
    client = redis_client.sync_redis_from_url(UNUSED_URL)
    try:
        with pytest.raises(redis.exceptions.TimeoutError):
            client.get("w47:any")
    finally:
        client.close()

    assert reads[0] == 1
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS
    assert kwargs["socket_connect_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS


def test_a_constructor_built_redis_py_8_client_retries_a_timed_out_reply(monkeypatch):
    """Why the policy is stated rather than inherited: a client built with ``Redis(host=..., port=...)`` gets redis-py
    8's 10-retry default and awaits the reply again and again, while ``Redis.from_url`` gets the connection default
    (no retry). One constructor style away from a lease that can be outlived. If this ever fails, the library changed
    its default once more; the app's own policy above still holds."""
    reads = _hang_sync(monkeypatch)
    client = redis.Redis(host="127.0.0.1", port=6399)
    try:
        with pytest.raises(redis.exceptions.TimeoutError):
            client.get("w47:any")
    finally:
        client.close()

    assert reads[0] > 1
