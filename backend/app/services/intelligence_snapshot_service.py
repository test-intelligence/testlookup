"""
Run Intelligence Snapshot Service — read-through caching.

Provides:
  - get_or_compute: returns cached snapshot or computes live then caches
  - save_snapshot: persists a computed intelligence payload
  - mark_stale: flags a snapshot as stale (triggers re-compute on next read)
  - invalidate: deletes a snapshot (used after re-analysis)

Schema:
  Snapshots are stored in run_intelligence_snapshots with:
  - schema_version: tracks payload shape evolution
  - stale: boolean flag for triggering refresh
  - fallback_used: whether the snapshot was generated without LLM

Cache strategy:
  - Read: if snapshot exists and not stale, return it directly
  - Miss: compute live via get_run_intelligence, save snapshot, return
  - Stale: return stale data immediately, trigger async refresh
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentMemoryEntry, RunIntelligenceSnapshot
from app.services.agent_memory_service import _active_memory_filters, build_memory_reference

logger = logging.getLogger("services.intelligence_snapshot")

# Bump this whenever the snapshot PAYLOAD SHAPE changes. ``get_cached_snapshot``
# serves any row whose ``schema_version >= CURRENT_SCHEMA_VERSION``, so a shape
# change without a bump leaves every existing snapshot serving the old shape
# for as long as it lives.
#
# 3 -> 4: the ``run`` block gained ``broken_tests`` and ``unknown_tests``.
# Without this bump the fix was invisible on every already-analysed run —
# measured on the deployment right after shipping it: 36 snapshots sat at
# version 3, and the endpoint kept returning a payload with no
# ``broken_tests`` even though the corrected code was live in the container.
# 4 -> 5: the ``run`` block gained durable batch-ingestion completeness and
# bounded rejection metadata. Older snapshots must be recomputed so release
# evidence cannot look complete merely because it came from the cache.
CURRENT_SCHEMA_VERSION = 5


def _hash_json(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8", errors="ignore")).hexdigest()


def _reference_sort_key(reference: dict) -> tuple[str, str, str]:
    return (
        str(reference.get("entity_type") or ""),
        str(reference.get("entity_id") or ""),
        str(reference.get("memory_entry_id") or ""),
    )


def _memory_reference_id(reference: dict) -> str:
    return _hash_json({
        "memory_entry_id": reference.get("memory_entry_id"),
        "entity_type": reference.get("entity_type"),
        "entity_id": reference.get("entity_id"),
        "payload_sha256": reference.get("payload_sha256"),
    })


def build_snapshot_memory_reference_manifest(
    *,
    snapshot_id: uuid.UUID,
    run_id: uuid.UUID,
    memory_entries: list[AgentMemoryEntry],
) -> dict:
    """Build deterministic memory references attached to a run snapshot."""
    references = []
    for entry in sorted(
        memory_entries,
        key=lambda item: (
            str(item.entity_type or ""),
            str(item.entity_id or ""),
            str(item.id),
        ),
    ):
        reference = build_memory_reference(entry)
        reference["source_snapshot_id"] = str(snapshot_id)
        reference["memory_reference_id"] = _memory_reference_id(reference)
        references.append(reference)

    references = sorted(references, key=_reference_sort_key)
    return {
        "schema_version": 1,
        "snapshot_id": str(snapshot_id),
        "run_id": str(run_id),
        "memory_reference_count": len(references),
        "memory_reference_ids": [
            reference["memory_reference_id"] for reference in references
        ],
        "memory_graph_checksum_sha256": _hash_json(references),
        "memory_references": references,
    }


async def _load_snapshot_memory_entries(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> list[AgentMemoryEntry]:
    result = await db.execute(
        select(AgentMemoryEntry)
        .where(AgentMemoryEntry.run_id == run_id, *_active_memory_filters())
        .order_by(
            AgentMemoryEntry.entity_type,
            AgentMemoryEntry.entity_id,
            AgentMemoryEntry.id,
        )
    )
    return list(result.scalars().all())


async def attach_memory_reference_manifest(
    db: AsyncSession,
    *,
    snapshot_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: dict,
) -> dict:
    """Return a snapshot payload linked to the current memory graph."""
    enriched = copy.deepcopy(payload)
    memory_entries = await _load_snapshot_memory_entries(db, run_id)
    manifest = build_snapshot_memory_reference_manifest(
        snapshot_id=snapshot_id,
        run_id=run_id,
        memory_entries=memory_entries,
    )
    enriched["memory_reference_manifest"] = manifest
    enriched.setdefault("provenance", {})
    if isinstance(enriched["provenance"], dict):
        enriched["provenance"]["memory_graph_checksum_sha256"] = manifest[
            "memory_graph_checksum_sha256"
        ]
        enriched["provenance"]["memory_reference_count"] = manifest[
            "memory_reference_count"
        ]
    return enriched


async def get_cached_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[dict]:
    """
    Return cached intelligence payload if it exists and is fresh.
    Returns None if no cache exists or if stale.
    Returns the payload dict (not the ORM object).
    """
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
            RunIntelligenceSnapshot.stale.is_(False),
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot and snapshot.schema_version >= CURRENT_SCHEMA_VERSION:
        return snapshot.payload
    return None


async def get_stale_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> Optional[dict]:
    """Return a stale snapshot for immediate use while refresh is in progress.

    Stale and OBSOLETE are different problems, and only one of them is safe to
    serve. A stale snapshot has the right shape and out-of-date content —
    handing it over while a refresh runs is a reasonable latency trade. A
    snapshot at a superseded ``schema_version`` has the WRONG SHAPE, and
    serving it gives the consumer a payload the current contract says cannot
    exist.

    This path had no version filter, so it defeated the bump on its sibling:
    ``get_cached_snapshot`` correctly refused a version-3 row, the request
    fell through to here, and the same row was returned anyway with
    ``stale: true``. Measured live — the run-block fix was invisible through
    two deploys because of it.

    An obsolete row is therefore treated as absent, which makes the caller
    recompute.
    """
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
            RunIntelligenceSnapshot.schema_version >= CURRENT_SCHEMA_VERSION,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot:
        return snapshot.payload
    return None


async def save_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
    payload: dict,
    fallback_used: bool = False,
) -> None:
    """Save or update a snapshot for a run.

    The payload is coerced to JSON-native types first. ``payload`` is a JSON
    column, but the intelligence payload is assembled partly from MongoDB,
    which hands back BSON dates as real ``datetime`` objects — and asyncpg
    cannot serialise those, so the flush died with ``Object of type datetime
    is not JSON serializable``. Encoding at this boundary covers every field
    that reaches the column, including ones added later.
    """
    payload = jsonable_encoder(payload)
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    existing = result.scalar_one_or_none()
    snapshot_id = existing.id if existing else uuid.uuid4()
    payload = await attach_memory_reference_manifest(
        db,
        snapshot_id=snapshot_id,
        run_id=run_id,
        payload=payload,
    )

    if existing:
        existing.payload = payload
        existing.schema_version = CURRENT_SCHEMA_VERSION
        existing.fallback_used = fallback_used
        existing.stale = False
        existing.generated_at = datetime.now(timezone.utc)
    else:
        db.add(RunIntelligenceSnapshot(
            id=snapshot_id,
            run_id=run_id,
            schema_version=CURRENT_SCHEMA_VERSION,
            payload=payload,
            fallback_used=fallback_used,
            generated_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    logger.debug("Snapshot saved for run %s", run_id)


async def mark_stale(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Mark a snapshot as stale (triggers refresh on next read). Returns True if found.

    Stage-only: mutation is left pending on the current transaction. The
    caller (router handler) owns the commit so a stale flip can land in the
    same transaction as whatever triggered it (defect promotion, release
    override, etc.), keeping the freshness guarantee atomic.
    """
    result = await db.execute(
        select(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot:
        snapshot.stale = True
        return True
    return False


async def stage_invalidate(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Delete the snapshot, leaving the mutation PENDING for the caller to commit.

    ``invalidate`` commits; ``mark_stale`` is stage-only. Neither fits a human
    release override, which needs both properties at once:

    * it must land in the same transaction as the override itself, so a
      rolled-back override cannot drop a valid snapshot (rules out
      ``invalidate``);
    * it must leave NOTHING servable behind, so the next read is forced to
      recompute rather than being handed the superseded verdict (rules out
      ``mark_stale``).

    Marking stale is right for cheap, automatic staleness such as defect
    promotion, where serving a slightly old payload while a refresh converges
    is a fair latency trade. It is wrong here: a QA Lead has just written
    ``GO -> NO_GO`` and the stale row still says ``GO``. Serving that even once,
    to one CI poller, is the whole defect — reducing the window from "forever"
    to "one request" would not be a fix for a release gate.
    """
    result = await db.execute(
        delete(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    return result.rowcount > 0


async def invalidate(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Delete a snapshot entirely (used after re-analysis). Returns True if deleted."""
    result = await db.execute(
        delete(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    await db.commit()
    return result.rowcount > 0
