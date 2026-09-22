"""An event loop whose clock moves only when it has nothing left to run.

Tests that race two coroutines against a lease used the wall clock: real
``asyncio.sleep`` calls, a real ``time.monotonic`` deadline, and assertions
about which of the two got somewhere first. On a loaded machine those flip
for reasons that are not bugs -- a 15 ms cancellation window is nothing next
to a Windows scheduler quantum under a parallel build -- and the suite goes
flaky without anything being wrong.

This loop keeps the same code paths and takes the machine out of the
measurement. Its ``time()`` is a number the loop owns. Whenever the ready
queue drains, it jumps that number straight to the next timer that is due,
so:

* every callback due at one instant runs before the clock moves at all --
  a cancellation unwinds in zero virtual time, exactly as a test that means
  "before the lease lapses" assumes;
* a ``sleep(5)`` costs nothing, so a test can cover a lease several times
  over without taking seconds;
* how long the real machine takes to run a callback cannot change the
  order of anything.

The code under test has to read the same clock. ``asyncio.sleep`` and
``asyncio.wait_for`` already do -- they are counted on ``loop.time()`` -- so
what is left is anything computing a deadline of its own: see
``ClusterSemaphore._now``.

Usage, per test module::

    from tests.virtual_clock import VirtualClockPolicy

    @pytest.fixture
    def event_loop_policy():
        return VirtualClockPolicy()

pytest-asyncio builds each test's loop from that policy. The fixture is
function-scoped, so every test starts at 0.0 with a loop of its own.

Not for tests that touch a real socket, a thread pool or a subprocess: those
wait on the outside world, which does not know about this clock.
"""
from __future__ import annotations

import asyncio
import heapq


def _default_loop_class() -> type:
    """The loop class this platform would have used anyway.

    Proactor on Windows, selector elsewhere -- subclassed rather than named,
    so the tests run on the same machinery as the rest of the suite.
    """
    loop = asyncio.new_event_loop()
    try:
        return type(loop)
    finally:
        loop.close()


class VirtualClockLoop(_default_loop_class()):  # type: ignore[misc]
    """A loop that reports virtual time and advances it only when idle."""

    def __init__(self, *args, **kwargs) -> None:
        self._virtual_now = 0.0
        super().__init__(*args, **kwargs)

    def time(self) -> float:
        return self._virtual_now

    def _run_once(self) -> None:
        # Only when nothing is ready: everything due at this instant must run
        # before the clock moves, or "A stopped before its lease lapsed"
        # would depend on how many callbacks the unwinding takes.
        if not self._ready:
            # Cancelled timers are dropped first; jumping to one of those
            # would leave the real selector waiting out the difference.
            while self._scheduled and self._scheduled[0]._cancelled:
                self._scheduled[0]._scheduled = False
                heapq.heappop(self._scheduled)
            if self._scheduled:
                self._virtual_now = max(self._virtual_now, self._scheduled[0]._when)
        super()._run_once()


class VirtualClockPolicy(asyncio.DefaultEventLoopPolicy):
    """Hands pytest-asyncio a :class:`VirtualClockLoop` per test."""

    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        return VirtualClockLoop()
