"""The virtual clock the lease tests race on is itself checked here.

A harness that quietly fell back to real time would make those tests pass for
the wrong reason -- and go flaky again on a loaded machine without anyone
noticing which half broke.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from tests.virtual_clock import VirtualClockLoop, VirtualClockPolicy


@pytest.fixture
def event_loop_policy():
    return VirtualClockPolicy()


async def test_the_tests_run_on_the_virtual_loop():
    assert isinstance(asyncio.get_running_loop(), VirtualClockLoop)


async def test_each_test_starts_at_zero():
    assert asyncio.get_running_loop().time() == 0.0


async def test_a_long_sleep_costs_no_real_time():
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    await asyncio.sleep(30.0)
    assert loop.time() == pytest.approx(30.0)
    assert time.monotonic() - started < 2.0


async def test_wait_for_is_counted_on_the_same_clock():
    loop = asyncio.get_running_loop()

    async def never() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(never(), timeout=1.5)
    assert loop.time() == pytest.approx(1.5)


async def test_everything_due_at_one_instant_runs_before_the_clock_moves():
    """The property the lease tests lean on: an unwinding chain of callbacks
    takes zero virtual time, however many hops it is."""
    loop = asyncio.get_running_loop()
    at: list[float] = []

    async def hop(depth: int) -> None:
        for _ in range(depth):
            await asyncio.sleep(0)  # a full trip through the ready queue
        at.append(loop.time())

    await asyncio.gather(hop(1), hop(25), hop(200))
    assert at == [0.0, 0.0, 0.0]


async def test_a_cancelled_timer_does_not_stall_the_clock():
    loop = asyncio.get_running_loop()
    doomed = asyncio.create_task(asyncio.sleep(0.5))
    await asyncio.sleep(0)
    doomed.cancel()
    started = time.monotonic()
    await asyncio.sleep(10.0)
    assert loop.time() == pytest.approx(10.0)
    assert time.monotonic() - started < 2.0


async def test_timers_still_fire_in_order():
    loop = asyncio.get_running_loop()
    fired: list[tuple[str, float]] = []

    async def at(label: str, when: float) -> None:
        await asyncio.sleep(when)
        fired.append((label, loop.time()))

    await asyncio.gather(at("c", 0.3), at("a", 0.1), at("b", 0.2))
    assert [label for label, _ in fired] == ["a", "b", "c"]
    assert [pytest.approx(when) for _, when in fired] == [0.1, 0.2, 0.3]
