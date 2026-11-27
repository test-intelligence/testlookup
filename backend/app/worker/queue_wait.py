"""Dispatch-ready delay observed when a broker-delivered task starts.

The stamp precedes publication I/O, so the measurement includes that I/O,
broker waiting and worker reservation. Scheduled ETA delay is excluded. It
describes a delivery attempt, and does not establish a durable business result
or the age of tasks that have not started. Synchronized wall clocks are required.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import time
from typing import Any

from app.core.metric_dimensions import TASK_QUEUE_WAIT_UNAVAILABLE_REASONS, task_queue_family

PUBLISHED_AT_HEADER = "testlookup_published_at_ms"
WAIT_UNAVAILABLE_REASONS = TASK_QUEUE_WAIT_UNAVAILABLE_REASONS
_ACTIVE = "_testlookup_queue_wait_observed"
_MISSING = object()
_MAX_EPOCH_MS = 253_402_300_799_999  # Last millisecond in UTC year 9999, below 2**53.


def stamp_publish_time(*, headers: Any = None, **_kwargs: Any) -> None:
    """Stamp every publication, including retries, without depending on tracing."""
    if not isinstance(headers, dict):
        return
    # Never reuse a previous attempt's reserved timestamp when a local clock
    # read fails. All unrelated business headers remain untouched.
    headers.pop(PUBLISHED_AT_HEADER, None)
    try:
        published = time.time_ns() // 1_000_000
        if type(published) is int and 0 < published <= _MAX_EPOCH_MS:
            headers[PUBLISHED_AT_HEADER] = published
    except Exception:  # noqa: BLE001 — telemetry must preserve publication behavior
        pass


def wait_observation(published: object = _MISSING, eta: object = None, *, now_ms: int) -> tuple[float | None, str | None]:
    """Return one valid delay or one closed unavailable reason, never a fake zero."""
    if published is _MISSING:
        return None, "missing_stamp"
    if type(published) is not int or not 0 < published <= _MAX_EPOCH_MS:
        return None, "invalid_stamp"
    if type(now_ms) is not int or not 0 < now_ms <= _MAX_EPOCH_MS or published > now_ms:
        return None, "clock_skew"
    ready_at = published
    if eta is not None:
        try:
            if isinstance(eta, str):
                scheduled = datetime.fromisoformat(eta.replace("Z", "+00:00"))
            elif isinstance(eta, datetime):
                scheduled = eta
            else:
                return None, "invalid_eta"
            if scheduled.tzinfo is None or scheduled.utcoffset() is None:
                return None, "invalid_eta"
            eta_ms = int(scheduled.timestamp() * 1000)
            ready_at = max(published, eta_ms)
        except (ValueError, TypeError, OverflowError, OSError):
            return None, "invalid_eta"
    if ready_at > now_ms:
        return None, "clock_skew"
    return (now_ms - ready_at) / 1000.0, None


def observe_queue_wait(*, task: Any = None, **_kwargs: Any) -> None:
    """Observe each real execution start once, excluding local eager calls."""
    request = getattr(task, "request", None)
    if request is None or getattr(request, "is_eager", False) or getattr(request, _ACTIVE, False):
        return
    try:
        setattr(request, _ACTIVE, True)
        headers = getattr(request, "headers", None)
        published = headers.get(PUBLISHED_AT_HEADER, _MISSING) if isinstance(headers, Mapping) else _MISSING
        delivery = getattr(request, "delivery_info", None)
        queue = task_queue_family(delivery.get("routing_key") if isinstance(delivery, Mapping) else None)
        seconds, reason = wait_observation(published, getattr(request, "eta", None), now_ms=time.time_ns() // 1_000_000)
        from app.core.metrics import celery_task_queue_wait_seconds, celery_task_queue_wait_unavailable_total

        if reason is None:
            assert seconds is not None
            celery_task_queue_wait_seconds.labels(queue_name=queue).observe(seconds)
        else:
            celery_task_queue_wait_unavailable_total.labels(queue_name=queue, reason=reason).inc()
    except Exception:  # noqa: BLE001 — counters and clocks cannot fail task execution
        pass


def finish_queue_wait(*, task: Any = None, **_kwargs: Any) -> None:
    """Release the attempt marker for retries or reused test/request contexts."""
    request = getattr(task, "request", None)
    try:
        if request is not None and hasattr(request, _ACTIVE):
            delattr(request, _ACTIVE)
    except Exception:  # noqa: BLE001 — preserve the task's outcome
        pass
