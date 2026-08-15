"""A cached async client must not outlive the event loop it was built on.

Found on the homelab, in two places at once:

```
worker-ai       RuntimeError: Event loop is closed
                  at app/agents/summary_agent.py:892 in _store_summary
worker-default  [AI Email] Failed for run 704cca9a…: Event loop is closed
```

Celery prefork workers run every task on a **fresh** event loop and close it
afterwards (``worker/tasks.py::_run_async``). ``AsyncIOMotorClient`` binds to
the loop that is running when it is constructed, and ``app/db/mongo.py`` caches
it in a module global. So task #1 built the client on its loop, that loop
closed, and task #2 onward got a client wired to a dead loop.

The wrapper already knew about this failure mode — it reset Redis for exactly
this reason and disposed the Postgres engine on its owning loop, with a comment
explaining both. Mongo was simply never added. And the second wrapper,
``worker/training_tasks.py::_run_async``, reset *nothing*.

Consequences were not cosmetic. When ``_store_summary`` raises, the summary
agent's outer handler returns ``structured_summary=None`` — so the run's
intelligence page renders empty, which is the same user-visible symptom as the
LLM-unavailable bug fixed separately, arriving by a completely different route.

The guard is the CLASS: **every loop-bound client cache is reset when a task
builds a new loop, and every task wrapper does it.** Per-wrapper lists drift —
that drift is the bug — so the reset lives in one place and both wrappers call
it.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

import app.db.mongo as mongo_mod
import app.db.redis_client as redis_mod
from app.db.loop_bound import reset_loop_bound_clients

BACKEND = pathlib.Path(__file__).resolve().parents[2]
DB_DIR = BACKEND / "app" / "db"


class _LoopBoundFake:
    """Stands in for Motor: captures its loop and refuses to work on another."""

    def __init__(self, *args, **kwargs):
        self.loop = asyncio.get_event_loop()
        self.closed = False

    async def ping(self):
        if self.loop.is_closed():
            raise RuntimeError("Event loop is closed")
        return True

    def close(self):
        self.closed = True

    def __getitem__(self, _name):
        return self


# ── The regression ──────────────────────────────────────────────────────────


def test_a_second_task_does_not_inherit_the_first_tasks_dead_loop(monkeypatch):
    """The exact live failure. Two tasks in one worker child, back to back."""
    monkeypatch.setattr(mongo_mod, "AsyncIOMotorClient", _LoopBoundFake)
    monkeypatch.setattr(mongo_mod, "_client", None)

    from app.worker.tasks import _run_async

    async def touch_mongo():
        return await mongo_mod.get_mongo_client().ping()

    assert _run_async(touch_mongo()) is True
    # Task #2 — this is the call that raised in production.
    assert _run_async(touch_mongo()) is True


def test_the_training_wrapper_gets_the_same_treatment(monkeypatch):
    """The wrapper that reset nothing at all."""
    monkeypatch.setattr(mongo_mod, "AsyncIOMotorClient", _LoopBoundFake)
    monkeypatch.setattr(mongo_mod, "_client", None)

    from app.worker.training_tasks import _run_async

    async def touch_mongo():
        return await mongo_mod.get_mongo_client().ping()

    assert _run_async(touch_mongo()) is True
    assert _run_async(touch_mongo()) is True


def test_each_task_really_gets_a_distinct_client(monkeypatch):
    """Guards against a "fix" that merely swallows the error: the point is a
    client built on the *live* loop, not a retry of a dead one."""
    monkeypatch.setattr(mongo_mod, "AsyncIOMotorClient", _LoopBoundFake)
    monkeypatch.setattr(mongo_mod, "_client", None)

    from app.worker.tasks import _run_async

    seen = []

    async def capture():
        client = mongo_mod.get_mongo_client()
        seen.append(client)
        await client.ping()

    _run_async(capture())
    _run_async(capture())
    assert seen[0] is not seen[1], "second task reused the first task's client"
    assert seen[0].closed, "the superseded client was never closed"


# ── The reset itself ────────────────────────────────────────────────────────


def test_reset_clears_mongo_and_redis():
    mongo_mod._client = _LoopBoundFake.__new__(_LoopBoundFake)
    mongo_mod._client.closed = False
    redis_mod._pool = object()
    redis_mod._client = object()

    reset_loop_bound_clients()

    assert mongo_mod._client is None
    assert redis_mod._pool is None
    assert redis_mod._client is None


def test_reset_survives_a_client_that_raises_on_close(monkeypatch):
    """A client whose loop is already gone can raise while shutting down. That
    must not fail the task that is only just starting."""

    class Angry:
        def close(self):
            raise RuntimeError("Event loop is closed")

    monkeypatch.setattr(mongo_mod, "_client", Angry())
    reset_loop_bound_clients()
    assert mongo_mod._client is None


def test_reset_works_with_no_running_loop():
    """It is called before the task's loop exists."""
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
    reset_loop_bound_clients()  # must not raise


# ── The ratchet: a newly cached client must not be forgotten ────────────────


def _module_level_cache_names(path: pathlib.Path) -> set[str]:
    """Module-scope globals that look like a cached client/pool handle."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        for t in targets:
            if t.id.startswith("_") and ("client" in t.id or "pool" in t.id):
                names.add(t.id)
    return names


# Modules whose cached handle is deliberately NOT dropped here, with the reason.
EXEMPT = {
    # Its pool holds live asyncpg connections, so it is *disposed on the loop
    # that owns them* during task teardown instead of being dropped.
    "postgres.py": "disposed via dispose_engine_for_loop() in task teardown",
}


def test_every_cached_db_client_is_reset_or_explicitly_exempt():
    """When someone adds a new cached async client to app/db, this fails until
    they either wire it into the reset or record why it is exempt. Per-wrapper
    drift is what caused the bug; this is the thing that stops it recurring."""
    unhandled = []
    for path in sorted(DB_DIR.glob("*.py")):
        if path.name == "__init__.py" or path.name == "loop_bound.py":
            continue
        names = _module_level_cache_names(path)
        if not names:
            continue
        if path.name in EXEMPT:
            continue
        module_reset = _reset_touches_module(path.stem)
        if not module_reset:
            unhandled.append(f"{path.name} caches {sorted(names)}")
    assert not unhandled, (
        "cached client(s) not cleared by reset_loop_bound_clients() and not "
        f"listed in EXEMPT: {unhandled}"
    )


def _reset_touches_module(module_stem: str) -> bool:
    """Does reset_loop_bound_clients reach the named app.db module?"""
    src = (DB_DIR / "loop_bound.py").read_text(encoding="utf-8")
    return module_stem in src


def test_the_ratchet_can_actually_fail():
    """The ratchet greps loop_bound.py for the module name, so a module that is
    genuinely absent must be reported. Without this, a typo in the helper would
    make the ratchet vacuous and everything would look handled."""
    assert not _reset_touches_module("a_module_that_does_not_exist")
    assert _reset_touches_module("mongo")
    assert _reset_touches_module("redis_client")


# ── Both wrappers must call it ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "relpath", ["app/worker/tasks.py", "app/worker/training_tasks.py"]
)
def test_every_task_wrapper_resets_loop_bound_clients(relpath):
    """Two wrappers existed and only one reset anything. Pin both."""
    src = (BACKEND / relpath).read_text(encoding="utf-8")
    assert "reset_loop_bound_clients()" in src, (
        f"{relpath} builds its own event loop but never resets loop-bound clients"
    )
