"""Per-project Celery routing for live-stream persist tasks (Phase 2.4).

Before this module, ``persist_live_session`` for every project landed on
a single shared ``ingestion`` queue. Under load that means one project's
queue depth blocks every other project's — the fairness problem the
Phase 2 design solves by partitioning into N shards keyed by
``hash(project_id) mod N``.

Why hash-based routing (not round-robin or LRU)?

* **Determinism.** Every batch from the same project lands on the same
  shard, so per-project ordering inside the worker is preserved. A
  round-robin scheme would race across workers.
* **No coordination.** The producer derives the shard locally; no
  Redis round-trip, no zookeeper-style election, no broker query.
* **Stable under scale-out.** Adding shard N+1 only re-homes 1/N of
  the existing project traffic, which is the consistent-hashing-style
  property we want for graceful capacity additions.

The shard count is configurable via ``settings.LIVE_INGEST_SHARD_COUNT``.
Setting it to 0 falls back to the legacy single-queue routing (used by
tests and any deployment that doesn't want to provision N worker
deployments yet).

Operators add a shard by spinning up a worker pod that subscribes to
``ingestion.shard.<i>`` and ``ingestion`` (legacy). The base
``ingestion`` queue stays active for ``ingest_test_run`` and any other
non-shardable tasks.
"""
from __future__ import annotations

import hashlib

from app.core.config import settings


# Base queue name — kept stable so existing Celery beat / monitoring
# tooling that watches ``ingestion`` still has something to watch even
# when shard routing is enabled. New shardable tasks use the
# ``ingestion.shard.<i>`` namespace.
LEGACY_INGESTION_QUEUE = "ingestion"
SHARD_QUEUE_PREFIX = "ingestion.shard."


def shard_count() -> int:
    """Effective shard count for the current process. Always >= 0.

    Reading the setting through this helper (rather than inlining
    ``settings.LIVE_INGEST_SHARD_COUNT`` at each call site) means a
    future env-var hot-reload only has to invalidate one cache.
    """
    n = settings.LIVE_INGEST_SHARD_COUNT
    return n if n and n > 0 else 0


def shard_for_project(project_id: str) -> int:
    """Return the shard index (0..N-1) for ``project_id``.

    Uses MD5 to map any string to an evenly-distributed integer bucket.
    Cryptographic strength is irrelevant here; what matters is that:

    * The mapping is **stable** across processes and restarts.
    * The distribution is **uniform** so no single shard gets a
      disproportionate share of traffic.

    ``project_id`` is whatever the producer is willing to pass — UUID
    strings, "all-projects", or arbitrary labels for non-project tasks.
    The function tolerates any input; an empty / falsy id collapses to
    shard 0 deterministically (matches the legacy single-queue path
    when shards are disabled).
    """
    if not project_id:
        return 0
    digest = hashlib.md5(project_id.encode("utf-8")).digest()
    # Use the first 4 bytes as an unsigned int; mod by shard count.
    # Avoids ``int(hex, 16)`` allocations on the hot path.
    n = shard_count()
    if n <= 0:
        return 0
    return int.from_bytes(digest[:4], "big") % n


def queue_for_project(project_id: str) -> str:
    """Return the Celery queue name that ``project_id``'s tasks should
    route to. Falls back to the legacy single-queue name when sharding
    is disabled (``LIVE_INGEST_SHARD_COUNT=0``).

    Call sites pass the result as ``queue=...`` to ``apply_async`` so
    the routing decision is made by the producer, not the broker.
    Worker pods subscribe to whichever subset of queues they own.
    """
    if shard_count() <= 0:
        return LEGACY_INGESTION_QUEUE
    return f"{SHARD_QUEUE_PREFIX}{shard_for_project(project_id)}"


def all_shard_queues() -> list[str]:
    """Enumerate every ingestion-shard queue name. Used by:

    * worker startup to subscribe to its assigned subset.
    * the ``/health/ingestion`` endpoint to surface per-shard depth.
    * tests to assert the routing-table shape.

    Returns an empty list when sharding is disabled; callers should
    treat that as "use the legacy ingestion queue".
    """
    n = shard_count()
    if n <= 0:
        return []
    return [f"{SHARD_QUEUE_PREFIX}{i}" for i in range(n)]
