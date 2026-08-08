"""The semantic cache must honour SEMANTIC_CACHE_MAX_DOCUMENTS.

    SEMANTIC_CACHE_MAX_DOCUMENTS: int = 10000   # cap ChromaDB collection size

Nothing read it. `semantic_cache_store` upserted unconditionally, so the
per-tenant ChromaDB collection grew for the life of the deployment — a documented
cap that existed only as a comment, and a test elsewhere asserting its *default
value* while no code consumed it.

Eviction is oldest-first by the `cached_at` metadata the store path already
writes, pruning to 90% of the cap so it runs in occasional batches rather than on
every store once at the ceiling.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("app.services.semantic_cache")

from app.services import semantic_cache  # noqa: E402

pytestmark = pytest.mark.regression


class FakeCollection:
    """Minimal stand-in for a ChromaDB collection."""

    def __init__(self, n: int, *, bad_meta: bool = False):
        self._ids = [f"id{i:05d}" for i in range(n)]
        # Oldest first by timestamp; id ordering is deliberately NOT the same as
        # age ordering for the shuffled case below.
        self._meta = {
            i: {"cached_at": f"2026-01-{(idx % 28) + 1:02d}T00:00:00+00:00"}
            for idx, i in enumerate(self._ids)
        }
        if bad_meta and self._ids:
            self._meta[self._ids[0]] = {}          # missing cached_at
        self.deleted: list[str] = []

    def count(self):
        return len(self._ids)

    def get(self, include=None):
        return {"ids": list(self._ids), "metadatas": [self._meta[i] for i in self._ids]}

    def delete(self, ids=None):
        self.deleted.extend(ids or [])
        self._ids = [i for i in self._ids if i not in set(ids or [])]


def _enforce(collection):
    asyncio.run(semantic_cache._enforce_size_cap(collection))


class TestTheCapIsEnforced:
    def test_over_cap_evicts_down_to_the_prune_target(self, monkeypatch):
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 100, raising=False)
        c = FakeCollection(150)
        _enforce(c)
        assert c.count() == 90, f"expected prune to 90% of 100, got {c.count()}"
        assert len(c.deleted) == 60

    def test_under_cap_evicts_nothing(self, monkeypatch):
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 100, raising=False)
        c = FakeCollection(50)
        _enforce(c)
        assert c.deleted == []
        assert c.count() == 50

    def test_exactly_at_cap_evicts_nothing(self, monkeypatch):
        """Boundary: `> cap`, not `>= cap`."""
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 100, raising=False)
        c = FakeCollection(100)
        _enforce(c)
        assert c.deleted == []

    def test_zero_means_uncapped(self, monkeypatch):
        """An operator setting 0 is choosing 'no cap', not 'evict everything'."""
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 0, raising=False)
        c = FakeCollection(5000)
        _enforce(c)
        assert c.deleted == []


class TestWhatGetsEvicted:
    def test_oldest_first(self, monkeypatch):
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 10, raising=False)
        c = FakeCollection(20)
        oldest = sorted(c._ids, key=lambda i: c._meta[i]["cached_at"])[:11]
        _enforce(c)
        assert set(c.deleted) == set(oldest), "eviction did not follow cached_at order"

    def test_missing_timestamp_is_evicted_first(self, monkeypatch):
        """A row with no cached_at must not become immortal."""
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 10, raising=False)
        c = FakeCollection(20, bad_meta=True)
        _enforce(c)
        assert "id00000" in c.deleted


class TestItNeverBreaksTheCaller:
    def test_a_failing_collection_does_not_raise(self, monkeypatch):
        """Cache upkeep must never fail the analysis that triggered it."""
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 10, raising=False)

        class Exploding(FakeCollection):
            def count(self):
                raise RuntimeError("chroma is down")

        _enforce(Exploding(50))  # must not raise

    def test_delete_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(semantic_cache.settings, "SEMANTIC_CACHE_MAX_DOCUMENTS", 10, raising=False)

        class BadDelete(FakeCollection):
            def delete(self, ids=None):
                raise RuntimeError("delete refused")

        _enforce(BadDelete(50))  # must not raise


def test_store_actually_calls_the_cap():
    """The cap is worthless if the store path never invokes it."""
    import inspect

    src = inspect.getsource(semantic_cache.semantic_cache_store)
    assert "_enforce_size_cap" in src, (
        "semantic_cache_store no longer enforces the size cap — the collection "
        "grows unbounded again"
    )
