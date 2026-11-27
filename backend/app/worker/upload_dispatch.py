"""Bound synchronous upload publication without discarding ambiguous work.

The upload status endpoint, rather than Celery's result backend, reports the
business outcome. A publish error or request timeout cannot establish whether
the broker accepted the message. Callers must retain its object and status
seed, including when a timed-out publication finishes later.

This is not a durable dispatch outbox: API exit can interrupt publication and
unconfirmed uploads still need reconciliation. The finite executor and slots
bound outstanding publications even when broker calls outlive their callers.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any


UPLOAD_PUBLISH_TIMEOUT_SECONDS = 3.0
_MAX_PENDING_PUBLISHES = 4
_PUBLISH_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_PENDING_PUBLISHES, thread_name_prefix="upload-publish"
)
_PUBLISH_SLOTS = threading.BoundedSemaphore(_MAX_PENDING_PUBLISHES)


class UploadPublisherBusy(RuntimeError):
    """All bounded publisher slots are still occupied; no call was submitted."""


def _consume_result(future: asyncio.Future[Any]) -> None:
    # A shielded publication can fail after its HTTP caller has timed out or
    # disconnected. Retrieve that exception without an unhandled-future warning;
    # the caller has already reported the unconfirmed dispatch with its task ID.
    if not future.cancelled():
        future.exception()


async def publish_upload(
    task: Any, *, kwargs: dict[str, Any], task_id: str
) -> None:
    """Wait a bounded time for publication, leaving late work running safely.

    No automatic publish retry is requested: an error can follow broker
    acceptance. Avoid the result-backend subscription entirely; upload workers
    write the dedicated, monotonic status record that clients already poll.
    A slot is released only when the real synchronous call ends, never when
    the request's wait expires, so repeated timeouts cannot grow a thread queue.
    """
    if not _PUBLISH_SLOTS.acquire(blocking=False):
        raise UploadPublisherBusy("upload publisher is busy")
    try:
        publish = partial(
            task.apply_async,
            kwargs=kwargs,
            task_id=task_id,
            ignore_result=True,
            retry=False,
        )
        future = _PUBLISH_EXECUTOR.submit(contextvars.copy_context().run, publish)
    except BaseException:
        _PUBLISH_SLOTS.release()
        raise
    future.add_done_callback(lambda _finished: _PUBLISH_SLOTS.release())
    pending = asyncio.wrap_future(future)
    pending.add_done_callback(_consume_result)
    await asyncio.wait_for(
        asyncio.shield(pending), timeout=UPLOAD_PUBLISH_TIMEOUT_SECONDS
    )
