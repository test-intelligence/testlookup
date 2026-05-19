"""Behavioral tests for the pipeline dedup-vs-retry contract (Phase J #1).

Before the fix the wrapper in ``worker/tasks.py::run_agent_pipeline`` set
a Redis key with ``SET NX`` and then, on retry, treated its own surviving
lock as proof of a duplicate — so a transiently-failed pipeline was
silently *skipped* by Celery's retry instead of being re-run.

The fix is two-fold and both halves matter; this file pins them:

  1. ``_is_duplicate(key, ttl, owner=...)`` lets the **same** owner
     (same Celery task id) reacquire the lock. Different owners still
     get a duplicate verdict. The TTL is refreshed on each successful
     reacquire so the lock survives a slow retry train.
  2. ``_release_duplicate_lock(key, owner)`` deletes the key only when
     the caller still owns it — a stale owner can't blow away a
     fresher attempt's lock.

Tests are direct unit tests of the two helpers using a minimal Redis
stand-in. The shared ``FakeRedis`` fixture in ``conftest.py`` predates
the ``nx`` flag the helper uses, so we build a tiny purpose-built fake
that supports the exact subset of commands the contract requires —
``set`` with ``nx`` + ``ex``, ``get``, ``expire``, ``delete``.
"""
from __future__ import annotations

import pytest

from app.worker.tasks import _is_duplicate, _release_duplicate_lock


class _DedupRedis:
    """Minimal async Redis fake with ``SET NX`` + ``EX`` semantics.

    Only the four commands the dedup helpers touch are implemented; any
    other call surfaces a clear ``AttributeError`` so an accidental
    coupling to a wider Redis API gets caught fast.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._ttl: dict[str, int] = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self._data:
            return False
        # Mirror real Redis: encoding-agnostic — we store the original
        # value so the ``get`` round-trip returns what was set.
        self._data[key] = value
        if ex is not None:
            self._ttl[key] = ex
        return True

    async def get(self, key):
        return self._data.get(key)

    async def expire(self, key, seconds):
        if key not in self._data:
            return False
        self._ttl[key] = seconds
        return True

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                self._ttl.pop(k, None)
                removed += 1
        return removed

    # Test-only inspection
    def ttl_of(self, key):
        return self._ttl.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch ``get_redis`` to return our minimal fake.

    ``app.worker.tasks._is_duplicate`` does ``from app.db.redis_client
    import get_redis`` lazily, so we replace the function at the module
    where it is *defined* rather than where it is imported — this is the
    canonical fix for the late-binding-import pattern.
    """
    fake = _DedupRedis()
    import app.db.redis_client as redis_mod

    monkeypatch.setattr(redis_mod, "get_redis", lambda: fake)
    return fake


# ── _is_duplicate ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_attempt_is_not_a_duplicate(fake_redis):
    """An empty Redis → the first call sets the lock and returns False
    (i.e. not a duplicate, work should proceed)."""
    is_dup = await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    assert is_dup is False
    assert await fake_redis.get("pipeline:abc:offline") == "task-1"


@pytest.mark.asyncio
async def test_same_owner_can_reacquire_lock_after_retry(fake_redis):
    """Phase J #1 core contract: when Celery retries with the same task
    id (the default), the wrapper must NOT treat its own lock as a
    duplicate. Otherwise transient failures are silently skipped."""
    first = await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    assert first is False

    # Same owner — typical Celery retry. Must be allowed.
    second = await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    assert second is False, (
        "Same-owner reacquire must be allowed; otherwise legitimate "
        "retries are dropped on the floor (Phase J #1 regression)."
    )


@pytest.mark.asyncio
async def test_different_owner_is_treated_as_duplicate(fake_redis):
    """A concurrent enqueue with a fresh task id must see the existing
    lock as a duplicate and skip — that's still the desired behaviour
    for the actual dedup contract."""
    await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    is_dup = await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-2")
    assert is_dup is True


@pytest.mark.asyncio
async def test_owner_reacquire_refreshes_ttl(fake_redis):
    """A long-running retry chain must not lose its lock to TTL
    expiry. Each same-owner reacquire refreshes the TTL."""
    await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    # Simulate clock drift: the original TTL would be at 60s. After
    # reacquire with a larger TTL, the stored TTL should reflect the
    # refreshed value.
    await _is_duplicate("pipeline:abc:offline", ttl=7200, owner="task-1")
    assert fake_redis.ttl_of("pipeline:abc:offline") == 7200


@pytest.mark.asyncio
async def test_no_owner_uses_strict_dedup(fake_redis):
    """Callers that don't pass an owner (legacy paths) still get the
    classic strict-dedup behaviour — a second call is a duplicate."""
    first = await _is_duplicate("legacy:key", ttl=60)
    assert first is False
    second = await _is_duplicate("legacy:key", ttl=60)
    assert second is True


# ── _release_duplicate_lock ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_deletes_when_owner_matches(fake_redis):
    """After a pipeline raises, the wrapper releases its own lock so
    the retry can reacquire cleanly."""
    await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    await _release_duplicate_lock("pipeline:abc:offline", "task-1")
    assert await fake_redis.get("pipeline:abc:offline") is None


@pytest.mark.asyncio
async def test_release_is_a_noop_when_owner_does_not_match(fake_redis):
    """A late-arriving release from a stale task must not blow away a
    newer attempt's lock — otherwise two retries can race and the
    second one starts thinking the slot is free."""
    await _is_duplicate("pipeline:abc:offline", ttl=60, owner="task-1")
    await _release_duplicate_lock("pipeline:abc:offline", "stranger")
    # Lock still owned by task-1.
    assert await fake_redis.get("pipeline:abc:offline") == "task-1"


# ── End-to-end retry-after-failure flow ───────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_after_failure_is_not_skipped(fake_redis):
    """Phase J #1 acceptance: the full ``acquire → fail → release →
    retry-acquire`` cycle. The second call must not return
    ``duplicate=True`` — that was the bug."""
    key = "pipeline:run-xyz:offline"

    # Attempt 1: acquire, then simulate the pipeline raising.
    first = await _is_duplicate(key, ttl=7200, owner="task-1")
    assert first is False
    await _release_duplicate_lock(key, "task-1")

    # Attempt 2 (Celery retry — same task id, default behaviour):
    second = await _is_duplicate(key, ttl=7200, owner="task-1")
    assert second is False, (
        "Retry-after-failure must reacquire cleanly. Returning "
        "duplicate=True here drops the run on the floor."
    )


@pytest.mark.asyncio
async def test_concurrent_different_attempts_dedup_correctly(fake_redis):
    """Two distinct Celery task ids targeting the same run within the
    TTL window: the second must be blocked. (This is the desired
    behaviour we don't want to lose while fixing the retry bug.)"""
    key = "pipeline:run-xyz:offline"

    # Attempt A acquires.
    a = await _is_duplicate(key, ttl=7200, owner="task-A")
    assert a is False

    # Attempt B fires concurrently with a different task id — blocked.
    b = await _is_duplicate(key, ttl=7200, owner="task-B")
    assert b is True

    # If attempt A finishes cleanly and releases, the slot becomes
    # available for the next genuinely-new attempt.
    await _release_duplicate_lock(key, "task-A")
    c = await _is_duplicate(key, ttl=7200, owner="task-C")
    assert c is False
