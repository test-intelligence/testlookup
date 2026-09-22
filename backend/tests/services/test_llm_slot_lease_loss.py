"""Re-audit QA-B45-A5: a holder that cannot keep its cluster-slot lease stops.

When renewal failed for longer than the lease, the lease lapsed while holder A
was still calling, holder B was admitted, and 2 calls ran under a limit of 1.
Now A's call is cancelled (``LLMSlotLost``) before its lease can lapse, so B
only ever runs after A has stopped.

The coordinator here is an in-memory stand-in with the same lease semantics
(acquire drops expired leases, renew fails for a lapsed one); the Redis
scripts themselves are exercised in
tests/integration/test_llm_cost_cap_and_slots_redis_postgres.py.

Every test in this file races two holders against a lease, so all of it runs
on a virtual clock (``tests/virtual_clock.py``): sleeps cost no real time and
the clock only moves when the loop has nothing left to run. The assertions
are therefore about the lease and nothing else -- a cancellation unwinds in
zero virtual time, so "A stopped before its lease lapsed" cannot turn on how
busy the machine is. Measured against the wall clock, the 15 ms margin these
tests turn on was smaller than a scheduler quantum on a loaded machine, and
this file failed the push gate and about one local run in three.
"""
from __future__ import annotations

import asyncio
from typing import NamedTuple

import pytest

from app.services.llm_cluster_semaphore import ClusterSemaphore, LLMSlotLost
from tests.virtual_clock import VirtualClockPolicy

LEASE = 0.3


@pytest.fixture
def event_loop_policy():
    return VirtualClockPolicy()


def _now() -> float:
    """The clock the semaphore measures its own deadlines on."""
    return asyncio.get_running_loop().time()


class _Leases:
    def __init__(self) -> None:
        self.expiry: dict[str, float] = {}

    def purge(self) -> None:
        now = _now()
        for token in [t for t, e in self.expiry.items() if e <= now]:
            del self.expiry[token]


class _MemorySemaphore(ClusterSemaphore):
    def __init__(self, leases: _Leases, *, renew: str = "ok") -> None:
        super().__init__("test", 1, lease_seconds=LEASE, redis=object(), poll_interval=0.01, max_poll_interval=0.02)
        self.leases = leases
        self.renew_mode = renew
        #: Every expiry the coordinator wrote for this holder. The last one is
        #: when its slot would free itself if it stopped renewing now.
        self.granted: list[float] = []

    def _grant(self, token: str) -> None:
        expiry = _now() + self.lease_seconds
        self.leases.expiry[token] = expiry
        self.granted.append(expiry)

    async def try_acquire(self, token: str) -> bool:
        self.leases.purge()
        if token in self.leases.expiry or len(self.leases.expiry) < self.limit:
            self._grant(token)
            return True
        return False

    async def renew(self, token: str) -> bool:
        if self.renew_mode == "raise":
            raise ConnectionError("redis blip")
        self.leases.purge()
        if self.renew_mode == "lost" or token not in self.leases.expiry:
            return False
        self._grant(token)
        return True

    async def release(self, token: str) -> None:
        self.leases.expiry.pop(token, None)


class _Race(NamedTuple):
    outcome: list[tuple[str, str]]
    peak: int
    order: list[str]
    #: What the clock read at each event in ``order``.
    at: dict[str, float]
    #: When the last lease A was granted would have lapsed on its own.
    a_lease_lapses_at: float


async def _race(renew_a: str = "ok", a_class: type = None) -> _Race:
    leases = _Leases()
    a_semaphore = (a_class or _MemorySemaphore)(leases, renew=renew_a)
    running: set[str] = set()
    peak = 0
    outcome: list[tuple[str, str]] = []
    order: list[str] = []
    at: dict[str, float] = {}

    def mark(event: str) -> None:
        order.append(event)
        at[event] = _now()

    async def holder(name: str, semaphore: ClusterSemaphore, seconds: float) -> None:
        nonlocal peak
        try:
            async with semaphore.slot(timeout=5):
                running.add(name)
                peak = max(peak, len(running))
                mark(f"{name} in")
                try:
                    await asyncio.sleep(seconds)
                finally:
                    running.discard(name)
                    mark(f"{name} out")
            outcome.append((name, "done"))
        except LLMSlotLost:
            outcome.append((name, "lost"))

    a = asyncio.create_task(holder("A", a_semaphore, 3.0))
    await asyncio.sleep(0.02)
    b = asyncio.create_task(holder("B", _MemorySemaphore(leases), 0.05))
    await asyncio.wait_for(asyncio.gather(a, b), timeout=10)
    return _Race(outcome, peak, order, at, a_semaphore.granted[-1])


def _assert_a_stopped_before_its_lease_could_lapse(race: _Race) -> None:
    """The property under test: the bound is never passed, and the reason is
    that A stops itself before the coordinator would drop it -- not that the
    two happened to fall in that order on this machine."""
    assert ("A", "lost") in race.outcome
    assert race.peak == 1, race.order
    assert race.order.index("A out") < race.order.index("B in"), race.order
    assert race.at["A out"] < race.a_lease_lapses_at, (race.at, race.a_lease_lapses_at)


@pytest.mark.parametrize("renew_a", ["raise", "lost"])
async def test_a_holder_that_cannot_keep_its_lease_stops_before_another_is_admitted(renew_a):
    race = await _race(renew_a)
    _assert_a_stopped_before_its_lease_could_lapse(race)
    assert ("B", "done") in race.outcome


async def test_a_holder_whose_renewals_succeed_keeps_its_slot_past_the_lease():
    leases = _Leases()
    semaphore = _MemorySemaphore(leases)
    async with semaphore.slot(timeout=1) as held:
        assert held is True
        await asyncio.sleep(LEASE * 3)
    assert leases.expiry == {}
    # It kept the slot by renewing, not because nothing was checking.
    assert len(semaphore.granted) > 1


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


@pytest.mark.parametrize("hang", [0.25, 0.5, 5.0])
async def test_a_renew_that_stalls_cannot_keep_a_holder_past_its_lease(hang):
    """QA-B45-R2-3: the lost-lease judgement ran only after the renew returned,
    so a renew hanging two intervals let the lease lapse under a live holder:
    B was admitted at 0.33 s while A ran to 0.38 s (0.25 s hang) or 0.61 s
    (0.5 s). The renew is now bounded by the time left on the lease."""
    race = await _race(a_class=_stalling(hang))
    _assert_a_stopped_before_its_lease_could_lapse(race)
    assert ("B", "done") in race.outcome
    # Bounded by the lease, not by the hang: a longer stall cannot stop A later.
    assert race.at["A out"] < LEASE


def test_the_stop_is_ordered_a_real_moment_before_the_lease_lapses():
    """The races above run on a virtual clock, where a cancellation unwinds in
    no time at all. In production it does not, and the margin is the real time
    the holder's call is given to stop before the coordinator would let a
    second one in. A virtual-time race cannot tell a 15 ms margin from none;
    this can, so the number itself is pinned here.
    """
    interval = max(0.05, LEASE / 3.0)
    margin = _MemorySemaphore(_Leases())._margin(interval)
    # Enough real time for a cancelled call to unwind...
    assert margin >= 0.01
    # ...and never so much that it eats the lease it is protecting, nor more
    # than half the gap between two attempts to renew.
    assert margin <= interval / 2
    assert margin < LEASE / 2
    # It scales with the lease, down to a floor no lease can push below.
    assert ClusterSemaphore("t", 1, lease_seconds=600)._margin(200) == pytest.approx(30.0)
    assert ClusterSemaphore("t", 1, lease_seconds=0.05)._margin(0.05) == 0.01


async def test_a_renew_whose_reply_is_slow_times_the_lease_from_the_send():
    """Redis extends the lease when the renew RUNS; a slow reply must not
    push the local deadline later. Renew #1 extends at once and replies
    0.15 s late; every renew after it fails. Timed from the reply, A would
    keep calling 0.15 s past the moment its lease lapses and B enters."""

    class SlowReplyThenDown(_MemorySemaphore):
        calls = 0

        async def renew(self, token):
            SlowReplyThenDown.calls += 1
            if SlowReplyThenDown.calls == 1:
                renewed = await super().renew(token)  # the server extends it now
                await asyncio.sleep(0.15)             # ...and the reply is slow
                return renewed
            raise ConnectionError("redis down")

    race = await _race(a_class=SlowReplyThenDown)
    _assert_a_stopped_before_its_lease_could_lapse(race)


async def test_a_slow_renew_that_lands_in_time_keeps_the_slot():
    leases = _Leases()
    async with _stalling(LEASE / 10)(leases).slot(timeout=1) as held:
        assert held is True
        await asyncio.sleep(LEASE * 3)  # several renew rounds, each slow but in time
    assert leases.expiry == {}


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
            seen.append(_now() - lease_from)
            return await super()._heartbeat(token, owner, lost, lease_from)

    async with Slow(_Leases()).slot(timeout=1):
        await asyncio.sleep(0.01)
    # The whole slow reply is already spent against the lease. Timed from the
    # reply instead, this would be 0.
    assert seen and seen[0] == pytest.approx(LEASE / 2)


def test_a_lease_one_redis_call_could_outlast_is_refused_at_config_load():
    from pydantic import ValidationError

    from app.core.config import REDIS_SOCKET_TIMEOUT_SECONDS, Settings

    floor = 2 * REDIS_SOCKET_TIMEOUT_SECONDS + 5
    with pytest.raises(ValidationError, match="LLM_CLUSTER_SLOT_LEASE_SECONDS"):
        Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=floor - 1)
    assert Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=floor).LLM_CLUSTER_SLOT_LEASE_SECONDS == floor
    assert Settings(LLM_CLUSTER_SLOT_LEASE_SECONDS=0).LLM_CLUSTER_SLOT_LEASE_SECONDS == 0  # the default


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
