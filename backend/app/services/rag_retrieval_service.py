"""RAG retrieval service — query ChromaDB knowledge_chunks for relevant evidence (RAG-7)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import KnowledgeSource
from app.services.knowledge_chunking_service import _get_or_create_knowledge_collection

logger = structlog.get_logger(__name__)


@dataclass
class RetrievedChunk:
    vector_id: str
    source_id: uuid.UUID
    source_title: str
    section_heading: Optional[str]
    chunk_text: str
    relevance_score: float
    requirement_id: Optional[str]
    chunk_text_preview: Optional[str] = None


async def retrieve_chunks(
    db: AsyncSession,
    project_id: uuid.UUID,
    query_text: str,
    source_ids: Optional[list[uuid.UUID]] = None,
    top_k: int = 10,
    min_score: float = 0.0,
) -> list[RetrievedChunk]:
    """
    Query ChromaDB knowledge_chunks collection and return ranked results
    with citation metadata. Filters by project scope and optionally by source_ids.
    """
    from app.services.feature_flags import is_enabled
    if not await is_enabled("knowledge_rag"):
        return []

    try:
        collection = await _get_or_create_knowledge_collection()
    except Exception as exc:
        logger.warning("ChromaDB unavailable for retrieval: %s", exc)
        return []

    # Build ChromaDB where clause
    where_filters: dict = {
        "$and": [
            {"project_id": {"$eq": str(project_id)}},
            {"is_active": {"$eq": 1}},
        ]
    }

    if source_ids:
        # ChromaDB doesn't support $in directly on string, so we query per-source
        # and merge. For small source_id lists this is efficient enough.
        pass  # handled below

    # Query ChromaDB
    if source_ids and len(source_ids) <= 10:
        # Per-source queries merged for precise filtering
        all_results: list[RetrievedChunk] = []
        for sid in source_ids:
            per_source_where = {
                "$and": [
                    {"project_id": {"$eq": str(project_id)}},
                    {"is_active": {"$eq": 1}},
                    {"source_id": {"$eq": str(sid)}},
                ]
            }
            results = await asyncio.to_thread(
                collection.query,
                query_texts=[query_text],
                n_results=top_k,
                where=per_source_where,
                include=["documents", "distances", "metadatas"],
            )
            all_results.extend(_parse_results(results))
        # Sort by relevance and take top_k
        all_results.sort(key=lambda c: c.relevance_score, reverse=True)
        chunks = all_results[:top_k]
    else:
        results = await asyncio.to_thread(
            collection.query,
            query_texts=[query_text],
            n_results=top_k,
            where=where_filters,
            include=["documents", "distances", "metadatas"],
        )
        chunks = _parse_results(results)

    # Filter by min_score
    if min_score > 0:
        chunks = [c for c in chunks if c.relevance_score >= min_score]

    # Enrich with source titles and classification from PostgreSQL
    source_id_set = {c.source_id for c in chunks}
    if source_id_set:
        result = await db.execute(
            select(KnowledgeSource.id, KnowledgeSource.title, KnowledgeSource.classification).where(
                KnowledgeSource.id.in_(source_id_set),
            )
        )
        source_meta = {row.id: (row.title, row.classification or "internal") for row in result.all()}
        for chunk in chunks:
            title, classification = source_meta.get(chunk.source_id, ("Unknown Source", "internal"))
            chunk.source_title = title

            # RAG-13: Apply redaction to chunk text before it reaches the UI or LLM
            from app.services.rag_redaction_service import redact_chunk_text
            chunk.chunk_text = redact_chunk_text(chunk.chunk_text, classification)
            chunk.chunk_text_preview = chunk.chunk_text[:500] if chunk.chunk_text else None

    return chunks


def _parse_results(results: dict) -> list[RetrievedChunk]:
    """Parse ChromaDB query results into RetrievedChunk list."""
    chunks: list[RetrievedChunk] = []
    if not results or not results.get("ids") or not results["ids"][0]:
        return chunks

    ids = results["ids"][0]
    documents = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    for i, vid in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        doc = documents[i] if i < len(documents) else ""
        # ChromaDB returns cosine distance, convert to similarity score
        distance = distances[i] if i < len(distances) else 1.0
        score = max(0.0, 1.0 - distance)

        try:
            source_id = uuid.UUID(meta.get("source_id", ""))
        except (ValueError, AttributeError):
            continue

        chunks.append(RetrievedChunk(
            vector_id=vid,
            source_id=source_id,
            source_title="",  # enriched later
            section_heading=meta.get("section_heading") or None,
            chunk_text=doc,
            relevance_score=round(score, 4),
            requirement_id=meta.get("requirement_id") or None,
            chunk_text_preview=doc[:500] if doc else None,
        ))

    return chunks
