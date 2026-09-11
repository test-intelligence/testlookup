"""Regression guard: the worker's loop teardown must close the shared httpx client.

The gap (AI-HTTPX-001, residual)
--------------------------------
``get_http_client()`` rotates the process-wide ``httpx.AsyncClient`` when it
notices the owning event loop changed. Rotating **replaces** the reference; the
outgoing client is never closed. ``close_http_client()`` was called from the
FastAPI lifespan and from nowhere in the worker path, so each task -- which ran
on a loop of its own -- abandoned a client whose pool still held sockets bound
to a loop about to close.

``reset_loop_bound_clients()`` does not cover it either: it drops the Redis and
Mongo clients, which only DROPS references. A pool with live TCP connections
needs draining on the loop that owns it, which is why Postgres is disposed
explicitly. The httpx client belongs in that second category.

Since re-audit M1 a worker child keeps one loop (``worker/loop_runner.py``) and
the client lives as long as it does, so the guard becomes: the client is not
closed between ordinary tasks (they share it), and it IS closed -- once, on its
own still-open loop -- whenever that loop is torn down: at worker shutdown, or
after an interrupted task. A failing close never escapes teardown.

Scope of the claim
------------------
The original AI-HTTPX-001 symptom -- ``RuntimeError: Event loop is closed`` --
did **not** reproduce on the deployment: zero occurrences across every worker
and beat pod while the integration probe ran. This guard covers the residual
mechanism, verified by reading the code, **not** by observing a leak.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

import app.core.http_client as http_client
from app.worker import loop_runner

pytestmark = pytest.mark.regression


@pytest.fixture(autouse=True)
def _fresh_worker_loop():
    loop_runner.shutdown_worker_loop()
    yield
    loop_runner.shutdown_worker_loop()


@pytest.fixture
def spy_close(monkeypatch):
    """Record each close_http_client call and whether its loop was still open."""
    calls: list[bool] = []

    async def _fake_close():
        calls.append(not asyncio.get_running_loop().is_closed())

    monkeypatch.setattr(http_client, "close_http_client", _fake_close)
    # Neutralise the other drains so this test isolates the http client.
    import app.db.postgres as pg
    import app.db.redis_client as redis_client

    async def _noop():
        return None

    monkeypatch.setattr(pg, "dispose_engine_for_loop", _noop)
    monkeypatch.setattr(redis_client, "close_redis", _noop)
    return calls


def _interrupt():
    raise KeyboardInterrupt


async def _work():
    return "done"


def test_the_client_is_shared_by_tasks_and_not_closed_between_them(spy_close):
    for _ in range(3):
        assert loop_runner.run_async(_work()) == "done"
    assert spy_close == []


def test_the_client_is_closed_once_on_its_own_loop_at_shutdown(spy_close):
    assert loop_runner.run_async(_work()) == "done"
    loop_runner.shutdown_worker_loop()
    assert spy_close == [True], "close_http_client must run exactly once, on a loop still open"


def test_an_interrupted_task_closes_the_client_with_its_loop(spy_close):
    async def _interrupted():
        asyncio.get_running_loop().call_soon(_interrupt)
        await asyncio.sleep(3600)

    with pytest.raises(KeyboardInterrupt):
        loop_runner.run_async(_interrupted())
    assert spy_close == [True], "the discarded loop's client was dropped, not drained"


def test_a_failing_close_does_not_escape_teardown(monkeypatch):
    """Teardown must never replace a task's outcome, or a shutdown, with its own error."""
    async def _raising_close():
        raise RuntimeError("pool already gone")

    monkeypatch.setattr(http_client, "close_http_client", _raising_close)
    assert loop_runner.run_async(_work()) == "done"
    loop_runner.shutdown_worker_loop()  # must not raise


def test_the_runner_drains_the_real_close_function():
    """Resolved at call time from its module, so the spy above is the one that
    runs -- and so a rename here cannot silently drop the drain."""
    assert ("http_client_close", http_client.close_http_client) in loop_runner._drains()


def test_reset_loop_bound_clients_still_excludes_httpx():
    """Pin the premise, so the fix is not silently made redundant.

    If httpx is ever added to ``reset_loop_bound_clients``, that only DROPS
    the reference -- it does not drain the pool -- so this guard's reasoning
    would need re-reading rather than quietly passing.
    """
    from app.db import loop_bound

    src = pathlib.Path(loop_bound.__file__).read_text(encoding="utf-8")
    assert "http_client" not in src, (
        "reset_loop_bound_clients now touches the http client. Dropping a "
        "reference does not close sockets; re-read whether teardown should "
        "still drain it explicitly."
    )
