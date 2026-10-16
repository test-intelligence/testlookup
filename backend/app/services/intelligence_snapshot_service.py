"""
Run Intelligence Snapshot Service — read-through caching.

Provides:
  - get_or_compute: returns a CURRENT payload, recomputing when the cached row
    is stale or at a superseded schema_version. Export paths must use this
    rather than reading the table directly.
  - save_snapshot: persists a computed intelligence payload
  - mark_stale: flags a snapshot as stale (triggers re-compute on next read)
  - invalidate: deletes a snapshot (used after re-analysis)
  - rebuild_snapshot: recompute + save after a pipeline's invalidate (worker only)
  - has_servable_snapshot: the rebuild task's idempotency check

``test_runs.ai_recommendation`` (W07 FAIL-05, migration 0214) is the PROJECTION
of the run's servable snapshot: ``run_verdict.ai_recommendation_of`` of the
payload ``save_snapshot`` stores, written in the snapshot's own transaction,
cleared by both deletes (``invalidate`` / ``stage_invalidate``), untouched by
``mark_stale`` (a stale snapshot is still served). These are its only writers
(``tests/regression/test_ai_recommendation_follows_the_snapshot.py``); each one
leaves ``test_runs.updated_at`` where it was, because live-run recovery picks
its candidates by ``updated_at``.

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
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentMemoryEntry, RunIntelligenceSnapshot, TestRun
from app.services.agent_memory_service import _active_memory_filters, build_memory_reference
from app.services.run_verdict import ai_recommendation_of

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


async def get_or_compute(db: AsyncSession, run_id: uuid.UUID) -> dict:
    """Return a CURRENT snapshot payload, recomputing when the cached row cannot serve.

    This module's docstring has advertised ``get_or_compute`` since it was
    written and never defined it. Both export paths hand-rolled their own
    ``select(RunIntelligenceSnapshot).where(run_id == ...)`` instead — with no
    ``stale`` predicate and no ``schema_version`` predicate — so a shared report
    link and a compliance evidence bundle would serve:

    * a **stale** payload, i.e. the verdict as it stood before the defect
      promotion or override that invalidated it; and
    * an **obsolete** payload at a superseded ``schema_version``, which
      ``get_stale_snapshot`` explicitly refuses to serve because it has the
      WRONG SHAPE — "a payload the current contract says cannot exist".

    Those are the two most exposed surfaces in the product: one is readable by
    anyone holding a share-link token, the other is what an auditor is handed.

    Recompute rather than refuse, so a link that worked keeps working. Failures
    propagate: an export that cannot produce current evidence must fail rather
    than quietly ship superseded evidence.

    The write uses its own session. ``save_snapshot`` commits, and an export is
    a read path — committing the caller's transaction from inside it would be a
    surprising side effect of rendering a report.
    """
    cached = await get_cached_snapshot(db, run_id)
    if cached is not None:
        return cached

    from app.db.mongo import get_mongo_db
    from app.services.run_intelligence_service import get_run_intelligence

    payload = await get_run_intelligence(run_id, db, get_mongo_db())

    try:
        from app.db.postgres import AsyncSessionLocal as _AsyncSessionLocal
        from app.db.postgres import release_read_connection

        provenance = payload.get("provenance") if isinstance(payload, dict) else None
        fallback = (
            provenance.get("fallback_used", False) if isinstance(provenance, dict) else False
        )
        # One connection at a time: the caller's read session hands its own
        # back before the write session takes one (2 + 1 pool).
        await release_read_connection(db)
        async with _AsyncSessionLocal() as write_db:
            await save_snapshot(write_db, run_id, payload, fallback_used=fallback)
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        logger.warning(
            "Could not cache the recomputed snapshot for run %s: %s", run_id, exc
        )

    return payload


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

    A payload whose ``defect_candidates_pending`` is set is not cached: its
    candidates are the heuristic stand-in while the LLM build runs in the
    background, and caching it would serve that stand-in as the answer.
    """
    if isinstance(payload, dict) and payload.get("defect_candidates_pending"):
        logger.debug("Not caching run %s: defect candidates still building", run_id)
        return
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
    # The list's AI-verdict column, from the payload exactly as stored, in the
    # snapshot's own transaction (W07 FAIL-05). Written BEFORE the snapshot row
    # is: every writer locks the run's row first, then the snapshot's -- the
    # order a run deletion takes them in (ON DELETE CASCADE), so the two never
    # wait on each other in opposite orders.
    await _set_ai_recommendation(db, run_id, ai_recommendation_of(payload))

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


async def _set_ai_recommendation(db: AsyncSession, run_id: uuid.UUID, value: Optional[str]) -> None:
    """Stage ``test_runs.ai_recommendation = value`` for one run, leaving ``updated_at`` as it is.

    ``TestRun.updated_at`` carries ``onupdate=func.now()``; setting it to itself
    keeps a projection write from making the run a live-recovery candidate again
    (``live_run_recovery_service`` selects by ``updated_at``).
    """
    # analytics-epoch: none — sets ai_recommendation only; the /runs list is not an analytics read
    await db.execute(
        update(TestRun)
        .where(TestRun.id == run_id)
        .values(ai_recommendation=value, updated_at=TestRun.updated_at)
        .execution_options(synchronize_session=False)
    )


async def has_servable_snapshot(db: AsyncSession, run_id: uuid.UUID) -> bool:
    """A snapshot the reads would serve exists (fresh or stale, ``schema_version`` current).

    The worker's rebuild task asks this first, so a duplicate enqueue, or a run
    whose Analysis tab was opened in the meantime, costs one indexed read
    (``ix_ris_run_id``) and nothing else.
    """
    result = await db.execute(
        select(RunIntelligenceSnapshot.id)
        .where(
            RunIntelligenceSnapshot.run_id == run_id,
            RunIntelligenceSnapshot.schema_version >= CURRENT_SCHEMA_VERSION,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def rebuild_snapshot(
    db: AsyncSession,
    run_id: uuid.UUID,
    mongo,
    *,
    build_wait_seconds: float = 0.0,
) -> bool:
    """Recompute the run's intelligence and save it as its snapshot. Never raises.

    Called by the worker task ``rebuild_run_snapshot``, which the pipeline task
    queues right after its ``invalidate`` (the CALLER invalidates first: a
    completed pipeline replaces the analysis wholesale), on that task's own
    session -- never on an API request path.
    Without it nothing rebuilt the snapshot until someone opened the run's
    Analysis tab, and the Runs list (which reads stored snapshots only) showed
    "no verdict" for exactly the runs people look at first.

    A payload with ``defect_candidates_pending`` is never cached (``save_snapshot``
    skips it). A run's FIRST intelligence read is pending whenever it has failure
    clusters and no persisted candidates -- the read schedules their build in the
    background -- which is most freshly analysed failed runs. So, given a
    ``build_wait_seconds`` budget, the rebuild waits (bounded) for that build,
    with the session's connection handed back meanwhile, and reads once more.
    Inside a worker task the build would otherwise be cancelled with the task's
    other stragglers when it ends (``worker/loop_runner._cancel_stragglers``).

    Returns True when a snapshot was saved; False when the payload stayed
    pending or anything failed. A failure is logged with the exception CLASS only
    and leaves the run as it is (no snapshot).
    """
    try:
        from app.services.run_intelligence_service import get_run_intelligence

        payload = await get_run_intelligence(run_id, db, mongo)
        if isinstance(payload, dict) and payload.get("defect_candidates_pending") and build_wait_seconds > 0:
            if await _wait_for_defect_candidate_builds(db, build_wait_seconds):
                payload = await get_run_intelligence(run_id, db, mongo)
        if not isinstance(payload, dict) or payload.get("defect_candidates_pending"):
            # save_snapshot would skip it too; say so without a write.
            return False
        provenance = payload.get("provenance")
        fallback = provenance.get("fallback_used", False) if isinstance(provenance, dict) else False
        await save_snapshot(db, run_id, payload, fallback_used=fallback)
        return True
    except Exception as exc:  # noqa: BLE001 — a warm-up is best effort
        logger.warning(
            "snapshot_rebuild_failed",
            extra={"run_id": str(run_id), "error_type": type(exc).__name__},
        )
        return False


async def _wait_for_defect_candidate_builds(db: AsyncSession, timeout: float) -> bool:
    """Wait up to ``timeout`` s for the in-flight background defect-candidate builds.

    True when every one finished in time (or none was in flight). The session's
    connection is handed back first: the build takes its own, and an idle hold
    for the whole wait would cost the worker's small pool a connection for nothing.
    """
    import asyncio

    from app.db.postgres import release_read_connection
    from app.services import run_intelligence_service

    tasks = [t for t in getattr(run_intelligence_service, "_DEFECT_BUILD_TASKS", ()) if not t.done()]
    if not tasks:
        return True
    await release_read_connection(db)
    _, still_running = await asyncio.wait(tasks, timeout=timeout)
    return not still_running


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
    # No snapshot, no list verdict: staged with the delete (W07 FAIL-05); the
    # run's row first, then the snapshot's (``save_snapshot``'s lock order).
    await _set_ai_recommendation(db, run_id, None)
    result = await db.execute(
        delete(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    # ``Result`` does not declare ``rowcount`` (only ``CursorResult`` does), so
    # reading it directly is two mypy errors: an unknown attribute and an Any
    # return. The sibling ``invalidate`` below carries both in the baseline;
    # this one does not add to it.
    deleted: int = getattr(result, "rowcount", 0) or 0
    return deleted > 0


async def invalidate(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Delete a snapshot entirely (used after re-analysis). Returns True if deleted."""
    # No snapshot, no list verdict: in the delete's transaction (W07 FAIL-05);
    # the run's row first, then the snapshot's (``save_snapshot``'s lock order).
    await _set_ai_recommendation(db, run_id, None)
    result = await db.execute(
        delete(RunIntelligenceSnapshot).where(
            RunIntelligenceSnapshot.run_id == run_id,
        )
    )
    await db.commit()
    return result.rowcount > 0
