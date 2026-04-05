"""
Agent Memory Service — unified memory and retrieval for the AI agent stack (P3).

Provides:
  - persist_memory_entries: bulk-insert memory entries after a pipeline run
  - list_project_memories: paginated, project-scoped memory query
  - get_run_memory_timeline: all memory entries for a run, grouped by entity type
  - recall_similar: semantic similarity search across historical memories via ChromaDB
  - persist_pipeline_memory: extract and store memory from a completed pipeline state

The intelligence snapshot service remains the canonical source for full run
intelligence payloads. This service adds the *relationship graph* layer on top,
connecting runs to clusters, defects, release decisions, and ownership for
cross-run recall.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import AgentMemoryEntry

logger = logging.getLogger("services.agent_memory")

_MEMORY_COLLECTION = "agent_memory_vectors"
_SIMILARITY_THRESHOLD = 0.70  # lower than analysis cache — recall should be broader
_MAX_MEMORY_DOCUMENTS = 50000


# ── ChromaDB helpers ─────────────────────────────────────────────────────────


def _get_chroma_client():
    import chromadb
    return chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)


async def _get_or_create_collection():
    client = await asyncio.to_thread(_get_chroma_client)
    return await asyncio.to_thread(
        client.get_or_create_collection,
        _MEMORY_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


async def _index_memory_vector(entry_id: str, signature: str, metadata: dict) -> None:
    """Index a single memory entry in ChromaDB for vector recall."""
    try:
        collection = await _get_or_create_collection()
        doc_id = hashlib.sha256(f"{entry_id}:{signature}".encode()).hexdigest()[:32]
        await asyncio.to_thread(
            collection.upsert,
            ids=[doc_id],
            documents=[signature],
            metadatas=[{
                "entry_id": str(entry_id),
                "entity_type": metadata.get("entity_type", ""),
                "project_id": metadata.get("project_id", ""),
                "failure_category": metadata.get("failure_category", ""),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
    except Exception as exc:
        logger.debug("Memory vector index failed (non-critical): %s", exc)


async def _query_similar_vectors(
    signature: str,
    project_id: str,
    entity_type: Optional[str],
    limit: int,
) -> list[dict]:
    """Query ChromaDB for similar memory signatures, project-scoped."""
    try:
        collection = await _get_or_create_collection()

        where_filter: dict[str, Any] = {"project_id": project_id}
        if entity_type:
            where_filter["entity_type"] = entity_type

        results = await asyncio.to_thread(
            collection.query,
            query_texts=[signature],
            n_results=min(limit * 2, 100),  # over-fetch to filter
            where=where_filter if len(where_filter) > 1 else {"project_id": project_id},
            include=["distances", "metadatas"],
        )

        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        matches = []
        for i, dist in enumerate(distances):
            similarity = 1.0 - (dist / 2.0)
            if similarity >= _SIMILARITY_THRESHOLD:
                matches.append({
                    "entry_id": metadatas[i].get("entry_id"),
                    "similarity": round(similarity, 4),
                })
        return matches[:limit]

    except Exception as exc:
        logger.debug("Memory vector query failed (non-critical): %s", exc)
        return []


# ── Core service functions ───────────────────────────────────────────────────


async def persist_memory_entries(
    db: AsyncSession,
    entries: list[dict],
) -> int:
    """
    Bulk-insert memory entries. Each dict should contain:
      project_id, run_id, pipeline_run_id (optional),
      entity_type, entity_id, error_signature (optional),
      failure_category (optional), root_cause_summary (optional),
      payload (optional), confidence (optional), resolution (optional)

    Returns the number of entries persisted.
    """
    if not entries:
        return 0

    count = 0
    for entry_data in entries:
        entry = AgentMemoryEntry(
            project_id=entry_data["project_id"],
            run_id=entry_data["run_id"],
            pipeline_run_id=entry_data.get("pipeline_run_id"),
            entity_type=entry_data["entity_type"],
            entity_id=str(entry_data["entity_id"]),
            error_signature=entry_data.get("error_signature"),
            failure_category=entry_data.get("failure_category"),
            root_cause_summary=entry_data.get("root_cause_summary"),
            payload=entry_data.get("payload"),
            confidence=entry_data.get("confidence"),
            resolution=entry_data.get("resolution"),
        )
        db.add(entry)
        count += 1

        # Index in ChromaDB if there's a searchable signature
        sig = entry_data.get("error_signature")
        if sig:
            asyncio.create_task(_index_memory_vector(
                str(entry.id),
                sig,
                {
                    "entity_type": entry_data["entity_type"],
                    "project_id": str(entry_data["project_id"]),
                    "failure_category": entry_data.get("failure_category", ""),
                },
            ))

    await db.commit()
    logger.info("Persisted %d memory entries", count)
    return count


async def list_project_memories(
    db: AsyncSession,
    project_id: uuid.UUID,
    entity_type: Optional[str] = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[AgentMemoryEntry], int]:
    """
    List memory entries for a project, optionally filtered by entity_type.
    Returns (entries, total_count).
    """
    base = select(AgentMemoryEntry).where(
        AgentMemoryEntry.project_id == project_id,
    )
    if entity_type:
        base = base.where(AgentMemoryEntry.entity_type == entity_type)

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    rows_q = base.order_by(AgentMemoryEntry.created_at.desc()).offset(
        (page - 1) * size
    ).limit(size)
    result = await db.execute(rows_q)
    entries = list(result.scalars().all())

    return entries, total


async def get_run_memory_timeline(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> tuple[dict[str, list[AgentMemoryEntry]], int]:
    """
    Get all memory entries for a run, grouped by entity_type.
    Returns (entries_by_type, total_count).
    """
    result = await db.execute(
        select(AgentMemoryEntry)
        .where(AgentMemoryEntry.run_id == run_id)
        .order_by(AgentMemoryEntry.created_at.asc())
    )
    entries = list(result.scalars().all())

    grouped: dict[str, list[AgentMemoryEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.entity_type].append(entry)

    return dict(grouped), len(entries)


async def recall_similar(
    db: AsyncSession,
    project_id: uuid.UUID,
    error_signature: str,
    entity_type: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Semantic similarity recall: find historically similar failures in the same project.

    Returns a list of {memory: AgentMemoryEntry, similarity: float} dicts,
    ranked by descending similarity.
    """
    matches = await _query_similar_vectors(
        signature=error_signature,
        project_id=str(project_id),
        entity_type=entity_type,
        limit=limit,
    )

    if not matches:
        return []

    # Fetch the actual DB entries for matched IDs
    entry_ids = [m["entry_id"] for m in matches]
    similarity_map = {m["entry_id"]: m["similarity"] for m in matches}

    result = await db.execute(
        select(AgentMemoryEntry).where(
            AgentMemoryEntry.id.in_([uuid.UUID(eid) for eid in entry_ids if eid])
        )
    )
    entries = {str(e.id): e for e in result.scalars().all()}

    results = []
    for entry_id, similarity in sorted(similarity_map.items(), key=lambda x: -x[1]):
        entry = entries.get(entry_id)
        if entry:
            results.append({"memory": entry, "similarity": similarity})

    return results[:limit]


# ── Pipeline integration ─────────────────────────────────────────────────────


async def persist_pipeline_memory(
    db: AsyncSession,
    project_id: str,
    run_id: str,
    pipeline_run_id: str,
    final_state: dict,
) -> int:
    """
    Extract memory entries from a completed pipeline state and persist them.

    This is called after a pipeline finishes successfully to build the
    unified memory graph linking runs → clusters → analyses → decisions.
    """
    entries: list[dict] = []
    pid = uuid.UUID(project_id)
    rid = uuid.UUID(run_id)
    plid = uuid.UUID(pipeline_run_id) if pipeline_run_id else None

    base = {
        "project_id": pid,
        "run_id": rid,
        "pipeline_run_id": plid,
    }

    # 1. Failure clusters
    for cluster in final_state.get("failure_clusters", []):
        entries.append({
            **base,
            "entity_type": "cluster",
            "entity_id": cluster.get("cluster_id", str(uuid.uuid4())),
            "error_signature": cluster.get("representative_error", "")[:5000],
            "failure_category": cluster.get("regression_classification"),
            "payload": {
                "label": cluster.get("label"),
                "size": cluster.get("size"),
                "member_test_ids": cluster.get("member_test_ids", []),
                "cohesion_score": cluster.get("cohesion_score"),
            },
            "confidence": cluster.get("confidence"),
        })

    # 2. Per-test analyses
    for test_id, analysis in final_state.get("analyses", {}).items():
        entries.append({
            **base,
            "entity_type": "analysis",
            "entity_id": str(test_id),
            "error_signature": _build_analysis_signature(analysis),
            "failure_category": analysis.get("failure_category"),
            "root_cause_summary": analysis.get("root_cause_summary", "")[:2000],
            "confidence": analysis.get("confidence_score"),
            "payload": {
                "is_flaky": analysis.get("is_flaky"),
                "recommended_actions": analysis.get("recommended_actions", [])[:5],
                "evidence_references": analysis.get("evidence_references", [])[:5],
            },
        })

    # 3. Release decision
    decision = final_state.get("release_decision")
    if decision and isinstance(decision, dict):
        entries.append({
            **base,
            "entity_type": "release_decision",
            "entity_id": str(rid),
            "payload": {
                "recommendation": decision.get("recommendation"),
                "risk_score": decision.get("risk_score"),
                "blocking_issues": decision.get("blocking_issues"),
                "conditions_for_go": decision.get("conditions_for_go"),
            },
            "confidence": decision.get("confidence"),
        })

    # 4. Anomaly summary
    anomaly_summary = final_state.get("anomaly_summary")
    if anomaly_summary:
        entries.append({
            **base,
            "entity_type": "anomaly",
            "entity_id": str(rid),
            "payload": {
                "is_regression": final_state.get("is_regression", False),
                "anomaly_count": len(final_state.get("anomalies", [])),
                "regression_test_count": len(final_state.get("regression_tests", [])),
            },
            "error_signature": str(anomaly_summary)[:2000] if anomaly_summary else None,
        })

    # 5. Executive summary
    summary = final_state.get("executive_summary")
    if summary:
        entries.append({
            **base,
            "entity_type": "summary",
            "entity_id": str(rid),
            "payload": {
                "pass_rate": final_state.get("pass_rate"),
                "total_tests": final_state.get("total_tests"),
                "failed_count": len(final_state.get("failed_test_ids", [])),
                "fallback_used": final_state.get("fallback_used", False),
            },
        })

    if not entries:
        return 0

    return await persist_memory_entries(db, entries)


def _build_analysis_signature(analysis: dict) -> str:
    """Build a searchable error signature from an analysis dict."""
    parts = []
    if analysis.get("failure_category"):
        parts.append(analysis["failure_category"])
    if analysis.get("root_cause_summary"):
        parts.append(analysis["root_cause_summary"][:500])
    if analysis.get("error_message"):
        parts.append(analysis["error_message"][:500])
    return " | ".join(parts) if parts else ""
