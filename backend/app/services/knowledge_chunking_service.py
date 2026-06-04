"""
Requirements-aware chunking + ChromaDB indexing for knowledge sources (RAG-5).

Splits normalized source content into chunks optimized for requirements and
acceptance criteria, embeds them into a separate ChromaDB collection, and
persists chunk metadata in PostgreSQL.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import KnowledgeChunk
from app.services.connectors.base import FetchedContent

logger = structlog.get_logger(__name__)

_COLLECTION_NAME = "knowledge_chunks"

# Regex for requirement identifiers at the start of a line
_REQ_ID_RE = re.compile(
    r"^[\[\(]?(REQ|AC|US|TC|FR|NFR|UC)-?\s*\d+[\]\)]?\s*[-:.]?\s*",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    text: str
    section_heading: Optional[str] = None
    requirement_id: Optional[str] = None
    chunk_index: int = 0
    token_count: int = 0


# ── Token estimation ──────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Approximate token count (1 token ~ 0.75 words)."""
    return max(1, int(len(text.split()) / 0.75))


# ── Requirement ID extraction ─────────────────────────────────────────────────

def _extract_requirement_id(text: str) -> Optional[str]:
    m = _REQ_ID_RE.match(text.strip())
    if m:
        return m.group(0).strip().rstrip("-:.").strip()
    return None


# ── Splitting strategy ────────────────────────────────────────────────────────

def _split_into_chunks(content: FetchedContent) -> list[Chunk]:
    """
    Requirements-aware splitting:
    1. Split on Markdown section headings (## / ###)
    2. Within sections, split on AC/REQ bullets and numbered lists
    3. If a segment > max tokens: paragraph split on blank lines
    4. If still > max: hard split at boundary
    """
    target = settings.KNOWLEDGE_CHUNK_TARGET_TOKENS
    max_tokens = settings.KNOWLEDGE_CHUNK_MAX_TOKENS
    overlap = settings.KNOWLEDGE_CHUNK_OVERLAP_TOKENS

    raw = content.raw_text or ""
    if not raw.strip():
        return []

    # Step 1: Split into sections by headings
    sections = _split_by_headings(raw)

    chunks: list[Chunk] = []
    idx = 0

    for heading, section_text in sections:
        # Step 2: Split section into sub-segments by requirement bullets
        segments = _split_by_requirements(section_text)

        buffer = ""
        for seg_text in segments:
            req_id = _extract_requirement_id(seg_text)
            combined = (buffer + "\n" + seg_text).strip() if buffer else seg_text.strip()
            combined_tokens = _estimate_tokens(combined)

            if combined_tokens <= target:
                buffer = combined
                continue

            # Buffer is full — flush it
            if buffer.strip():
                chunks.append(Chunk(
                    text=buffer.strip(),
                    section_heading=heading,
                    requirement_id=_extract_requirement_id(buffer),
                    chunk_index=idx,
                    token_count=_estimate_tokens(buffer),
                ))
                idx += 1

            # Handle oversized segment
            if _estimate_tokens(seg_text) > max_tokens:
                for sub in _hard_split(seg_text, max_tokens):
                    chunks.append(Chunk(
                        text=sub.strip(),
                        section_heading=heading,
                        requirement_id=req_id,
                        chunk_index=idx,
                        token_count=_estimate_tokens(sub),
                    ))
                    idx += 1
                buffer = ""
            else:
                # Carry overlap from previous chunk
                if chunks and overlap > 0:
                    prev_words = chunks[-1].text.split()
                    overlap_text = " ".join(prev_words[-overlap:]) if len(prev_words) > overlap else ""
                    buffer = (overlap_text + " " + seg_text).strip()
                else:
                    buffer = seg_text.strip()

        # Flush remaining buffer
        if buffer.strip():
            chunks.append(Chunk(
                text=buffer.strip(),
                section_heading=heading,
                requirement_id=_extract_requirement_id(buffer),
                chunk_index=idx,
                token_count=_estimate_tokens(buffer),
            ))
            idx += 1

    return chunks


def _split_by_headings(text: str) -> list[tuple[Optional[str], str]]:
    """Split text by Markdown headings (## or ###). Returns [(heading, body)]."""
    heading_re = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
    parts: list[tuple[Optional[str], str]] = []
    last_pos = 0
    last_heading: Optional[str] = None

    for m in heading_re.finditer(text):
        if m.start() > last_pos:
            body = text[last_pos:m.start()].strip()
            if body:
                parts.append((last_heading, body))
        last_heading = m.group(2).strip()
        last_pos = m.end()

    # Remainder after last heading
    remainder = text[last_pos:].strip()
    if remainder:
        parts.append((last_heading, remainder))

    if not parts:
        parts.append((None, text.strip()))

    return parts


def _split_by_requirements(text: str) -> list[str]:
    """Split section text on requirement bullets, numbered lists, and Given/When/Then."""
    splitter = re.compile(
        r"(?:^|\n)(?="
        r"[\[\(]?(?:REQ|AC|US|TC|FR|NFR|UC)-?\s*\d+"
        r"|\d+[.)]\s"
        r"|- \*\*(?:Given|When|Then|And)\*\*"
        r"|- "
        r")",
        re.IGNORECASE,
    )
    parts = splitter.split(text)
    return [p for p in parts if p.strip()]


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """Split on paragraph boundaries, then on hard token limit."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buf = ""

    for para in paragraphs:
        combined = (buf + "\n\n" + para).strip() if buf else para.strip()
        if _estimate_tokens(combined) <= max_tokens:
            buf = combined
        else:
            if buf.strip():
                chunks.append(buf.strip())
            if _estimate_tokens(para) > max_tokens:
                # Hard word-boundary split
                words = para.split()
                sub = ""
                for w in words:
                    test = (sub + " " + w).strip()
                    if _estimate_tokens(test) > max_tokens:
                        if sub.strip():
                            chunks.append(sub.strip())
                        sub = w
                    else:
                        sub = test
                if sub.strip():
                    buf = sub.strip()
                else:
                    buf = ""
            else:
                buf = para.strip()

    if buf.strip():
        chunks.append(buf.strip())

    return chunks


# ── ChromaDB collection management ────────────────────────────────────────────

def _get_chroma_client():
    import chromadb
    return chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)


async def _get_or_create_knowledge_collection():
    client = await asyncio.to_thread(_get_chroma_client)
    return await asyncio.to_thread(
        client.get_or_create_collection,
        _COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


async def get_collection_status() -> dict:
    try:
        collection = await _get_or_create_knowledge_collection()
        count = await asyncio.to_thread(collection.count)
        return {"status": "healthy", "document_count": count}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc), "document_count": 0}


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def chunk_and_index(
    db: AsyncSession,
    source,
    content: FetchedContent,
    sync_version: int,
) -> int:
    """
    Full pipeline: chunk -> retire old -> upsert ChromaDB -> persist metadata.
    Returns chunk count.
    """
    chunks = _split_into_chunks(content)
    if not chunks:
        return 0

    # Retire existing active chunks for this source
    await retire_chunks_for_source(db, source.id)

    # Upsert into ChromaDB
    try:
        collection = await _get_or_create_knowledge_collection()
        ids = []
        documents = []
        metadatas = []

        for chunk in chunks:
            chunk_id = str(uuid.uuid4())
            ids.append(chunk_id)
            documents.append(chunk.text)
            metadatas.append({
                "source_id": str(source.id),
                "project_id": str(source.project_id),
                "source_type": source.source_type,
                "section_heading": chunk.section_heading or "",
                "requirement_id": chunk.requirement_id or "",
                "chunk_index": chunk.chunk_index,
                "sync_version": sync_version,
                "is_active": 1,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            })

        # Batch upsert
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            batch_docs = documents[i:i + batch_size]
            batch_meta = metadatas[i:i + batch_size]
            await asyncio.to_thread(
                collection.upsert,
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_meta,
            )

        logger.info("chromadb_chunks_indexed", chunk_count=len(chunks), source_id=str(source.id))
    except Exception as exc:
        logger.warning(
            "chromadb_indexing_failed_chunks_saved_to_pg",
            source_id=str(source.id),
            error=str(exc),
        )

    # Persist chunk metadata to PostgreSQL
    for i, chunk in enumerate(chunks):
        chunk_row = KnowledgeChunk(
            id=uuid.UUID(ids[i]),
            source_id=source.id,
            project_id=source.project_id,
            vector_id=ids[i],
            section_heading=chunk.section_heading,
            requirement_id=chunk.requirement_id,
            chunk_index=chunk.chunk_index,
            chunk_text_preview=chunk.text[:500],
            token_count=chunk.token_count,
            sync_version=sync_version,
            is_active=True,
        )
        db.add(chunk_row)

    await db.flush()
    logger.info("chunk_metadata_persisted", chunk_count=len(chunks), source_id=str(source.id))
    return len(chunks)


async def retire_chunks_for_source(db: AsyncSession, source_id: uuid.UUID) -> int:
    """Soft-delete active chunks for a source. Also removes from ChromaDB."""
    result = await db.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.source_id == source_id, KnowledgeChunk.is_active.is_(True))
        .values(is_active=False)
    )
    retired = result.rowcount or 0

    # Remove from ChromaDB
    if retired > 0:
        try:
            collection = await _get_or_create_knowledge_collection()
            await asyncio.to_thread(
                collection.delete,
                where={"source_id": str(source_id)},
            )
        except Exception as exc:
            logger.warning("chromadb_delete_failed", source_id=str(source_id), error=str(exc))

    return retired
