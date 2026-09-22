"""VIZ-212: the analytics cache keys on a per-project epoch.

What these pin, in cache_service and its two consumers:

* ``bump_analytics_epoch`` is an INCR of the project's counter and of the
  all-projects counter, with NO TTL (Redis runs ``volatile-lru``, which evicts
  only keys that have one), and the counters live outside ``analytics:*`` so
  the legacy SCAN+DELETE cannot reset them to 0.
* An epoch that cannot be read (Redis down, corrupt counter) BYPASSES the
  cache: nothing is read, nothing is written, the caller computes from the
  database and the response is still right.
* The epoch is read once, before the query: a mutation that commits while the
  query runs must not have its old snapshot cached under the new epoch.
* End to end: a cached dashboard summary changes after a run is deleted.
"""
from __future__ import annotations

import asyncio
import fnmatch
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("redis")

from app.services import cache_service  # noqa: E402


class _EpochRedis:
    """In-memory async Redis with the commands the cache uses. Records every
    command, and can be switched to fail like an unreachable server."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.calls: list[tuple] = []
        self.down = False
        self.round_trips = 0
        self.hang_seconds = 0.0

    def _hit(self, *call) -> None:
        self.calls.append(call)
        if self.down:
            raise ConnectionError("redis is down")

    async def get(self, key):
        self._hit("get", key)
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self._hit("set", key, ex)
        self.data[key] = str(value)
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def incr(self, key):
        self._hit("incr", key)
        value = int(self.data.get(key) or 0) + 1  # ValueError on a garbage value
        if value > 2**63 - 1:
            raise OverflowError("increment or decrement would overflow")
        self.data[key] = str(value)
        return value

    def pipeline(self, transaction=True):
        return _EpochPipeline(self)

    async def expire(self, key, seconds):
        self._hit("expire", key, seconds)
        self.ttl[key] = seconds
        return True

    async def scan(self, cursor=0, match="*", count=100):
        self._hit("scan", match)
        return 0, [k for k in self.data if fnmatch.fnmatchcase(k, match)]

    async def delete(self, *keys):
        self._hit("delete", *keys)
        for key in keys:
            self.data.pop(key, None)
            self.ttl.pop(key, None)
        return len(keys)


class _EpochPipeline:
    """A non-transactional pipeline: queued INCRs, one round trip, and a
    failed command is returned in place (``raise_on_error=False``) while the
    commands after it still run — what redis-py does against a real server."""

    def __init__(self, redis: _EpochRedis) -> None:
        self.redis = redis
        self.queued: list[str] = []

    def incr(self, key):
        self.queued.append(key)
        return self

    async def execute(self, raise_on_error=True):
        self.redis.round_trips += 1
        if self.redis.hang_seconds:
            await asyncio.sleep(self.redis.hang_seconds)
        if self.redis.down:
            raise ConnectionError("redis is down")
        results = []
        for key in self.queued:
            try:
                results.append(await self.redis.incr(key))
            except Exception as exc:  # noqa: BLE001 - mirrors raise_on_error=False
                if raise_on_error:
                    raise
                results.append(exc)
        return results


@pytest.fixture
def redis(monkeypatch) -> _EpochRedis:
    fake = _EpochRedis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


# ── The counter ──────────────────────────────────────────────────────────────


async def test_bump_increments_project_and_global_counters_without_ttl(redis):
    await cache_service.bump_analytics_epoch("p1")
    await cache_service.bump_analytics_epoch(uuid.UUID(int=7))

    assert redis.data["analytics_epoch:p1"] == "1"
    assert redis.data[f"analytics_epoch:{uuid.UUID(int=7)}"] == "1"
    assert redis.data["analytics_epoch:__all__"] == "2"
    # INCR only. A SET-with-EX or an EXPIRE would make the counter evictable
    # under volatile-lru, and an evicted counter restarts at 0 — re-validating
    # every entry cached under epoch 0.
    assert {c[0] for c in redis.calls} == {"incr"}
    assert redis.ttl == {}


async def test_epoch_is_zero_when_missing_and_none_when_unknowable(redis):
    assert await cache_service.get_analytics_epoch("p1") == 0
    await cache_service.bump_analytics_epoch("p1")
    assert await cache_service.get_analytics_epoch("p1") == 1
    assert await cache_service.get_analytics_epoch(None) == 1  # the global one

    redis.data["analytics_epoch:p2"] = "not-a-number"
    assert await cache_service.get_analytics_epoch("p2") is None

    redis.down = True
    assert await cache_service.get_analytics_epoch("p1") is None


async def test_bump_with_redis_down_does_not_raise(redis):
    redis.down = True
    await cache_service.bump_analytics_epoch("p1")  # runs after a commit: must not 500
    await cache_service.bump_analytics_epochs(["p1", "p2"])


async def test_sweep_helper_bumps_each_project_once(redis):
    await cache_service.bump_analytics_epochs(["p1", "p1", None, "p2", uuid.UUID(int=1)])
    assert redis.data["analytics_epoch:p1"] == "1"
    assert redis.data["analytics_epoch:p2"] == "1"
    # One sweep is one invalidation of the all-projects entries.
    assert redis.data["analytics_epoch:__all__"] == "1"


async def test_a_bump_is_one_round_trip_however_many_projects(redis):
    await cache_service.bump_analytics_epochs(["p1", "p2", "p3"])
    assert redis.round_trips == 1
    assert [redis.data[f"analytics_epoch:{p}"] for p in ("p1", "p2", "p3")] == ["1"] * 3
    assert redis.data["analytics_epoch:__all__"] == "1", "__all__ once per call"

    redis.round_trips = 0
    await cache_service.bump_analytics_epoch("p1")
    assert redis.round_trips == 1


async def test_a_hanging_redis_cannot_stall_the_mutation(redis, monkeypatch):
    """The bump runs after the commit, on the request. A Redis that accepts
    and never answers took the client's 5 s socket timeout per INCR."""
    import time

    counted = _failure_counter(monkeypatch)
    redis.hang_seconds = 5.0
    started = time.monotonic()
    await cache_service.bump_analytics_epochs(["p1", "p2"])
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"a bump against a hanging Redis took {elapsed:.2f}s"
    assert redis.round_trips == 1, "one attempt, not one per project"
    assert counted() == 3, "p1, p2 and __all__ were not bumped"


async def test_a_real_client_against_a_silent_server_gives_up_fast(monkeypatch):
    """Not the fake: redis-py with the app's 5 s socket timeout, talking to a
    TCP server that accepts and never answers."""
    import time

    import redis.asyncio as aioredis

    held: list[asyncio.StreamWriter] = []

    async def _silent(_reader, writer):
        held.append(writer)

    server = await asyncio.start_server(_silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = aioredis.Redis(
        host="127.0.0.1", port=port, socket_timeout=5, socket_connect_timeout=5
    )
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: client)
    try:
        started = time.monotonic()
        await cache_service.bump_analytics_epochs(["p1", "p2"])
        elapsed = time.monotonic() - started
    finally:
        for writer in held:
            writer.close()
        server.close()
        await client.aclose()
    assert elapsed < 1.0, f"took {elapsed:.2f}s"


async def test_all_projects_counter_is_bumped_even_when_a_project_incr_fails(
    redis, monkeypatch
):
    counted = _failure_counter(monkeypatch)
    redis.data["analytics_epoch:__all__"] = "4"
    redis.data["analytics_epoch:bad"] = "not-a-number"

    await cache_service.bump_analytics_epochs(["bad", "good"])

    assert redis.data["analytics_epoch:__all__"] == "5"
    assert redis.data["analytics_epoch:good"] == "1"
    assert counted() == 1


@pytest.mark.parametrize(
    "stored", ["not-a-number", "-3", str(2**63 - 1)], ids=["garbage", "negative", "overflow"]
)
async def test_a_corrupt_counter_is_bypassed_then_repaired(redis, stored):
    """A counter that is not a usable epoch must not disable caching forever."""
    key = "analytics_epoch:p1"
    redis.data[key] = stored

    await cache_service.bump_analytics_epoch("p1")
    assert int(redis.data[key]) > 10**12, "the bump repaired the counter"
    assert int(redis.data[key]) < 2**63 - 1
    redis.data[key] = stored

    assert await cache_service.get_analytics_epoch("p1") is None, "bypass this read"
    repaired = await cache_service.get_analytics_epoch("p1")
    assert repaired is not None and repaired > 10**12, (
        "the read repaired the counter to a fresh value, above anything INCR reached"
    )
    assert redis.ttl.get(key) is None, "the repair gives the counter no TTL"
    await cache_service.cache_set("dash", {"v": 1}, "p1", epoch=repaired, days=7)
    assert await cache_service.cache_get("dash", "p1", epoch=repaired, days=7) == {"v": 1}


async def test_a_failed_bump_is_counted(redis, monkeypatch):
    counted = _failure_counter(monkeypatch)
    redis.down = True
    await cache_service.bump_analytics_epoch("p1")
    assert counted() == 2, "the project counter and __all__"


def _failure_counter(monkeypatch):
    """Read ``analytics_epoch_bump_failures_total`` as a delta from now."""
    from app.core import metrics

    counter = metrics.analytics_epoch_bump_failures_total
    before = counter._value.get()
    return lambda: counter._value.get() - before


async def test_scan_delete_cannot_reset_the_counters(redis):
    await cache_service.bump_analytics_epoch("p1")
    await cache_service.cache_set("dash", {"v": 1}, "p1", epoch=1, days=7)

    await cache_service.invalidate_analytics_cache("p1")
    assert redis.data["analytics_epoch:p1"] == "2", "invalidate bumps too"
    await cache_service.invalidate_analytics_cache(None)

    assert not [k for k in redis.data if k.startswith("analytics:")]
    assert redis.data["analytics_epoch:p1"] == "2"
    assert redis.data["analytics_epoch:__all__"] == "3"


# ── Reads and writes key on the epoch; an unknown epoch bypasses ─────────────


async def test_a_bumped_epoch_misses_the_old_entry(redis):
    await cache_service.cache_set("dash", {"v": 1}, "p1", epoch=0, days=7)
    assert await cache_service.cache_get("dash", "p1", epoch=0, days=7) == {"v": 1}

    await cache_service.bump_analytics_epoch("p1")
    epoch = await cache_service.get_analytics_epoch("p1")
    assert await cache_service.cache_get("dash", "p1", epoch=epoch, days=7) is None


async def test_unknown_epoch_bypasses_the_cache_entirely(redis):
    await cache_service.cache_set("dash", {"v": 1}, "p1", epoch=0, days=7)
    redis.calls.clear()

    assert await cache_service.cache_get("dash", "p1", epoch=None, days=7) is None
    await cache_service.cache_set("dash", {"v": 2}, "p1", epoch=None, days=7)

    assert redis.calls == [], "a value whose epoch is unknown is never read or written"


def test_epoch_is_a_required_argument():
    """Forgetting it must be a TypeError at the call, not a silently
    unversioned key."""
    with pytest.raises(TypeError):
        asyncio.run(cache_service.cache_get("dash", "p1", days=7))  # type: ignore[call-arg]


# ── The dashboard summary, end to end over the cache ─────────────────────────


class _Runs:
    """The 'database': a project's runs, as the summary's queries would see them."""

    def __init__(self, project_id, runs):
        self.project_id = project_id
        self.runs = runs  # {run_id: executions}
        self.queries = 0


def _wire_summary(monkeypatch, store: _Runs, *, during_query=None):
    from app.services import metrics_service

    async def _period_stats(db, project_id, start, end, suite, release_id):
        store.queries += 1
        # The query's snapshot is taken first; a mutation committing while the
        # rest of the request runs does not change what this read saw.
        snapshot = {
            "total_runs": len(store.runs),
            "total_executions": sum(store.runs.values()),
            "pass_rate": 90.0,
            "avg_duration_ms": 1000,
        }
        if during_query is not None:
            await during_query()
        return snapshot

    monkeypatch.setattr(metrics_service, "_period_stats", _period_stats)
    monkeypatch.setattr(metrics_service, "_count_flaky_tests", AsyncMock(return_value=0))
    monkeypatch.setattr(
        metrics_service, "_resolve_policy_for_project", AsyncMock(return_value=None)
    )

    result = MagicMock()
    result.scalar.return_value = 0
    return SimpleNamespace(execute=AsyncMock(return_value=result))


async def _summary(db, project_id):
    from app.services.metrics_service import get_dashboard_summary

    out = await get_dashboard_summary(db, str(project_id), days=7)
    return out["total_executions_7d"]["value"]


def test_cached_dashboard_summary_changes_after_a_run_is_deleted(redis, monkeypatch):
    """The story's headline, through the real delete task and the real cache."""
    import app.db.postgres as pg
    from app.db import mongo as mongo_module
    from app.db import storage as storage_module
    from app.services import deletion_job_service, run_deletion_service, semantic_search
    from app.worker import tasks

    project_id = uuid.uuid4()
    doomed, kept = uuid.uuid4(), uuid.uuid4()
    store = _Runs(project_id, {doomed: 10, kept: 5})
    db = _wire_summary(monkeypatch, store)

    assert asyncio.run(_summary(db, project_id)) == 15
    assert asyncio.run(_summary(db, project_id)) == 15
    assert store.queries == 2, "the second read was served from the cache"

    # Delete ``doomed`` through the real Celery task body.
    run = SimpleNamespace(id=doomed, project_id=project_id, status="PASSED", minio_prefix=None)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def execute(self, *_a, **_k):
            return SimpleNamespace(scalar_one_or_none=lambda: run)

        async def commit(self):
            return None

    async def _perform(db, *, run, **_k):
        store.runs.pop(run.id)
        return {"postgres": {"runs": 1}}

    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(mongo_module, "get_mongo_db", lambda: object())
    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: object())
    monkeypatch.setattr(run_deletion_service, "execution_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr(run_deletion_service, "perform_run_deletion", _perform)
    monkeypatch.setattr(semantic_search, "purge_run_documents", AsyncMock(return_value=0))
    monkeypatch.setattr(deletion_job_service, "close_job", AsyncMock(return_value=True))
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    tasks.delete_run_everywhere.run(str(doomed))

    assert asyncio.run(_summary(db, project_id)) == 5, (
        "the dashboard still counts the deleted run: its cache entry outlived "
        "the deletion"
    )


async def test_dashboard_summary_with_redis_down_is_still_correct(redis, monkeypatch):
    project_id = uuid.uuid4()
    store = _Runs(project_id, {uuid.uuid4(): 7})
    db = _wire_summary(monkeypatch, store)
    redis.down = True

    assert await _summary(db, project_id) == 7
    store.runs[uuid.uuid4()] = 3
    assert await _summary(db, project_id) == 10, "nothing may be served from the cache"
    assert store.queries == 4
    assert not [c for c in redis.calls if c[0] == "set"]


async def test_a_mutation_during_the_query_is_not_cached_as_fresh(redis, monkeypatch):
    """The epoch is read ONCE, before the query. A mutation that commits and
    bumps while the query runs leaves the old snapshot under the old epoch,
    which no later reader asks for."""
    project_id = uuid.uuid4()
    store = _Runs(project_id, {uuid.uuid4(): 4})

    async def _concurrent_delete():
        if store.queries == 1:  # during the first summary's query only
            store.runs.clear()
            await cache_service.bump_analytics_epoch(project_id)

    db = _wire_summary(monkeypatch, store, during_query=_concurrent_delete)

    assert await _summary(db, project_id) == 4  # computed from the old snapshot
    assert await _summary(db, project_id) == 0, (
        "the old snapshot was cached under the post-bump epoch"
    )


async def test_all_projects_summary_follows_any_project_bump(redis, monkeypatch):
    from app.services.metrics_service import get_dashboard_summary

    store = _Runs(None, {uuid.uuid4(): 2})
    db = _wire_summary(monkeypatch, store)

    first = await get_dashboard_summary(db, None, days=7)
    store.runs[uuid.uuid4()] = 5
    await cache_service.bump_analytics_epoch(uuid.uuid4())
    second = await get_dashboard_summary(db, None, days=7)

    assert first["total_executions_7d"]["value"] == 2
    assert second["total_executions_7d"]["value"] == 7


async def test_hours_saved_model_keys_on_the_epoch(redis, monkeypatch):
    """The second consumer: value_metrics_service.get_hours_saved_model."""
    from app.services import value_metrics_service as vms

    project_id = uuid.uuid4()
    monkeypatch.setattr(
        vms, "get_effective_assumptions", AsyncMock(return_value=vms.EffectiveAssumptions())
    )
    run_span = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(vms, "_run_span", run_span)

    await vms.get_hours_saved_model(MagicMock(), project_id, months=6)
    await vms.get_hours_saved_model(MagicMock(), project_id, months=6)
    assert run_span.await_count == 1, "second read served from the cache"

    await cache_service.bump_analytics_epoch(project_id)
    await vms.get_hours_saved_model(MagicMock(), project_id, months=6)
    assert run_span.await_count == 2, "a bumped epoch must miss"
