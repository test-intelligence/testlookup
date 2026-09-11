"""The worker loop's teardown closes the Mongo client too (code review of re-audit M1).

The per-task teardown this runner replaced never closed Motor's client either,
but the runner is now the only place a worker's clients are drained. A client
dropped without ``close()`` keeps pymongo's monitor threads alive until garbage
collection, past the loop they were created on.
"""
from __future__ import annotations

import asyncio

import pytest

from app.worker import loop_runner


@pytest.fixture(autouse=True)
def _quiet_other_drains(monkeypatch):
    import app.core.http_client as http
    import app.db.postgres as pg
    import app.db.redis_client as redis_client

    async def _noop():
        return None

    monkeypatch.setattr(pg, "dispose_engine_for_loop", _noop)
    monkeypatch.setattr(http, "close_http_client", _noop)
    monkeypatch.setattr(redis_client, "close_redis", _noop)
    loop_runner.shutdown_worker_loop()
    yield
    loop_runner.shutdown_worker_loop()


def test_the_runner_drains_the_real_mongo_close():
    import app.db.mongo as mongo

    assert ("mongo_close", mongo.close_mongo) in loop_runner._drains()


def test_worker_shutdown_closes_the_mongo_client_on_its_loop(monkeypatch):
    import app.db.mongo as mongo

    closed_on: list[asyncio.AbstractEventLoop] = []

    class _Client:
        def close(self):
            closed_on.append(asyncio.get_running_loop())

    monkeypatch.setattr(mongo, "_client", None)

    async def _task_opens_mongo():
        mongo._client = _Client()
        return asyncio.get_running_loop()

    task_loop = loop_runner.run_async(_task_opens_mongo())
    loop_runner.shutdown_worker_loop()

    assert closed_on == [task_loop], "worker shutdown left the Mongo client open"
    assert mongo._client is None


def test_an_interrupted_task_closes_the_mongo_client_with_its_loop(monkeypatch):
    import app.db.mongo as mongo

    closed_on: list[asyncio.AbstractEventLoop] = []

    class _Client:
        def close(self):
            closed_on.append(asyncio.get_running_loop())

    monkeypatch.setattr(mongo, "_client", None)

    def _interrupt():
        raise KeyboardInterrupt

    async def _interrupted():
        mongo._client = _Client()
        asyncio.get_running_loop().call_soon(_interrupt)
        await asyncio.sleep(3600)

    with pytest.raises(KeyboardInterrupt):
        loop_runner.run_async(_interrupted())

    assert len(closed_on) == 1, "the discarded loop left the Mongo client open"
    assert closed_on[0].is_closed()
