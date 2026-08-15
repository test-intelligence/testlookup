"""Reset every client cache that is bound to a specific event loop.

Celery's prefork workers run each task on a **fresh** event loop and close it
afterwards. Any client cached at module scope that captured the previous loop
is unusable on the next one, and says so with ``RuntimeError: Event loop is
closed`` at the first call — far from where the caching happened.

The two task wrappers used to each maintain their own list of things to reset.
They drifted: ``worker/tasks.py`` cleared Redis, ``worker/training_tasks.py``
cleared nothing, and **neither** cleared Mongo, so the summary agent's
``_store_summary`` and the AI-email notifier both failed on every task after
the first in a given worker child.

One function, called by both wrappers, so a newly cached client is added in a
single place. Anything registered here must be safe to call with no running
loop, and must never raise.
"""
from __future__ import annotations


def reset_loop_bound_clients() -> None:
    """Drop cached async clients so the next loop builds its own.

    Postgres is deliberately absent: its engine is disposed *on the loop that
    owns it* during task teardown (``dispose_engine_for_loop``), which is the
    correct shutdown for a pool holding live asyncpg connections. Clients here
    are the ones that only need dropping, not draining.
    """
    import app.db.redis_client as _redis_mod

    _redis_mod._pool = None
    _redis_mod._client = None

    from app.db.mongo import reset_mongo_client

    reset_mongo_client()
