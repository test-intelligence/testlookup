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
import difflib
import fnmatch
import hashlib
import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentMemoryEntry

logger = logging.getLogger("services.agent_memory")

_MEMORY_COLLECTION = "agent_memory_vectors"
_SIMILARITY_THRESHOLD = 0.70  # lower than analysis cache — recall should be broader
_MAX_MEMORY_DOCUMENTS = 50000
_MEMORY_RETRIEVAL_VERSION = "agent_memory.recall:v1"
_MEMORY_CONSUMER_CONTEXT_VERSION = "agent_memory.consumer_context:v1"
_ACTIVE_MEMORY_STATUS = "active"
_MEMORY_SOURCE_TYPES = frozenset({"pipeline_agent", "human_feedback", "system", "external_artifact"})
_MEMORY_TRUST_LEVELS = frozenset({"authoritative", "derived", "human_verified", "unverified"})
_MEMORY_LIFECYCLE_STATUSES = frozenset({"active", "superseded", "expired", "revoked"})
_DEFECT_MEMORY_ENTITY_TYPES = ("promoted_defect", "defect_candidate")
_OPEN_DEFECT_STATUSES = {
    "",
    "approved",
    "executed",
    "open",
    "pending",
    "pending_review",
    "promoted",
    "suggested",
}
_CLOSED_DEFECT_STATUSES = {
    "closed",
    "dismissed",
    "duplicate",
    "fixed",
    "rejected",
    "resolved",
    "wont_fix",
    "won't_fix",
}


# ── ChromaDB helpers ─────────────────────────────────────────────────────────


async def _get_chroma_client():
    from app.db.chroma import get_configured_chroma_client
    return await get_configured_chroma_client()


async def _get_or_create_collection():
    client = await _get_chroma_client()
    return await asyncio.to_thread(
        client.get_or_create_collection,
        _MEMORY_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


async def _index_memory_vector(entry_id: str, signature: str, metadata: dict) -> None:
    """Index a single memory entry in ChromaDB for vector recall."""
    try:
        collection = await _get_or_create_collection()
        normalized_signature = _normalize_memory_signature(signature)
        doc_id = hashlib.sha256(
            f"{entry_id}:{_hash_text(normalized_signature)}".encode()
        ).hexdigest()[:32]
        await asyncio.to_thread(
            collection.upsert,
            ids=[doc_id],
            documents=[normalized_signature],
            metadatas=[{
                "entry_id": str(entry_id),
                "entity_type": metadata.get("entity_type", ""),
                "project_id": metadata.get("project_id", ""),
                "failure_category": metadata.get("failure_category", ""),
                "signature_sha256": _hash_text(normalized_signature),
                "retrieval_version": _MEMORY_RETRIEVAL_VERSION,
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
        normalized_signature = _normalize_memory_signature(signature)

        where_filter: dict[str, Any] = {"project_id": project_id}
        if entity_type:
            where_filter["entity_type"] = entity_type

        results = await asyncio.to_thread(
            collection.query,
            query_texts=[normalized_signature],
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
                    "distance": round(float(dist), 6),
                })
        matches.sort(key=lambda item: (-item["similarity"], str(item.get("entry_id") or "")))
        return matches[:limit]

    except Exception as exc:
        logger.debug("Memory vector query failed (non-critical): %s", exc)
        return []


# ── Core service functions ───────────────────────────────────────────────────


def _hash_text(value: object) -> str:
    text = "" if value is None else str(value)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_json(value: object) -> str:
    """Hash JSON-like payloads with stable key ordering for replay references."""
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _hash_text(canonical)


def _short_hash(value: object, length: int = 16) -> str:
    return _hash_json(value)[:length]


def _coerce_uuid(value: object) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _normalize_memory_signature(signature: str | None) -> str:
    """Normalize recall/index text so vector lookup is replayable."""
    normalized = re.sub(r"\s+", " ", str(signature or "")).strip().lower()
    return normalized[:5000]


def _active_memory_filters(now: datetime | None = None) -> tuple[Any, Any]:
    current = now or datetime.now(timezone.utc)
    return (
        AgentMemoryEntry.lifecycle_status == _ACTIVE_MEMORY_STATUS,
        or_(
            AgentMemoryEntry.expires_at.is_(None),
            AgentMemoryEntry.expires_at > current,
        ),
    )


def _memory_expiry(entry_data: dict[str, Any], now: datetime) -> datetime:
    supplied = entry_data.get("expires_at")
    if isinstance(supplied, datetime):
        if supplied.tzinfo is None:
            return supplied.replace(tzinfo=timezone.utc)
        return supplied
    from app.core.config import settings

    days = max(1, min(int(getattr(settings, "AGENT_MEMORY_RETENTION_DAYS", 365) or 365), 3650))
    return now + timedelta(days=days)


def _sanitize_memory_text(value: object) -> Any:
    """Redact PII/secrets from free-text memory fields before persistence.

    Returns None/empty unchanged so callers preserve their nullability; any
    non-empty string is routed through the privacy service (the same boundary
    used before DB writes elsewhere in the codebase).
    """
    if not value:
        return value
    from app.services.privacy_service import sanitize_for_persistence  # noqa: PLC0415

    return sanitize_for_persistence(str(value))


def _memory_retrieval_manifest(
    *,
    project_id: uuid.UUID,
    error_signature: str,
    entity_type: Optional[str],
    limit: int,
) -> dict[str, Any]:
    """Build deterministic retrieval metadata for recall audit/replay."""
    normalized_signature = _normalize_memory_signature(error_signature)
    return {
        "schema_version": 1,
        "retrieval_version": _MEMORY_RETRIEVAL_VERSION,
        "retrieval_strategy": "project_scoped_vector_recall",
        "collection": _MEMORY_COLLECTION,
        "similarity_threshold": _SIMILARITY_THRESHOLD,
        "project_id": str(project_id),
        "entity_type": entity_type,
        "limit": limit,
        "query_signature_sha256": _hash_text(error_signature),
        "normalized_query_signature_sha256": _hash_text(normalized_signature),
    }


def _evidence_refs_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    evidence_refs = (
        payload.get("evidence_refs")
        or payload.get("evidence_references")
        or payload.get("evidence")
        or []
    )
    if not isinstance(evidence_refs, list):
        return []
    return [ref for ref in evidence_refs if isinstance(ref, dict)]


def build_memory_reference(
    entry: AgentMemoryEntry,
    *,
    retrieval_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical reference shape for memory/replay consumers."""
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    return {
        "memory_entry_id": str(entry.id),
        "entity_type": entry.entity_type,
        "entity_id": str(entry.entity_id),
        "source_type": getattr(entry, "source_type", "pipeline_agent"),
        "trust_level": getattr(entry, "trust_level", "derived"),
        "lifecycle_status": getattr(entry, "lifecycle_status", _ACTIVE_MEMORY_STATUS),
        "source_snapshot_id": (
            getattr(entry, "source_snapshot_id", None)
            or _coerce_uuid(payload.get("source_snapshot_id"))
        ),
        "source_hash": getattr(entry, "source_hash", None),
        "expires_at": (
            entry.expires_at.isoformat()
            if getattr(entry, "expires_at", None) else None
        ),
        "payload_sha256": _hash_json(payload),
        "retrieval_audit": retrieval_audit,
        "evidence_refs": _evidence_refs_from_payload(payload),
    }


def _memory_consumer_audit(
    *,
    consumer: str,
    project_id: uuid.UUID,
    entity_types: list[str] | tuple[str, ...],
    limit: int,
    query: str | None = None,
) -> dict[str, Any]:
    normalized_query = _normalize_memory_signature(query)
    return {
        "schema_version": 1,
        "consumer_context_version": _MEMORY_CONSUMER_CONTEXT_VERSION,
        "consumer": consumer,
        "retrieval_strategy": "canonical_agent_memory",
        "project_id": str(project_id),
        "entity_types": list(entity_types),
        "limit": limit,
        "query_signature_sha256": _hash_text(query) if query is not None else None,
        "normalized_query_signature_sha256": (
            _hash_text(normalized_query) if query is not None else None
        ),
    }


def _memory_entry_sort_key(entry: AgentMemoryEntry) -> tuple[str, str, str, str]:
    created = getattr(entry, "created_at", None)
    created_key = created.isoformat() if hasattr(created, "isoformat") else str(created or "")
    return (
        str(getattr(entry, "entity_type", "") or ""),
        str(getattr(entry, "entity_id", "") or ""),
        created_key,
        str(getattr(entry, "id", "") or ""),
    )


def _memory_payload(entry: AgentMemoryEntry) -> dict[str, Any]:
    payload = getattr(entry, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _memory_reference_with_audit(
    entry: AgentMemoryEntry,
    retrieval_audit: dict[str, Any],
) -> dict[str, Any]:
    return build_memory_reference(entry, retrieval_audit=retrieval_audit)


async def load_canonical_memory_entries(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    entity_types: list[str] | tuple[str, ...],
    limit: int = 100,
    entity_id: str | None = None,
) -> list[AgentMemoryEntry]:
    """Load memory rows for downstream consumers with stable ordering."""
    limit = max(1, min(int(limit or 100), 500))
    result = await db.execute(
        select(AgentMemoryEntry)
        .where(
            AgentMemoryEntry.project_id == project_id,
            AgentMemoryEntry.entity_type.in_(list(entity_types)),
            *_active_memory_filters(),
            *(
                [AgentMemoryEntry.entity_id == entity_id]
                if entity_id is not None else []
            ),
        )
        .order_by(
            AgentMemoryEntry.entity_type.asc(),
            AgentMemoryEntry.entity_id.asc(),
            AgentMemoryEntry.created_at.desc(),
            AgentMemoryEntry.id.asc(),
        )
        .limit(limit)
    )
    entries = [
        entry for entry in result.scalars().all()
        if str(getattr(entry, "entity_type", "") or "") in set(entity_types)
    ]
    return sorted(entries, key=_memory_entry_sort_key)


def _defect_memory_identity(entry: AgentMemoryEntry) -> str:
    payload = _memory_payload(entry)
    return str(
        payload.get("promoted_defect_id")
        or payload.get("defect_id")
        or entry.entity_id
        or entry.id
    )


def _defect_memory_is_open(entry: AgentMemoryEntry) -> bool:
    payload = _memory_payload(entry)
    status = str(
        payload.get("status")
        or payload.get("resolution_status")
        or getattr(entry, "resolution", None)
        or ""
    ).lower()
    if status in _CLOSED_DEFECT_STATUSES:
        return False
    return status in _OPEN_DEFECT_STATUSES or not status


def _defect_memory_text(entry: AgentMemoryEntry) -> str:
    payload = _memory_payload(entry)
    parts = [
        payload.get("title"),
        payload.get("duplicate_hint"),
        payload.get("component"),
        getattr(entry, "root_cause_summary", None),
        getattr(entry, "error_signature", None),
    ]
    return " | ".join(str(part) for part in parts if part)[:2000]


def _defect_memory_text_candidates(entry: AgentMemoryEntry) -> list[str]:
    payload = _memory_payload(entry)
    parts = [
        payload.get("title"),
        payload.get("duplicate_hint"),
        getattr(entry, "root_cause_summary", None),
        getattr(entry, "error_signature", None),
        _defect_memory_text(entry),
    ]
    return [
        _normalize_memory_signature(part)
        for part in parts
        if _normalize_memory_signature(part)
    ]


async def load_release_risk_memory_context(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Return release-risk inputs from the canonical memory layer."""
    entries = await load_canonical_memory_entries(
        db,
        project_id,
        entity_types=_DEFECT_MEMORY_ENTITY_TYPES,
        limit=limit,
    )
    audit = _memory_consumer_audit(
        consumer="release_risk",
        project_id=project_id,
        entity_types=_DEFECT_MEMORY_ENTITY_TYPES,
        limit=limit,
    )
    latest_by_identity: dict[str, AgentMemoryEntry] = {}
    for entry in sorted(entries, key=_memory_entry_sort_key):
        latest_by_identity[_defect_memory_identity(entry)] = entry
    active = {
        identity: entry
        for identity, entry in latest_by_identity.items()
        if _defect_memory_is_open(entry)
    }

    references = [
        _memory_reference_with_audit(entry, {**audit, "rank": rank})
        for rank, entry in enumerate(
            sorted(active.values(), key=_memory_entry_sort_key),
            start=1,
        )
    ]
    return {
        "schema_version": 1,
        "memory_layer_version": _MEMORY_CONSUMER_CONTEXT_VERSION,
        "source": "agent_memory",
        "open_defects": len(active),
        "memory_entry_count": len(entries),
        "memory_references": references,
        "retrieval_audit": audit,
    }


async def find_duplicate_defect_memory(
    db: AsyncSession,
    project_id: uuid.UUID,
    duplicate_hint: str,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Find duplicate defects through canonical memory, with deterministic fallback."""
    limit = max(1, min(int(limit or 10), 50))
    audit = _memory_consumer_audit(
        consumer="defect_promotion_duplicate_check",
        project_id=project_id,
        entity_types=_DEFECT_MEMORY_ENTITY_TYPES,
        limit=limit,
        query=duplicate_hint,
    )
    candidates: dict[str, dict[str, Any]] = {}
    for entity_type in _DEFECT_MEMORY_ENTITY_TYPES:
        for match in await recall_similar(
            db,
            project_id,
            duplicate_hint,
            entity_type=entity_type,
            limit=limit,
        ):
            entry = match.get("memory")
            if not entry or not _defect_memory_is_open(entry):
                continue
            key = str(getattr(entry, "id", ""))
            current = candidates.get(key)
            if current is None or match.get("similarity", 0) > current.get("similarity", 0):
                candidates[key] = match

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            -float(item.get("similarity") or 0),
            str(getattr(item.get("memory"), "id", "")),
        ),
    )
    if ranked:
        best = ranked[0]
        entry = best["memory"]
        retrieval_audit = {
            **audit,
            "rank": 1,
            "similarity": best.get("similarity"),
            "source_retrieval_audit": best.get("retrieval_audit"),
        }
        return {
            "found": True,
            "duplicate_defect_id": _defect_memory_identity(entry),
            "memory": entry,
            "similarity": best.get("similarity"),
            "memory_reference": _memory_reference_with_audit(entry, retrieval_audit),
            "retrieval_audit": retrieval_audit,
        }

    entries = await load_canonical_memory_entries(
        db,
        project_id,
        entity_types=_DEFECT_MEMORY_ENTITY_TYPES,
        limit=max(limit * 5, 25),
    )
    hint_lower = _normalize_memory_signature(duplicate_hint)
    fallback_matches: list[tuple[float, AgentMemoryEntry]] = []
    for entry in entries:
        if not _defect_memory_is_open(entry):
            continue
        ratio = max(
            (
                difflib.SequenceMatcher(
                    None,
                    hint_lower[:200],
                    candidate[:200],
                ).ratio()
                for candidate in _defect_memory_text_candidates(entry)
            ),
            default=0.0,
        )
        if ratio > 0.7:
            fallback_matches.append((ratio, entry))
    fallback_matches.sort(key=lambda item: (-item[0], str(item[1].id)))
    if fallback_matches:
        ratio, entry = fallback_matches[0]
        retrieval_audit = {
            **audit,
            "rank": 1,
            "similarity": round(ratio, 4),
            "retrieval_strategy": "canonical_agent_memory_title_similarity",
        }
        return {
            "found": True,
            "duplicate_defect_id": _defect_memory_identity(entry),
            "memory": entry,
            "similarity": round(ratio, 4),
            "memory_reference": _memory_reference_with_audit(entry, retrieval_audit),
            "retrieval_audit": retrieval_audit,
        }

    return {
        "found": False,
        "duplicate_defect_id": None,
        "memory": None,
        "similarity": None,
        "memory_reference": None,
        "retrieval_audit": {**audit, "memory_entry_count": len(entries)},
    }


def _ownership_payload(entry: AgentMemoryEntry) -> dict[str, Any]:
    payload = _memory_payload(entry)
    ownership = payload.get("ownership")
    return ownership if isinstance(ownership, dict) else payload


def _ownership_match_score(
    entry: AgentMemoryEntry,
    *,
    cluster_id: str | None,
    component: str | None,
    member_test_ids: set[str],
) -> tuple[int, int, str]:
    payload = _memory_payload(entry)
    ownership = _ownership_payload(entry)
    score = 0
    if cluster_id and str(entry.entity_id) == str(cluster_id):
        score += 100
    if cluster_id and str(payload.get("cluster_id") or "") == str(cluster_id):
        score += 100
    component_lower = str(component or "").lower()
    service = str(ownership.get("service_name") or "").lower()
    if component_lower and service and fnmatch.fnmatch(component_lower, service):
        score += 50
    entry_members = {
        str(member_id)
        for member_id in payload.get("member_test_ids", [])
        if member_id is not None
    }
    overlap = len(member_test_ids & entry_members)
    score += min(overlap, 10) * 5
    confidence = _ownership_confidence_score(ownership.get("confidence")) or 0
    return (score, confidence, str(entry.id))


async def resolve_ownership_from_memory(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    cluster_id: str | None = None,
    component: str | None = None,
    member_test_ids: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any] | None:
    """Resolve ownership from canonical memory when prior context exists."""
    entries = await load_canonical_memory_entries(
        db,
        project_id,
        entity_types=("ownership",),
        limit=limit,
    )
    member_set = {str(item) for item in (member_test_ids or []) if item is not None}
    scored = [
        (_ownership_match_score(
            entry,
            cluster_id=cluster_id,
            component=component,
            member_test_ids=member_set,
        ), entry)
        for entry in entries
    ]
    scored = [(score, entry) for score, entry in scored if score[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0][0], -item[0][1], item[0][2]))
    best_score, best_entry = scored[0]
    audit = _memory_consumer_audit(
        consumer="ownership_routing",
        project_id=project_id,
        entity_types=("ownership",),
        limit=limit,
        query=cluster_id or component or " ".join(sorted(member_set)),
    )
    ownership = dict(_ownership_payload(best_entry))
    ownership["match_source"] = (
        ownership.get("match_source")
        or "agent_memory"
    )
    ownership["memory_match_score"] = best_score[0]
    return {
        "ownership": ownership,
        "memory": best_entry,
        "memory_reference": _memory_reference_with_audit(
            best_entry,
            {**audit, "rank": 1, "match_score": best_score[0]},
        ),
        "retrieval_audit": {**audit, "rank": 1, "match_score": best_score[0]},
    }


def _bounded_list(value: Any, limit: int = 10) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _entry_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{_short_hash(parts)}"


def _evidence_signature(ref: dict[str, Any]) -> str:
    source = str(ref.get("source") or ref.get("source_system") or ref.get("type") or "")
    excerpt = str(
        ref.get("excerpt")
        or ref.get("summary_excerpt")
        or ref.get("message")
        or ref.get("uri_or_ref")
        or ""
    )
    return f"{source} | {excerpt}"[:2000]


def _append_evidence_memory(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    *,
    entity_seed: object,
    evidence: dict[str, Any],
    cluster_id: str | None = None,
    test_id: str | None = None,
) -> None:
    signature = _evidence_signature(evidence)
    if not signature.strip(" |"):
        return
    source = str(evidence.get("source") or evidence.get("source_system") or "unknown")
    entries.append({
        **base,
        "entity_type": "evidence",
        "entity_id": str(evidence.get("id") or _entry_id("ev", entity_seed, evidence)),
        "error_signature": signature,
        "payload": {
            "source": source,
            "cluster_id": cluster_id,
            "test_id": test_id,
            "evidence_refs": [evidence],
            "payload_sha256": _hash_json(evidence),
        },
        "confidence": evidence.get("confidence") or evidence.get("confidence_score"),
    })


def _append_analysis_evidence_memories(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    analyses: dict[str, Any],
) -> None:
    for test_id, analysis in sorted(analyses.items(), key=lambda item: str(item[0])):
        if not isinstance(analysis, dict):
            continue
        for index, evidence in enumerate(_bounded_list(analysis.get("evidence_references"), 10)):
            if isinstance(evidence, dict):
                _append_evidence_memory(
                    entries,
                    base,
                    entity_seed=("analysis", test_id, index),
                    evidence=evidence,
                    test_id=str(test_id),
                )


def _append_summary_evidence_memories(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    final_state: dict[str, Any],
) -> None:
    structured = _safe_dict(final_state.get("structured_summary"))
    layer3 = _safe_dict(structured.get("layer3_evidence_pack"))
    for key in ("citations", "evidence_highlights", "evidence_snippets"):
        for index, evidence in enumerate(_bounded_list(layer3.get(key), 10)):
            if isinstance(evidence, dict):
                _append_evidence_memory(
                    entries,
                    base,
                    entity_seed=("summary", key, index),
                    evidence=evidence,
                )


def _append_deep_finding_evidence_memories(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    final_state: dict[str, Any],
) -> None:
    findings = _safe_dict(final_state.get("deep_findings"))
    for cluster_id, finding in sorted(findings.items(), key=lambda item: str(item[0])):
        finding_payload = _safe_dict(finding)
        for index, evidence in enumerate(_bounded_list(finding_payload.get("evidence"), 10)):
            if isinstance(evidence, dict):
                _append_evidence_memory(
                    entries,
                    base,
                    entity_seed=("deep_finding", cluster_id, index),
                    evidence=evidence,
                    cluster_id=str(cluster_id),
                )


def _append_defect_memories(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    final_state: dict[str, Any],
) -> None:
    candidates = (
        _bounded_list(final_state.get("defect_candidates"), 20)
        + _bounded_list(final_state.get("promoted_defects"), 20)
        + _bounded_list(final_state.get("defects"), 20)
    )
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        promoted_id = candidate.get("promoted_defect_id") or candidate.get("defect_id")
        entity_type = "promoted_defect" if promoted_id else "defect_candidate"
        cluster_id = str(candidate.get("cluster_id") or candidate.get("id") or index)
        title = str(candidate.get("title") or candidate.get("label") or cluster_id)
        evidence_bundle = _safe_dict(candidate.get("evidence_bundle"))
        entries.append({
            **base,
            "entity_type": entity_type,
            "entity_id": str(promoted_id or candidate.get("id") or _entry_id("defect", cluster_id, title)),
            "error_signature": str(candidate.get("duplicate_hint") or title)[:2000],
            "failure_category": candidate.get("failure_category"),
            "root_cause_summary": candidate.get("description") or title,
            "payload": {
                "cluster_id": cluster_id,
                "title": title,
                "severity": candidate.get("severity") or candidate.get("severity_hint"),
                "component": candidate.get("component"),
                "owner_team": candidate.get("owner_team"),
                "status": candidate.get("status"),
                "duplicate_detected": candidate.get("duplicate_detected"),
                "duplicate_defect_id": candidate.get("duplicate_defect_id"),
                "promoted_defect_id": promoted_id,
                "evidence_bundle": evidence_bundle,
                "evidence_refs": _evidence_refs_from_payload(evidence_bundle),
                "payload_sha256": _hash_json(candidate),
            },
            "confidence": candidate.get("confidence"),
            "resolution": candidate.get("status"),
        })


def _release_input_snapshot(final_state: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    snapshot = (
        decision.get("input_snapshot")
        or final_state.get("release_decision_input_snapshot")
        or final_state.get("release_input_snapshot")
    )
    return _safe_dict(snapshot)


def _append_release_input_snapshot_memory(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    run_id: uuid.UUID,
    final_state: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    snapshot = _release_input_snapshot(final_state, decision)
    if not snapshot:
        return
    entries.append({
        **base,
        "entity_type": "release_input_snapshot",
        "entity_id": str(run_id),
        "payload": {
            "input_snapshot": snapshot,
            "input_snapshot_sha256": _hash_json(snapshot),
            "score_model_version": snapshot.get("score_model_version"),
        },
        "confidence": decision.get("confidence"),
    })


def _append_declared_ownership_memories(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    final_state: dict[str, Any],
) -> set[str]:
    emitted_cluster_ids: set[str] = set()
    declared = final_state.get("ownership_resolutions") or {}
    if isinstance(declared, dict):
        iterable = declared.items()
    elif isinstance(declared, list):
        iterable = [
            (item.get("cluster_id") or item.get("entity_id") or index, item)
            for index, item in enumerate(declared)
            if isinstance(item, dict)
        ]
    else:
        iterable = []

    for cluster_id, ownership in iterable:
        if not isinstance(ownership, dict):
            continue
        cluster_id_str = str(cluster_id)
        emitted_cluster_ids.add(cluster_id_str)
        entries.append({
            **base,
            "entity_type": "ownership",
            "entity_id": cluster_id_str,
            "payload": {
                "cluster_id": cluster_id_str,
                "ownership": ownership,
                "payload_sha256": _hash_json(ownership),
            },
            "confidence": _ownership_confidence_score(ownership.get("confidence")),
        })
    return emitted_cluster_ids


def _append_cluster_embedded_ownership_memories(
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    clusters: list[dict[str, Any]],
    emitted_cluster_ids: set[str],
) -> None:
    for cluster in clusters:
        cluster_id = str(cluster.get("cluster_id") or "")
        if not cluster_id or cluster_id in emitted_cluster_ids:
            continue
        ownership = (
            cluster.get("ownership")
            or cluster.get("ownership_resolution")
            or {}
        )
        if not isinstance(ownership, dict):
            ownership = {}
        owner_team = cluster.get("owner_team") or ownership.get("team_name")
        if not ownership and not owner_team:
            continue
        ownership_payload = {
            **ownership,
            "team_name": ownership.get("team_name") or owner_team,
            "service_name": ownership.get("service_name") or cluster.get("component"),
            "confidence": ownership.get("confidence") or "low",
        }
        emitted_cluster_ids.add(cluster_id)
        entries.append({
            **base,
            "entity_type": "ownership",
            "entity_id": cluster_id,
            "payload": {
                "cluster_id": cluster_id,
                "ownership": ownership_payload,
                "member_test_ids": _bounded_list(cluster.get("member_test_ids"), 50),
                "payload_sha256": _hash_json(ownership_payload),
            },
            "confidence": _ownership_confidence_score(ownership_payload.get("confidence")),
        })


def _ownership_confidence_score(confidence: object) -> int | None:
    return {
        "high": 90,
        "medium": 65,
        "low": 35,
        "none": 0,
    }.get(str(confidence or "").lower())


async def _append_resolved_ownership_memories(
    db: AsyncSession,
    entries: list[dict[str, Any]],
    base: dict[str, Any],
    project_id: uuid.UUID,
    clusters: list[dict[str, Any]],
    emitted_cluster_ids: set[str],
) -> None:
    try:
        from app.services.ownership_resolver_service import resolve_cluster_ownership  # noqa: PLC0415
    except Exception:
        return

    for cluster in clusters[:20]:
        cluster_id = str(cluster.get("cluster_id") or "")
        if not cluster_id or cluster_id in emitted_cluster_ids:
            continue
        member_ids = [str(item) for item in _bounded_list(cluster.get("member_test_ids"), 50)]
        valid_member_ids = [item for item in member_ids if _coerce_uuid(item)]
        if not valid_member_ids:
            continue
        try:
            ownership = await resolve_cluster_ownership(db, project_id, valid_member_ids)
            ownership_payload = ownership.to_dict()
        except Exception as exc:
            logger.debug("Ownership memory resolution skipped for %s: %s", cluster_id, exc)
            continue
        emitted_cluster_ids.add(cluster_id)
        entries.append({
            **base,
            "entity_type": "ownership",
            "entity_id": cluster_id,
            "payload": {
                "cluster_id": cluster_id,
                "ownership": ownership_payload,
                "member_test_ids": valid_member_ids,
                "payload_sha256": _hash_json(ownership_payload),
            },
            "confidence": _ownership_confidence_score(ownership_payload.get("confidence")),
        })


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
    index_tasks: list[Any] = []
    now = datetime.now(timezone.utc)
    for entry_data in entries:
        entry_id = entry_data.get("id") or uuid.uuid4()
        source_type = str(entry_data.get("source_type") or "pipeline_agent")
        trust_level = str(entry_data.get("trust_level") or "derived")
        if source_type not in _MEMORY_SOURCE_TYPES or trust_level not in _MEMORY_TRUST_LEVELS:
            raise ValueError("memory_provenance_invalid")
        # Redact PII/secrets before the text crosses the persistence boundary
        # (Postgres row + ChromaDB document below). Memory text is re-derived
        # from pipeline state, which may carry raw failure output.
        signature = _sanitize_memory_text(entry_data.get("error_signature"))
        root_cause = _sanitize_memory_text(entry_data.get("root_cause_summary"))
        payload = entry_data.get("payload")
        if isinstance(payload, dict):
            from app.services.privacy_service import sanitize_dict_for_persistence

            payload = sanitize_dict_for_persistence(payload)
        entry = AgentMemoryEntry(
            id=entry_id,
            project_id=entry_data["project_id"],
            run_id=entry_data["run_id"],
            pipeline_run_id=entry_data.get("pipeline_run_id"),
            entity_type=entry_data["entity_type"],
            entity_id=str(entry_data["entity_id"]),
            error_signature=signature,
            failure_category=entry_data.get("failure_category"),
            root_cause_summary=root_cause,
            payload=payload,
            confidence=entry_data.get("confidence"),
            resolution=entry_data.get("resolution"),
            source_type=source_type,
            trust_level=trust_level,
            lifecycle_status=_ACTIVE_MEMORY_STATUS,
            source_snapshot_id=(
                str(entry_data.get("source_snapshot_id"))
                if entry_data.get("source_snapshot_id") is not None else None
            ),
            source_hash=str(entry_data.get("source_hash")) if entry_data.get("source_hash") else None,
            expires_at=_memory_expiry(entry_data, now),
        )
        db.add(entry)
        count += 1

        # Index in ChromaDB if there's a searchable signature. Collect the
        # coroutines and await them below — a detached asyncio.create_task is
        # not guaranteed to run before the caller's event loop tears down (the
        # pipeline persists memory inside a short-lived AsyncSessionLocal block),
        # which would silently leave rows unindexed and unrecallable.
        if signature:
            index_tasks.append(_index_memory_vector(
                str(entry_id),
                signature,
                {
                    "entity_type": entry_data["entity_type"],
                    "project_id": str(entry_data["project_id"]),
                    "failure_category": entry_data.get("failure_category", ""),
                },
            ))

    await db.commit()
    if index_tasks:
        # _index_memory_vector swallows its own exceptions; gather defensively.
        await asyncio.gather(*index_tasks, return_exceptions=True)
    logger.info("Persisted %d memory entries", count)
    return count


async def supersede_memory_entry(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    memory_entry_id: uuid.UUID,
    replacement_entry_id: uuid.UUID,
) -> bool:
    """Atomically supersede one memory row with a same-tenant replacement."""
    rows = await db.execute(
        select(AgentMemoryEntry).where(
            AgentMemoryEntry.project_id == project_id,
            AgentMemoryEntry.id.in_([memory_entry_id, replacement_entry_id]),
        )
    )
    entries = {entry.id: entry for entry in rows.scalars().all()}
    current = entries.get(memory_entry_id)
    replacement = entries.get(replacement_entry_id)
    if not current or not replacement or current.id == replacement.id:
        return False
    if current.lifecycle_status != _ACTIVE_MEMORY_STATUS:
        return False
    if replacement.lifecycle_status != _ACTIVE_MEMORY_STATUS:
        return False
    current.lifecycle_status = "superseded"
    current.superseded_by_id = replacement.id
    current.superseded_at = datetime.now(timezone.utc)
    return True


async def expire_memory_entries(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Mark expired active memories terminal without deleting audit history."""
    current = now or datetime.now(timezone.utc)
    predicates = [
        AgentMemoryEntry.lifecycle_status == _ACTIVE_MEMORY_STATUS,
        AgentMemoryEntry.expires_at.is_not(None),
        AgentMemoryEntry.expires_at <= current,
    ]
    if project_id is not None:
        predicates.append(AgentMemoryEntry.project_id == project_id)
    result = await db.execute(
        update(AgentMemoryEntry)
        .where(*predicates)
        .values(lifecycle_status="expired")
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def purge_memory_vectors(
    project_id: uuid.UUID,
    entry_ids: list[uuid.UUID] | tuple[uuid.UUID, ...],
) -> int:
    """Delete Chroma vectors for lifecycle-expired rows, best effort and bounded."""
    wanted = {str(item) for item in entry_ids[:5000]}
    if not wanted:
        return 0
    try:
        collection = await _get_or_create_collection()
        result = await asyncio.to_thread(
            collection.get,
            where={"project_id": str(project_id)},
            include=["metadatas"],
        )
        ids = result.get("ids", []) if isinstance(result, dict) else []
        metadatas = result.get("metadatas", []) if isinstance(result, dict) else []
        vector_ids = [
            vector_id
            for vector_id, metadata in zip(ids, metadatas)
            if isinstance(metadata, dict) and str(metadata.get("entry_id")) in wanted
        ]
        if vector_ids:
            await asyncio.to_thread(collection.delete, ids=vector_ids)
        return len(vector_ids)
    except Exception as exc:
        logger.warning("memory_vector_purge_failed", extra={"error_type": type(exc).__name__})
        return 0


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
    # Clamp pagination defensively to match the other query helpers (the router
    # already bounds these, but an internal caller might not).
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 50), 200))
    base = select(AgentMemoryEntry).where(
        AgentMemoryEntry.project_id == project_id,
        *_active_memory_filters(),
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
        .where(AgentMemoryEntry.run_id == run_id, *_active_memory_filters())
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
    ranked by descending similarity with deterministic tie-breaks and retrieval audit.
    """
    limit = max(1, min(int(limit or 10), 50))
    normalized_signature = _normalize_memory_signature(error_signature)
    retrieval_manifest = _memory_retrieval_manifest(
        project_id=project_id,
        error_signature=error_signature,
        entity_type=entity_type,
        limit=limit,
    )
    matches = await _query_similar_vectors(
        signature=normalized_signature,
        project_id=str(project_id),
        entity_type=entity_type,
        limit=limit,
    )

    if not matches:
        return []

    # Fetch the actual DB entries for matched IDs
    ranked_matches = sorted(
        (m for m in matches if m.get("entry_id")),
        key=lambda item: (-float(item.get("similarity") or 0), str(item.get("entry_id"))),
    )
    entry_ids = [m["entry_id"] for m in ranked_matches]

    # Chroma metadata ids are external input — skip any that don't parse as a
    # UUID rather than letting uuid.UUID() raise and abort the whole recall.
    valid_ids = [parsed for eid in entry_ids if (parsed := _coerce_uuid(eid))]
    if not valid_ids:
        return []
    result = await db.execute(
        select(AgentMemoryEntry).where(
            AgentMemoryEntry.id.in_([uuid.UUID(eid) for eid in valid_ids]),
            AgentMemoryEntry.project_id == project_id,
            *_active_memory_filters(),
        )
    )
    entries = {str(e.id): e for e in result.scalars().all()}

    results = []
    for rank, match in enumerate(ranked_matches, start=1):
        entry_id = str(match["entry_id"])
        entry = entries.get(entry_id)
        if entry:
            retrieval_audit = {
                **retrieval_manifest,
                "rank": rank,
                "entry_id": entry_id,
                "distance": match.get("distance"),
                "memory_created_at": (
                    entry.created_at.isoformat()
                    if getattr(entry, "created_at", None) else None
                ),
            }
            results.append({
                "memory": entry,
                "similarity": match["similarity"],
                "retrieval_audit": retrieval_audit,
                "memory_reference": build_memory_reference(
                    entry,
                    retrieval_audit=retrieval_audit,
                ),
            })

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
    try:
        pid = uuid.UUID(project_id)
        rid = uuid.UUID(run_id)
        plid = uuid.UUID(pipeline_run_id) if pipeline_run_id else None
    except (TypeError, ValueError) as exc:
        # Make a malformed id an explicit, logged no-op rather than a generic
        # exception the caller swallows as "memory_persistence_failed".
        logger.warning(
            "persist_pipeline_memory skipped: invalid id (project_id=%s run_id=%s): %s",
            project_id, run_id, exc,
        )
        return 0

    base = {
        "project_id": pid,
        "run_id": rid,
        "pipeline_run_id": plid,
    }
    clusters = [
        cluster for cluster in final_state.get("failure_clusters", [])
        if isinstance(cluster, dict)
    ]
    analyses = {
        str(test_id): analysis
        for test_id, analysis in (final_state.get("analyses") or {}).items()
        if isinstance(analysis, dict)
    }

    # 1. Failure clusters
    for cluster in clusters:
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
    for test_id, analysis in analyses.items():
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
        input_snapshot = _release_input_snapshot(final_state, decision)
        entries.append({
            **base,
            "entity_type": "release_decision",
            "entity_id": str(rid),
            "payload": {
                "recommendation": decision.get("recommendation"),
                "risk_score": decision.get("risk_score"),
                "blocking_issues": decision.get("blocking_issues"),
                "conditions_for_go": decision.get("conditions_for_go"),
                "score_model_version": decision.get("score_model_version"),
                "policy_id": decision.get("policy_id"),
                "policy_evaluation": decision.get("policy_evaluation"),
                "input_snapshot_sha256": _hash_json(input_snapshot) if input_snapshot else None,
            },
            "confidence": decision.get("confidence"),
        })
        _append_release_input_snapshot_memory(entries, base, rid, final_state, decision)

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

    # 6. Evidence artifacts from analysis, summary, and deep findings.
    _append_analysis_evidence_memories(entries, base, analyses)
    _append_summary_evidence_memories(entries, base, final_state)
    _append_deep_finding_evidence_memories(entries, base, final_state)

    # 7. Defect candidate and promoted defect memories, if the pipeline state
    # carries them from intelligence/defect promotion steps.
    _append_defect_memories(entries, base, final_state)

    # 8. Ownership memories. Prefer explicit state, then cluster-embedded
    # ownership, then best-effort resolver for clusters with UUID members.
    emitted_ownership = _append_declared_ownership_memories(entries, base, final_state)
    _append_cluster_embedded_ownership_memories(entries, base, clusters, emitted_ownership)
    await _append_resolved_ownership_memories(
        db,
        entries,
        base,
        pid,
        clusters,
        emitted_ownership,
    )

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
