"""Bound synchronous upload publication without discarding ambiguous work.

The upload status endpoint, rather than Celery's result backend, reports the
business outcome. A publish error or request timeout cannot establish whether
the broker accepted the message. Callers must retain its object and status
seed, including when a timed-out publication finishes later.

The PostgreSQL upload receipt records recoverable work across API exits. The
finite executor and slots bound calls that outlive their HTTP wait.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.worker.celery_app import celery_app


UPLOAD_PUBLISH_TIMEOUT_SECONDS = 3.0
_BROKER_SOCKET_TIMEOUT_SECONDS = 2.0
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


def _send_upload(task: Any, kwargs: dict[str, Any], task_id: str) -> None:
    # The default Kombu Redis transport can wait indefinitely for a reply on a
    # half-open socket. Isolate upload connections from the shared publisher and
    # consumer pools and put a deadline on each connect or socket operation.
    with celery_app.connection_for_write(
        connect_timeout=_BROKER_SOCKET_TIMEOUT_SECONDS,
        transport_options={
            "socket_connect_timeout": _BROKER_SOCKET_TIMEOUT_SECONDS,
            "socket_timeout": _BROKER_SOCKET_TIMEOUT_SECONDS,
            "retry_on_timeout": False,
            "max_retries": 0,
        },
    ) as connection:
        task.apply_async(
            kwargs=kwargs, task_id=task_id, ignore_result=True, retry=False,
            connection=connection,
        )


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
        future = _PUBLISH_EXECUTOR.submit(
            contextvars.copy_context().run, _send_upload, task, kwargs, task_id,
        )
    except BaseException:
        _PUBLISH_SLOTS.release()
        raise
    future.add_done_callback(lambda _finished: _PUBLISH_SLOTS.release())
    pending = asyncio.wrap_future(future)
    pending.add_done_callback(_consume_result)
    await asyncio.wait_for(
        asyncio.shield(pending), timeout=UPLOAD_PUBLISH_TIMEOUT_SECONDS
    )
