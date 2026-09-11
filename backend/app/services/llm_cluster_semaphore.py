"""Cluster-wide bound on concurrent LLM calls (re-audit M12).

The analysis stage bounds its fan-out with an ``asyncio.Semaphore``: one per
process. Every API and worker pod runs several processes, so the model server
(one GPU behind Ollama, or a rate-limited hosted API) saw
``pods x processes x LLM_MAX_CONCURRENT_ANALYSES`` calls at once, and the
number grew with every replica the HPA added.

This is the cluster bound, held in Redis:

* Each slot is a member of a sorted set, scored by its lease expiry, taken
  from the Redis server's own clock (``TIME``) so pods with skewed clocks
  agree on what has expired.
* Acquire is one Lua script: drop expired leases, then admit if fewer than
  ``limit`` remain. Atomic, so two waiters cannot both take the last slot.
* A holder renews its lease while its call runs; release removes it. A holder
  that crashes stops renewing, and its slot frees itself when the lease lapses
  -- a dead pod cannot leak capacity forever.
* A LIVE holder that cannot keep its lease (renew says the lease is gone, or
  renewals keep failing until the next one would land after the lease
  lapses) is stopped: its call is cancelled and the block raises
  :class:`LLMSlotLost`. Otherwise a second holder is admitted when the lease
  lapses while the first is still calling, and the bound is passed
  (QA-B45-A5). Bounded: a holder stops at most one renew interval
  (lease / 3) before its lease could have lapsed.

The per-process semaphore stays as the local bound. When Redis is unreachable
the cluster bound is skipped with a warning, and the local bound is what
remains: this protects a model server's capacity, not money or data, so an
outage of the coordinator should degrade throughput control rather than stop
all analysis (compare ``llm_cost_reservation``, which fails closed).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any, AsyncIterator, Optional

import structlog

logger = structlog.get_logger("services.llm_cluster_semaphore")

#: Redis key prefix; tests point it at a unique namespace.
KEY_PREFIX = "tl:llm:slots"

# KEYS[1] zset; ARGV: token, lease_seconds, limit. 1 = held, 0 = full.
_ACQUIRE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local lease = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], now + lease, ARGV[1])
  return 1
end
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], now + lease, ARGV[1])
  redis.call('EXPIRE', KEYS[1], math.ceil(lease * 2))
  return 1
end
return 0
"""

# KEYS[1] zset; ARGV: token, lease_seconds. 1 = renewed, 0 = lease lost.
_RENEW_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local lease = tonumber(ARGV[2])
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if score and tonumber(score) > now then
  redis.call('ZADD', KEYS[1], now + lease, ARGV[1])
  redis.call('EXPIRE', KEYS[1], math.ceil(lease * 2))
  return 1
end
return 0
"""


class LLMSlotTimeout(TimeoutError):
    """No cluster slot became free within the wait budget."""


class LLMSlotLost(RuntimeError):
    """The slot's lease could not be kept, so the call holding it was stopped.

    Not a "no request was sent" failure: the call was in flight.
    """


class ClusterSemaphore:
    def __init__(
        self,
        name: str,
        limit: int,
        *,
        lease_seconds: float,
        redis: Any = None,
        poll_interval: float = 0.05,
        max_poll_interval: float = 0.5,
    ) -> None:
        self.key = f"{KEY_PREFIX}:{name}"
        self.limit = int(limit)
        self.lease_seconds = float(lease_seconds)
        self._redis = redis
        self._poll = poll_interval
        self._max_poll = max_poll_interval

    def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        from app.db.redis_client import get_redis

        return get_redis()

    async def try_acquire(self, token: str) -> bool:
        result = await self._client().eval(
            _ACQUIRE_LUA, 1, self.key, token, repr(self.lease_seconds), self.limit,
        )
        return int(result) == 1

    async def renew(self, token: str) -> bool:
        result = await self._client().eval(
            _RENEW_LUA, 1, self.key, token, repr(self.lease_seconds),
        )
        return int(result) == 1

    async def release(self, token: str) -> None:
        await self._client().zrem(self.key, token)

    async def _heartbeat(self, token: str, owner: Optional[asyncio.Task], lost: asyncio.Event) -> None:
        interval = max(0.05, self.lease_seconds / 3.0)
        last_renewed = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            try:
                renewed: Optional[bool] = await self.renew(token)
            except Exception as exc:  # noqa: BLE001 -- judged against the lease below
                logger.warning("llm_cluster_slot_renew_failed", key=self.key, error=str(exc)[:200])
                renewed = None
            if renewed:
                last_renewed = time.monotonic()
                continue
            # False: Redis says the lease is gone. None: renewal failed; keep
            # trying only while the NEXT attempt still lands inside the lease.
            if renewed is False or time.monotonic() + interval - last_renewed >= self.lease_seconds:
                logger.warning(
                    "llm_cluster_slot_lease_lost",
                    key=self.key,
                    reason="lease gone" if renewed is False else "renewal failing",
                )
                lost.set()
                if owner is not None:
                    owner.cancel()
                return

    @contextlib.asynccontextmanager
    async def slot(self, *, timeout: float) -> AsyncIterator[bool]:
        """Hold one cluster slot for the duration of the block.

        Yields ``True`` when a slot is held, ``False`` when the coordinator
        was unreachable and only the local bound applies. Raises
        :class:`LLMSlotTimeout` when the cluster is full for ``timeout`` s.
        """
        token = uuid.uuid4().hex
        deadline = time.monotonic() + max(0.0, float(timeout))
        delay = self._poll
        held = False
        while True:
            try:
                held = await self.try_acquire(token)
            except Exception as exc:  # noqa: BLE001 -- degrade to the local bound
                logger.warning("llm_cluster_semaphore_unavailable", key=self.key, error=str(exc)[:200])
                held = False
                break
            if held:
                break
            if time.monotonic() >= deadline:
                raise LLMSlotTimeout(
                    f"no LLM slot free within {timeout:.0f}s ({self.limit} in use cluster-wide)"
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_poll)

        owner = asyncio.current_task()
        lost = asyncio.Event()
        heartbeat: Optional[asyncio.Task] = (
            asyncio.create_task(self._heartbeat(token, owner, lost)) if held else None
        )
        try:
            yield held
        except asyncio.CancelledError:
            if not lost.is_set():
                raise  # an ordinary cancellation from outside
            if owner is not None:
                owner.uncancel()  # the heartbeat's cancel, consumed here
            raise LLMSlotLost(
                f"the LLM slot lease on {self.key} could not be kept; the call was stopped "
                "so the cluster bound holds"
            ) from None
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat
            if held:
                try:
                    await self.release(token)
                except Exception as exc:  # noqa: BLE001 -- the lease frees it
                    logger.warning("llm_cluster_slot_release_failed", key=self.key, error=str(exc)[:200])


@contextlib.asynccontextmanager
async def cluster_llm_slot(provider: str) -> AsyncIterator[bool]:
    """The configured cluster bound for ``provider``'s calls."""
    from app.core.config import settings

    limit = int(settings.LLM_CLUSTER_MAX_CONCURRENT or 0)
    if limit <= 0:
        yield False
        return
    semaphore = ClusterSemaphore(
        str(provider or "unknown").lower(),
        limit,
        lease_seconds=float(settings.LLM_CLUSTER_SLOT_LEASE_SECONDS or 60),
    )
    async with semaphore.slot(timeout=float(settings.AI_TIMEOUT_SECONDS or 300)) as held:
        yield held
