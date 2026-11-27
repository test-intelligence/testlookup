"""Finite scrape work with explicit failed/stale observation state."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.metrics import (
    metric_collection_errors_total,
    metric_collection_last_success,
    metric_collection_success,
)

logger = structlog.get_logger(__name__)
COLLECTION_TIMEOUT_SECONDS = 1.0
COLLECTORS = ("queues", "agents", "downstream", "redis", "nightly", "digests")


@dataclass
class _Attempt:
    task: asyncio.Task[bool]
    deadline: float
    abandoned: bool = False
    recorded: bool = False
    success: bool = False


_IN_FLIGHT: dict[str, _Attempt] = {}
for _collector in COLLECTORS:
    metric_collection_success.labels(collector=_collector)
    metric_collection_last_success.labels(collector=_collector)
    metric_collection_errors_total.labels(collector=_collector)


async def collect_bounded(
    collector: str, refresh: Callable[[], Coroutine[Any, Any, bool]],
) -> bool:
    """Keep exposition available; failed reads never advance freshness."""
    if collector not in COLLECTORS:
        raise ValueError("unknown metric collector")
    # wait_for waits for cancellation cleanup, which can itself block on a
    # failed database connection. Bound the foreground wait instead. Share
    # healthy work across overlapping scrapers, and retain timed-out work
    # until it really ends so abandoned reads cannot pile up.
    loop = asyncio.get_running_loop()
    attempt = _IN_FLIGHT.get(collector)
    if attempt is None:
        attempt = _Attempt(asyncio.create_task(refresh()), loop.time() + COLLECTION_TIMEOUT_SECONDS)
        _IN_FLIGHT[collector] = attempt

        def record(success: bool, error_type: str | None = None) -> None:
            if attempt.recorded:
                return
            attempt.recorded = True
            attempt.success = success
            metric_collection_success.labels(collector=collector).set(int(success))
            if success:
                metric_collection_last_success.labels(collector=collector).set(time.time())
            else:
                metric_collection_errors_total.labels(collector=collector).inc()
                logger.warning(
                    "metric_collection_failed", collector=collector,
                    error_type=error_type or "IncompleteCollection",
                )

        def expire() -> None:
            if not attempt.task.done() and not attempt.abandoned:
                attempt.abandoned = True
                record(False, "TimeoutError")
                attempt.task.cancel()

        deadline_handle = loop.call_at(attempt.deadline, expire)

        def completed(task: asyncio.Task[bool]) -> None:
            deadline_handle.cancel()
            error = None if task.cancelled() else task.exception()
            record(
                not attempt.abandoned and not task.cancelled() and error is None and task.result() is True,
                type(error).__name__ if error else None,
            )
            if _IN_FLIGHT.get(collector) is attempt:
                _IN_FLIGHT.pop(collector)

        attempt.task.add_done_callback(completed)
    if attempt.abandoned:
        return False
    # Cancelling one HTTP request does not cancel a shared healthy read. Its
    # independent deadline still cancels it even if all callers disconnect.
    await asyncio.wait({attempt.task}, timeout=max(0, attempt.deadline - loop.time()))
    if not attempt.recorded:
        # Timer and task callbacks may be queued for the next event-loop turn.
        # Let those callbacks publish the single authoritative outcome.
        await asyncio.sleep(0)
    return attempt.recorded and attempt.success
