"""A worker child's Postgres pool survives from one task to the next (re-audit M1).

Every Celery task used to run on a fresh event loop and dispose the async engine
before closing it, so the pool never outlived a task: every task that touched
Postgres paid a TCP connect, authentication and a backend fork. A worker child
now keeps one loop (``worker/loop_runner.py``), and with it one engine.

This counts real connections against real Postgres: three tasks, one connect.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import event, text
from sqlalchemy.pool import Pool

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def test_three_tasks_open_one_connection(monkeypatch):
    import app.db.postgres as pg
    from app.core.config import settings
    from app.worker import loop_runner

    monkeypatch.setattr(settings, "DATABASE_URL", _dsn())
    loop_runner.shutdown_worker_loop()
    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()

    connects: list[int] = []

    def _on_connect(*_args):
        connects.append(1)

    async def _one_query():
        async with pg.AsyncSessionLocal() as db:
            return (await db.execute(text("SELECT 1"))).scalar_one()

    event.listen(Pool, "connect", _on_connect)
    try:
        for _ in range(3):
            assert loop_runner.run_async(_one_query()) == 1
    finally:
        event.remove(Pool, "connect", _on_connect)
        loop_runner.shutdown_worker_loop()
        pg.get_engine.cache_clear()
        pg.get_session_factory.cache_clear()

    assert len(connects) == 1, (
        f"{len(connects)} connections opened for 3 tasks: the pool is rebuilt per task"
    )
