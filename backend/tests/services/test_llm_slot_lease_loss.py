"""Re-audit QA-B45-A5: a holder that cannot keep its cluster-slot lease stops.

When renewal failed for longer than the lease, the lease lapsed while holder A
was still calling, holder B was admitted, and 2 calls ran under a limit of 1.
Now A's call is cancelled (``LLMSlotLost``) before its lease can lapse, so B
only ever runs after A has stopped.

The coordinator here is an in-memory stand-in with the same lease semantics
(acquire drops expired leases, renew fails for a lapsed one); the Redis
scripts themselves are exercised in
tests/integration/test_llm_cost_cap_and_slots_redis_postgres.py.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.llm_cluster_semaphore import ClusterSemaphore, LLMSlotLost

LEASE = 0.3


class _Leases:
    def __init__(self) -> None:
        self.expiry: dict[str, float] = {}

    def purge(self) -> None:
        now = time.monotonic()
        for token in [t for t, e in self.expiry.items() if e <= now]:
            del self.expiry[token]


class _MemorySemaphore(ClusterSemaphore):
    def __init__(self, leases: _Leases, *, renew: str = "ok") -> None:
        super().__init__("test", 1, lease_seconds=LEASE, redis=object(), poll_interval=0.01, max_poll_interval=0.02)
        self.leases = leases
        self.renew_mode = renew

    async def try_acquire(self, token: str) -> bool:
        self.leases.purge()
        if token in self.leases.expiry or len(self.leases.expiry) < self.limit:
            self.leases.expiry[token] = time.monotonic() + self.lease_seconds
            return True
        return False

    async def renew(self, token: str) -> bool:
        if self.renew_mode == "raise":
            raise ConnectionError("redis blip")
        self.leases.purge()
        if self.renew_mode == "lost" or token not in self.leases.expiry:
            return False
        self.leases.expiry[token] = time.monotonic() + self.lease_seconds
        return True

    async def release(self, token: str) -> None:
        self.leases.expiry.pop(token, None)


async def _race(renew_a: str) -> tuple[list, int, list[str]]:
    leases = _Leases()
    running: set[str] = set()
    peak = 0
    outcome: list = []
    order: list[str] = []

    async def holder(name: str, semaphore: ClusterSemaphore, seconds: float) -> None:
        nonlocal peak
        try:
            async with semaphore.slot(timeout=5):
                running.add(name)
                peak = max(peak, len(running))
                order.append(f"{name} in")
                try:
                    await asyncio.sleep(seconds)
                finally:
                    running.discard(name)
                    order.append(f"{name} out")
            outcome.append((name, "done"))
        except LLMSlotLost:
            outcome.append((name, "lost"))

    a = asyncio.create_task(holder("A", _MemorySemaphore(leases, renew=renew_a), 3.0))
    await asyncio.sleep(0.02)
    b = asyncio.create_task(holder("B", _MemorySemaphore(leases), 0.05))
    await asyncio.wait_for(asyncio.gather(a, b), timeout=10)
    return outcome, peak, order


@pytest.mark.asyncio
@pytest.mark.parametrize("renew_a", ["raise", "lost"])
async def test_a_holder_that_cannot_keep_its_lease_stops_before_another_is_admitted(renew_a):
    outcome, peak, order = await _race(renew_a)
    assert ("A", "lost") in outcome
    assert ("B", "done") in outcome
    assert peak == 1, order
    assert order.index("A out") < order.index("B in"), order


@pytest.mark.asyncio
async def test_a_holder_whose_renewals_succeed_keeps_its_slot_past_the_lease():
    leases = _Leases()
    async with _MemorySemaphore(leases).slot(timeout=1) as held:
        assert held is True
        await asyncio.sleep(LEASE * 3)
    assert leases.expiry == {}


@pytest.mark.asyncio
async def test_one_failed_renewal_inside_the_lease_is_tolerated():
    """A single blip must not stop a call whose lease is still valid."""

    class OneBlip(_MemorySemaphore):
        calls = 0

        async def renew(self, token):
            OneBlip.calls += 1
            if OneBlip.calls == 1:
                raise ConnectionError("one blip")
            return await super().renew(token)

    leases = _Leases()
    async with OneBlip(leases).slot(timeout=1):
        await asyncio.sleep(LEASE * 2)
    assert OneBlip.calls >= 2


@pytest.mark.asyncio
async def test_an_outside_cancellation_is_still_a_cancellation():
    leases = _Leases()

    async def hold():
        async with _MemorySemaphore(leases).slot(timeout=1):
            await asyncio.sleep(10)

    task = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert leases.expiry == {}
