"""One event loop per Celery worker child, reused by every task it runs (re-audit M1).

History. Every task used to build a fresh event loop and tear it down when it
finished: the async engine disposed, Redis and httpx closed, Mongo reset. That
was the correct teardown *given* a loop per task -- pooled asyncpg connections
bound to a closed loop raise "Event loop is closed" (BUG-003), and each client
left undrained leaked sockets under load (~96% of summary stages failed in a
750-pipeline burst on the homelab). But it meant no connection ever outlived a
task: every task that touched Postgres paid a TCP connect, authentication and a
backend fork, and the pool the engine was configured with never pooled
anything.

Now a worker child keeps one loop, built by its first task, and every later
task runs on it, so the engine, Redis, httpx and Mongo clients bound to that
loop are reused. These rules keep that safe:

* After each task, anything the task left pending is cancelled. Closing the
  per-task loop used to destroy those tasks; letting them resume inside the
  next task would be worse. A leftover that ignores cancellation for
  ``_STRAGGLER_GRACE_SECONDS`` takes the loop down with it.
* A task interrupted mid-flight -- a soft time limit, a signal -- leaves its own
  coroutine pending and the loop in a state that cannot be trusted. That loop
  is torn down exactly as every loop used to be, and the next task builds a
  fresh one.
* The drain moves to worker-process shutdown (``celery_app``), on the loop that
  owns the connections.
* A loop inherited across fork is never used, or closed, by the child
  (``forget_inherited_loop``): its selector is shared with the parent.
* ``run_async`` refuses to run inside a running event loop, as a fresh loop per
  task always did, and refuses before it builds or resets anything.

Pooled connections now outlive a task, so worker engines ping on checkout
(``app.db.postgres.get_engine``): a Postgres restart must not strand dead
connections in the pool. The fleet connection budget
(``scripts/validate_db_connection_budget.py``) already counts every process's
full pool, so keeping it open between tasks stays inside that budget.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How long a task's leftovers get to honour cancellation before the loop is
# discarded instead of reused.
_STRAGGLER_GRACE_SECONDS = 5.0

_loop: Optional[asyncio.AbstractEventLoop] = None

# Loops inherited across fork: held for the child's lifetime, never closed.
_inherited: list[asyncio.AbstractEventLoop] = []


def _worker_loop() -> asyncio.AbstractEventLoop:
    """This child's loop, built on first use or after one was discarded."""
    global _loop
    if _loop is None or _loop.is_closed():
        # A new loop: every cached client belongs to a loop that is gone.
        from app.db.loop_bound import reset_loop_bound_clients

        reset_loop_bound_clients()
        _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    return _loop


def run_async(coro: Awaitable[T]) -> T:
    """Run ``coro`` to completion on this worker child's loop.

    A task that fails still completes: its exception propagates and the loop
    carries on. A task interrupted while still running discards the loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # Called from a coroutine, on this loop or another. Refuse before
        # anything is built or reset, so the running loop, its task and its
        # clients are left exactly as they were.
        if asyncio.iscoroutine(coro):
            coro.close()
        raise RuntimeError(
            "run_async() cannot be called from a running event loop; "
            "await the coroutine instead"
        )
    loop = _worker_loop()
    task = asyncio.ensure_future(coro, loop=loop)
    try:
        return loop.run_until_complete(task)
    except BaseException:
        if not task.done():
            logger.warning("worker_loop_discarded_after_interruption")
            _discard(loop)
        raise
    finally:
        if not loop.is_closed() and not _cancel_stragglers(loop):
            logger.warning("worker_loop_discarded_after_stuck_straggler")
            _discard(loop, cancel=False)


def shutdown_worker_loop() -> None:
    """Drain every loop-bound client on its own loop, then close the loop.

    Called at worker-process shutdown. Safe to call when no loop was built.
    """
    global _loop
    loop, _loop = _loop, None
    if loop is None or loop.is_closed():
        return
    try:
        _cancel_stragglers(loop)
    finally:
        _teardown(loop)


def forget_inherited_loop() -> None:
    """After fork: drop a loop the parent built -- never use it, never close it.

    Its selector and self-pipe are shared with the parent across fork, so
    closing it here would unregister the parent's descriptors. A reference is
    kept for the child's lifetime because an event loop's ``__del__`` closes it.
    """
    global _loop
    if _loop is not None:
        _inherited.append(_loop)
    _loop = None


def _cancel_stragglers(loop: asyncio.AbstractEventLoop) -> bool:
    """Cancel whatever a task left pending. False if any of it would not stop."""
    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
    if not pending:
        return True
    for straggler in pending:
        straggler.cancel()
    try:
        _, stuck = loop.run_until_complete(
            asyncio.wait(pending, timeout=_STRAGGLER_GRACE_SECONDS)
        )
    except Exception as exc:  # noqa: BLE001 -- the caller discards the loop
        logger.warning("worker_loop_straggler_cancel_failed error=%r", exc)
        return False
    if stuck:
        logger.warning("worker_loop_straggler_ignored_cancel count=%d", len(stuck))
        return False
    return True


def _discard(loop: asyncio.AbstractEventLoop, *, cancel: bool = True) -> None:
    """Tear ``loop`` down for good; the next task builds a fresh one."""
    global _loop
    try:
        if cancel:
            _cancel_stragglers(loop)
    finally:
        try:
            _teardown(loop)
        finally:
            if _loop is loop:
                _loop = None


def _teardown(loop: asyncio.AbstractEventLoop) -> None:
    """Drain every loop-bound client on the loop that owns it, then close it.

    Each drain is independent and never raises: a failing one is logged, and
    the others and the close still run.
    """
    for label, drain in _drains():
        try:
            loop.run_until_complete(drain())
        except Exception as exc:  # noqa: BLE001 -- teardown must not raise
            logger.warning("worker_loop_%s_failed error=%r", label, exc)
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:  # noqa: BLE001
        pass
    loop.close()


def _drains() -> tuple[tuple[str, Any], ...]:
    # Resolved at call time, so a test (or a later refactor) that replaces one
    # of these on its module is the one that runs.
    import app.core.http_client as http_client
    import app.db.mongo as mongo
    import app.db.postgres as postgres
    import app.db.redis_client as redis_client

    return (
        # The engine first: its pool holds live asyncpg connections (BUG-003).
        ("engine_dispose", postgres.dispose_engine_for_loop),
        ("http_client_close", http_client.close_http_client),
        ("redis_close", redis_client.close_redis),
        # Motor's client runs pymongo's monitor threads; dropped without
        # close() they outlive the loop until garbage collection (code review
        # of re-audit M1).
        ("mongo_close", mongo.close_mongo),
    )
