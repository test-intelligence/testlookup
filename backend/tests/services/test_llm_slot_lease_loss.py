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


async def _race(renew_a: str = "ok", a_class: type = None) -> tuple[list, int, list[str]]:
    leases = _Leases()
    a_semaphore = (a_class or _MemorySemaphore)(leases, renew=renew_a)
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

    a = asyncio.create_task(holder("A", a_semaphore, 3.0))
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


def _stalling(hang: float) -> type:
    class Stalls(_MemorySemaphore):
        """A renew that itself hangs (a slow Redis, a hung socket)."""

        async def renew(self, token):
            await asyncio.sleep(hang)
            return await super().renew(token)

    return Stalls


@pytest.mark.asyncio
@pytest.mark.parametrize("hang", [0.25, 0.5, 5.0])
async def test_a_renew_that_stalls_cannot_keep_a_holder_past_its_lease(hang):
    """QA-B45-R2-3: the lost-lease judgement ran only after the renew returned,
    so a renew hanging two intervals let the lease lapse under a live holder:
    B was admitted at 0.33 s while A ran to 0.38 s (0.25 s hang) or 0.61 s
    (0.5 s). The renew is now bounded by the time left on the lease."""
    outcome, peak, order = await _race(a_class=_stalling(hang))
    assert ("A", "lost") in outcome
    assert ("B", "done") in outcome
    assert peak == 1, order
    assert order.index("A out") < order.index("B in"), order


@pytest.mark.asyncio
async def test_a_slow_renew_that_lands_in_time_keeps_the_slot():
    leases = _Leases()
    async with _stalling(LEASE / 10)(leases).slot(timeout=1) as held:
        assert held is True
        await asyncio.sleep(LEASE * 3)  # several renew rounds, each slow but in time
    assert leases.expiry == {}


@pytest.mark.asyncio
async def test_the_lease_is_timed_from_when_the_acquire_was_sent():
    """The server set the lease when the acquire ran, before it returned: a
    slow acquire reply must not stretch the holder's local deadline."""
    seen: list[float] = []

    class Slow(_MemorySemaphore):
        async def try_acquire(self, token):
            held = await super().try_acquire(token)
            await asyncio.sleep(LEASE / 2)  # the reply is slow
            return held

        async def _heartbeat(self, token, owner, lost, lease_from=None):
            seen.append(time.monotonic() - lease_from)
            return await super()._heartbeat(token, owner, lost, lease_from)

    async with Slow(_Leases()).slot(timeout=1):
        await asyncio.sleep(0.01)
    assert seen and seen[0] >= LEASE / 2 * 0.9


def test_a_lease_one_redis_call_could_outlast_is_refused_at_config_load():
    from pydantic import ValidationError

    from app.core.config import REDIS_SOCKET_TIMEOUT_SECONDS, Settings

    floor = 2 * REDIS_SOCKET_TIMEOUT_SECONDS + 5
    with pytest.raises(ValidationError, match="LLM_CLUSTER_SLOT_LEASE_SECONDS"):
        Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=floor - 1)
    assert Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=floor).LLM_CLUSTER_SLOT_LEASE_SECONDS == floor
    assert Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=0).LLM_CLUSTER_SLOT_LEASE_SECONDS == 0  # the default


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
