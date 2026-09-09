"""Unit tests for the per-project ingestion-shard router (Phase 2.4).

The router is a pure pair of functions — no DB, no Redis — so the
tests are equally light. What we pin:

  1. ``shard_for_project`` is deterministic across runs/processes.
  2. The distribution is roughly uniform across the shard count.
  3. ``queue_for_project`` returns the legacy single queue when
     ``LIVE_INGEST_SHARD_COUNT == 0`` (back-compat for tests + deploys
     that haven't provisioned shard workers yet).
  4. ``queue_for_project`` returns ``ingestion.shard.<i>`` with the
     correct index when sharding is enabled.
  5. ``all_shard_queues`` matches the ``queue_for_project`` namespace.
  6. Empty / missing ``project_id`` collapses deterministically to
     shard 0 (matches the legacy single-queue path).
"""
from __future__ import annotations

from collections import Counter

import pytest


@pytest.fixture
def enable_sharding(monkeypatch):
    """Force the shard count to a fixed value so the test isn't
    affected by env / settings defaults drift."""
    from app.core import config
    monkeypatch.setattr(config.settings, "LIVE_INGEST_SHARD_COUNT", 8)


@pytest.fixture
def disable_sharding(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "LIVE_INGEST_SHARD_COUNT", 0)


def test_shard_for_project_is_deterministic(enable_sharding):
    from app.worker.ingestion_routing import shard_for_project

    # Same input → same shard, every call. This is the contract that
    # lets the producer derive routing without coordination.
    project = "11111111-1111-1111-1111-111111111111"
    repeated = {shard_for_project(project) for _ in range(50)}
    assert len(repeated) == 1


def test_explicit_count_helper_preserves_existing_routing_contract():
    from app.worker.ingestion_routing import shard_for_count

    assert shard_for_count("11111111-1111-1111-1111-111111111111", 8) == 2
    assert shard_for_count("proj-1", 8) == 3
    assert shard_for_count("all-projects", 8) == 3


def test_modulo_growth_from_eight_to_nine_remaps_most_projects():
    from app.worker.ingestion_routing import shard_for_count

    projects = [f"project-{index:05d}" for index in range(10_000)]
    moved = sum(
        shard_for_count(project, 8) != shard_for_count(project, 9)
        for project in projects
    )

    # Fixed corpus pins the real algorithm: 8 -> 9 moves about 8/9, not 1/8.
    assert moved == 8_918


def test_shard_change_queue_union_covers_old_and_new_counts():
    from app.worker.ingestion_routing import shard_change_queue_union

    expected = ["ingestion", *[f"ingestion.shard.{index}" for index in range(9)]]
    assert shard_change_queue_union(8, 9) == expected
    assert shard_change_queue_union(9, 8) == expected
    assert shard_change_queue_union(0, 0) == ["ingestion"]


def test_shard_for_project_distributes_uniformly(enable_sharding):
    """Hash-based routing must spread projects across the shard count
    without obvious skew. With 1000 random project IDs and 8 shards,
    every shard should land within ±50% of the expected uniform share
    (125). A wider tolerance keeps the test stable against MD5's
    bit-pattern variance; we're checking "not catastrophically skewed",
    not "perfectly uniform"."""
    import uuid as _uuid
    from app.worker.ingestion_routing import shard_for_project

    samples = [str(_uuid.uuid4()) for _ in range(1000)]
    buckets = Counter(shard_for_project(p) for p in samples)
    expected = 1000 / 8
    for shard, count in buckets.items():
        assert expected * 0.5 <= count <= expected * 1.5, (
            f"shard {shard} got {count} samples — outside ±50% of {expected}"
        )


def test_queue_for_project_returns_legacy_when_sharding_disabled(disable_sharding):
    from app.worker.ingestion_routing import queue_for_project, LEGACY_INGESTION_QUEUE

    # Even with a valid-looking project_id, no shards = fall back.
    assert queue_for_project("proj-1") == LEGACY_INGESTION_QUEUE


def test_queue_for_project_uses_shard_prefix_when_enabled(enable_sharding):
    from app.worker.ingestion_routing import queue_for_project, SHARD_QUEUE_PREFIX

    name = queue_for_project("proj-1")
    assert name.startswith(SHARD_QUEUE_PREFIX), name
    # The suffix is a valid shard index (0..7) for an 8-shard setup.
    suffix = name[len(SHARD_QUEUE_PREFIX):]
    assert suffix.isdigit() and 0 <= int(suffix) < 8


def test_all_shard_queues_enumerates_every_shard(enable_sharding):
    from app.worker.ingestion_routing import all_shard_queues, queue_for_project

    queues = all_shard_queues()
    assert len(queues) == 8
    # Every queue follows the prefix convention used by ``queue_for_project``.
    assert all(q.startswith("ingestion.shard.") for q in queues)
    # Indexes 0..7 are present exactly once.
    suffixes = sorted(int(q.rsplit(".", 1)[-1]) for q in queues)
    assert suffixes == list(range(8))
    # Sanity: producer-side ``queue_for_project`` cannot route to a queue
    # the worker side doesn't list.
    assert queue_for_project("proj-1") in queues


def test_all_shard_queues_empty_when_disabled(disable_sharding):
    from app.worker.ingestion_routing import all_shard_queues

    assert all_shard_queues() == []


def test_empty_project_id_collapses_to_shard_zero(enable_sharding):
    """Empty / None / falsy project_id is benign — it lands on shard 0
    so the routing decision stays deterministic even when the caller
    forgot to set the project (tests, fallback paths)."""
    from app.worker.ingestion_routing import shard_for_project

    assert shard_for_project("") == 0
