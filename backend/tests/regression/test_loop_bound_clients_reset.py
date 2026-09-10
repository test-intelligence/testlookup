"""A cached async client must not outlive the event loop it was built on.

Found on the homelab, in two places at once::

    worker-ai       RuntimeError: Event loop is closed
                      at app/agents/summary_agent.py:892 in _store_summary
    worker-default  [AI Email] Failed for run 704cca9a...: Event loop is closed

Celery tasks each ran on a fresh event loop that was closed afterwards.
``AsyncIOMotorClient`` binds to the loop running when it is built, and
``app/db/mongo.py`` caches it in a module global, so task #2 onward got a
client wired to a dead loop. The wrapper reset Redis and disposed the engine
for exactly this reason; Mongo was never added, and the second wrapper,
``worker/training_tasks.py``, reset nothing.

Since re-audit M1 a worker child keeps one loop (``worker/loop_runner.py``),
so a client lives as long as that loop -- which is the point: that is the pool
doing its job. The guard is still the CLASS: **every loop-bound client cache
is reset whenever a new loop is built, and every task wrapper goes through the
one runner that does it.**
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

import app.db.mongo as mongo_mod
import app.db.redis_client as redis_mod
from app.db.loop_bound import reset_loop_bound_clients
from app.worker import loop_runner

BACKEND = pathlib.Path(__file__).resolve().parents[2]
DB_DIR = BACKEND / "app" / "db"


@pytest.fixture(autouse=True)
def _fresh_worker_loop():
    loop_runner.shutdown_worker_loop()
    yield
    loop_runner.shutdown_worker_loop()


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
    # Task #2 -- this is the call that raised in production.
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


def test_tasks_on_one_worker_loop_share_the_client(monkeypatch):
    """Re-audit M1: the loop outlives the task, so its client is reused."""
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
    assert seen[0] is seen[1], "a second task on the same loop rebuilt the client"
    assert not seen[0].closed


def test_a_new_loop_gets_a_new_client_and_the_old_one_is_closed(monkeypatch):
    """Guards against a "fix" that merely swallows the error: when the loop IS
    replaced, the client is rebuilt on the live loop and the old one closed."""
    monkeypatch.setattr(mongo_mod, "AsyncIOMotorClient", _LoopBoundFake)
    monkeypatch.setattr(mongo_mod, "_client", None)

    from app.worker.tasks import _run_async

    seen = []

    async def capture():
        client = mongo_mod.get_mongo_client()
        seen.append(client)
        await client.ping()

    _run_async(capture())
    loop_runner.shutdown_worker_loop()  # the child recycles, or a task was interrupted
    _run_async(capture())
    assert seen[0] is not seen[1], "the new loop reused the old loop's client"
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
    """It is called before the new loop runs anything."""
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
    # that owns them* when the worker loop is torn down, instead of dropped.
    "postgres.py": "disposed via dispose_engine_for_loop() when the worker loop is torn down",
}


def test_every_cached_db_client_is_reset_or_explicitly_exempt():
    """A new cached async client in app/db fails this until it is wired into the
    reset or recorded as exempt. Per-wrapper drift caused the bug."""
    unhandled = []
    for path in sorted(DB_DIR.glob("*.py")):
        if path.name == "__init__.py" or path.name == "loop_bound.py":
            continue
        names = _module_level_cache_names(path)
        if not names:
            continue
        if path.name in EXEMPT:
            continue
        if not _reset_touches_module(path.stem):
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
    """A module that is genuinely absent must be reported, or a typo in the
    helper would make the ratchet vacuous."""
    assert not _reset_touches_module("a_module_that_does_not_exist")
    assert _reset_touches_module("mongo")
    assert _reset_touches_module("redis_client")


# ── Every wrapper goes through the one runner ───────────────────────────────


@pytest.mark.parametrize("module", ["app.worker.tasks", "app.worker.training_tasks"])
def test_every_task_wrapper_runs_on_the_one_loop_runner(module, monkeypatch):
    """Two wrappers existed and only one reset anything. Both now delegate."""
    import importlib

    wrapper = importlib.import_module(module)._run_async
    handed: list[object] = []

    def _spy(coro):
        handed.append(coro)
        coro.close()
        return "ran"

    monkeypatch.setattr(loop_runner, "run_async", _spy)

    async def _body():
        return 1

    assert wrapper(_body()) == "ran"
    assert len(handed) == 1, f"{module}._run_async bypassed worker/loop_runner"


def test_the_runner_resets_clients_whenever_it_builds_a_loop():
    assert "reset_loop_bound_clients()" in inspect.getsource(loop_runner._worker_loop)
