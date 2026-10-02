"""Regression guard: every API worker freezes its start-up heap, once.

The gap
-------
An API worker finishes start-up with ~700,000 GC-tracked objects that live as
long as the process, and nothing told CPython's collector so. Every full
(generation-2) collection walked them all again: 180-260 ms each, about every
7-12 requests of a 3,520-row heatmap, and the entire p95 tail of those
responses on Linux (Wave 3 perf investigation).

The fix (``app/core/startup_gc.py``): the last start-up step of
``app.main.lifespan`` collects and then ``gc.freeze()``-s the heap, so full
collections only walk what was allocated after start-up.

These tests run the REAL lifespan (its network-bound steps stubbed) and read
CPython's own freeze count, instead of asserting the source mentions a call.
"""
from __future__ import annotations

import gc
import weakref

import pytest

import app.main as main
from app.core import startup_gc

pytestmark = pytest.mark.regression


@pytest.fixture
def clean_gc_state(monkeypatch):
    """Start unfrozen with the once-per-process latch open; leave it that way.

    ``gc.unfreeze()`` afterwards returns the objects to the oldest generation,
    and the thresholds are put back, so the rest of the test session collects
    exactly as it did before.
    """
    thresholds = gc.get_threshold()
    gc.unfreeze()
    monkeypatch.setattr(startup_gc, "_frozen_in_this_process", False)
    yield
    gc.unfreeze()
    gc.set_threshold(*thresholds)


@pytest.fixture
def quiet_lifespan(monkeypatch, clean_gc_state):
    """Stub every lifespan step that would reach Mongo, Redis, Postgres or DNS."""

    async def _noop(*_args, **_kwargs):
        return None

    class _Idle:
        async def initialize(self):
            return None

        async def run(self):
            return None

    import app.services.local_embedder_guard as guard
    import app.streams.live_consumer as live_consumer
    import app.streams.live_fanout as live_fanout

    monkeypatch.setattr(guard, "install_offline_embedder_guard", lambda: None)
    monkeypatch.setattr(main, "ensure_mongo_indexes", _noop)
    monkeypatch.setattr(main, "_warn_about_refused_notification_destinations", _noop)
    monkeypatch.setattr(live_consumer, "LiveEventStreamConsumer", _Idle)
    monkeypatch.setattr(live_fanout, "LiveFanoutSubscriber", _Idle)
    for name in ("close_db", "close_mongo", "close_redis", "close_http_client"):
        monkeypatch.setattr(main, name, _noop)

    calls: list[int | None] = []
    real = startup_gc.freeze_startup_heap

    def _spy():
        result = real()
        calls.append(result)
        return result

    monkeypatch.setattr(main, "freeze_startup_heap", _spy)
    return calls


async def test_the_lifespan_freezes_the_heap_before_serving(quiet_lifespan):
    # Not 0: CPython itself keeps a few hundred objects in the permanent
    # generation (377 on 3.12/Windows, back after any gc.unfreeze()).
    before = gc.get_freeze_count()
    assert len(gc.get_objects(generation=2)) > 10_000
    async with main.lifespan(main.app):
        # The application is serving here: the import-time heap must already
        # be out of the collector's generations.
        frozen = gc.get_freeze_count()
        assert frozen - before > 10_000, (before, frozen)
        # One freeze. Its own count can sit a few objects above ours: frozen
        # objects still die by refcount.
        [reported] = quiet_lifespan
        assert reported is not None and reported >= frozen
        # Generation 2 no longer holds the heap a full collection used to walk.
        assert len(gc.get_objects(generation=2)) < frozen / 10


async def test_a_second_start_up_in_the_same_process_does_not_freeze_again(quiet_lifespan):
    async with main.lifespan(main.app):
        frozen = gc.get_freeze_count()

    # Long-lived-looking objects allocated after start-up: a second freeze
    # would move them into a generation that is never collected.
    allocated_after_start_up = [[i] for i in range(5_000)]
    async with main.lifespan(main.app):
        # Frozen objects can still die by refcount, so the count may drop; it
        # must not grow by what was allocated after the first start-up.
        assert gc.get_freeze_count() <= frozen
    first, second = quiet_lifespan
    assert first is not None and first >= frozen
    assert second is None
    del allocated_after_start_up


def test_garbage_is_collected_not_frozen(clean_gc_state):
    """A dead cycle alive at freeze time would never be reclaimed."""

    class Node:
        pass

    node = Node()
    node.self = node
    dead = weakref.ref(node)
    del node
    assert dead() is not None  # only the cycle collector can free it

    frozen = startup_gc.freeze_startup_heap()

    assert dead() is None
    assert frozen == gc.get_freeze_count() > 0


def test_freeze_keeps_cpython_default_thresholds(clean_gc_state):
    """The thresholds were measured and deliberately left alone."""
    before = gc.get_threshold()
    startup_gc.freeze_startup_heap()
    assert gc.get_threshold() == before
