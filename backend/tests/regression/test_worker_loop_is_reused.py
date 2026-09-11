"""One event loop per Celery worker child, reused by every task (re-audit M1).

Every task used to build a fresh event loop and tear down the engine, Redis,
httpx and Mongo clients when it ended, so no connection ever outlived a task.
``worker/loop_runner.py`` keeps one loop per child instead. These pin the rules
that make reusing it safe:

* consecutive tasks run on one loop;
* whatever a task leaves pending is cancelled, never resumed inside the next,
  and a leftover that will not stop takes the loop down with it;
* a task that fails leaves the loop usable; one interrupted mid-flight
  discards it, and never resumes;
* a call from inside a running loop is refused without harming that loop;
* a loop inherited across fork is dropped, never used or closed, by the child;
* worker shutdown drains the loop;
* worker engines ping pooled connections, since those now outlive a task.
"""
from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from app.worker import loop_runner


@pytest.fixture(autouse=True)
def _quiet_drains(monkeypatch):
    """Teardown drains are exercised in test_worker_drains_redis_on_owning_loop."""
    import app.core.http_client as http
    import app.db.mongo as mongo
    import app.db.postgres as pg
    import app.db.redis_client as redis_client

    async def _noop():
        return None

    monkeypatch.setattr(pg, "dispose_engine_for_loop", _noop)
    monkeypatch.setattr(http, "close_http_client", _noop)
    monkeypatch.setattr(redis_client, "close_redis", _noop)
    monkeypatch.setattr(mongo, "close_mongo", _noop)
    loop_runner.shutdown_worker_loop()
    yield
    loop_runner.shutdown_worker_loop()


def _interrupt():
    # Propagates out of the loop, as a signal-raised soft time limit does
    # when it lands while the loop is waiting.
    raise KeyboardInterrupt


async def _current_loop():
    return asyncio.get_running_loop()


def test_consecutive_tasks_share_one_loop():
    first = loop_runner.run_async(_current_loop())
    for _ in range(3):
        assert loop_runner.run_async(_current_loop()) is first


def test_a_straggler_does_not_run_inside_the_next_task():
    ran: list[str] = []

    async def _leaves_a_task_behind():
        async def _late():
            await asyncio.sleep(0.05)
            ran.append("straggler")

        asyncio.get_running_loop().create_task(_late())
        return "done"

    async def _next():
        await asyncio.sleep(0.2)
        return "next"

    assert loop_runner.run_async(_leaves_a_task_behind()) == "done"
    assert loop_runner.run_async(_next()) == "next"
    assert ran == [], "a task left behind by one task ran inside the next"


def test_a_straggler_that_ignores_cancellation_takes_the_loop_with_it(monkeypatch):
    """Waiting on it forever would hang the worker child until the hard time
    limit killed it; keeping the loop would run it inside the next task."""
    monkeypatch.setattr(loop_runner, "_STRAGGLER_GRACE_SECONDS", 0.05)

    async def _leaves_a_stubborn_task():
        async def _stubborn():
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    continue

        asyncio.get_running_loop().create_task(_stubborn())
        return asyncio.get_running_loop()

    first = loop_runner.run_async(_leaves_a_stubborn_task())
    assert first.is_closed(), "the loop was kept with a live task still on it"
    assert loop_runner.run_async(_current_loop()) is not first


def test_a_failed_task_leaves_the_loop_usable():
    async def _boom():
        raise ValueError("task failed")

    first = loop_runner.run_async(_current_loop())
    with pytest.raises(ValueError):
        loop_runner.run_async(_boom())
    assert loop_runner.run_async(_current_loop()) is first


def test_an_interrupted_task_is_discarded_and_never_resumes():
    trace: list[str] = []

    async def _interrupted():
        asyncio.get_running_loop().call_soon(_interrupt)
        try:
            await asyncio.sleep(3600)
        finally:
            trace.append("cancelled")
        trace.append("resumed")  # must never run

    first = loop_runner.run_async(_current_loop())
    with pytest.raises(KeyboardInterrupt):
        loop_runner.run_async(_interrupted())

    assert trace == ["cancelled"], trace
    assert first.is_closed(), "the interrupted loop was kept"
    assert loop_runner.run_async(_current_loop()) is not first


def test_a_soft_time_limit_inside_the_task_keeps_the_loop_and_runs_cleanups():
    """The soft time limit can also land while the task's own code runs. The
    task then fails like any other: its cleanups run as it unwinds, and the
    loop is kept. Only the landing above, while the loop waits, discards it
    (code review of M1: this landing was not pinned)."""
    from billiard.exceptions import SoftTimeLimitExceeded

    trace: list[str] = []

    async def _hits_the_limit():
        try:
            await asyncio.sleep(0)
            raise SoftTimeLimitExceeded()
        finally:
            trace.append("cleanup")

    first = loop_runner.run_async(_current_loop())
    with pytest.raises(SoftTimeLimitExceeded):
        loop_runner.run_async(_hits_the_limit())

    assert trace == ["cleanup"]
    assert not first.is_closed(), "a task that failed inside its own code cost the loop"
    assert loop_runner.run_async(_current_loop()) is first


def test_a_call_from_inside_a_running_loop_is_refused_and_harms_nothing():
    """A per-task loop refused this too. Tearing the running loop down to
    report it would cancel the caller."""
    async def _outer():
        with pytest.raises(RuntimeError, match="cannot be called from a running event loop"):
            loop_runner.run_async(_current_loop())
        await asyncio.sleep(0)  # the caller was not cancelled
        return asyncio.get_running_loop()

    loop = loop_runner.run_async(_outer())
    assert not loop.is_closed()
    assert loop_runner.run_async(_current_loop()) is loop


def test_an_inherited_loop_is_forgotten_and_never_closed(monkeypatch):
    """A child must never drive, or close, a loop whose selector it shares with
    its parent -- not even by letting the garbage collector close it."""
    from app.worker import celery_app

    monkeypatch.setattr(loop_runner, "_inherited", [])
    inherited = asyncio.new_event_loop()
    monkeypatch.setattr(loop_runner, "_loop", inherited)

    celery_app._reset_db_pool_after_fork()

    assert loop_runner._loop is None
    ref = weakref.ref(inherited)
    del inherited
    gc.collect()
    survivor = ref()
    try:
        assert survivor is not None, "dropped, so the garbage collector closed it"
        assert not survivor.is_closed(), "the child closed a loop its parent owns"
    finally:
        if survivor is not None:
            survivor.close()


def test_the_worker_shutdown_signal_drains_the_loop(monkeypatch):
    from app.worker import celery_app

    calls: list[int] = []
    monkeypatch.setattr(loop_runner, "shutdown_worker_loop", lambda: calls.append(1))
    celery_app._close_worker_loop()
    assert calls == [1]


@pytest.mark.parametrize("role, pings", [("worker", True), ("api", False)])
def test_in_production_only_worker_engines_ping_before_checkout(monkeypatch, role, pings):
    """Worker connections now outlive a task, so a Postgres restart must not
    strand dead ones in the pool. The API keeps the P3-5 saving."""
    import app.db.postgres as pg
    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", role)
    monkeypatch.setattr(
        settings, "DATABASE_URL", "postgresql+asyncpg://t:t@localhost:5432/t"
    )
    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()
    try:
        assert pg.get_engine().pool._pre_ping is pings
    finally:
        pg.get_engine.cache_clear()
        pg.get_session_factory.cache_clear()
