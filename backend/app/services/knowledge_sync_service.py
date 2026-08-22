"""
Knowledge source sync orchestration, MinIO storage, freshness, and observability (RAG-4/6).

Orchestrates the full sync lifecycle:
1. Fetch content via connector
2. Store raw content in MinIO
3. Delegate to chunking service for indexing
4. Record sync events for audit trail
5. Compute freshness state
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import (
    KnowledgeChunk,
    KnowledgeSource,
    KnowledgeSourceType,
    KnowledgeSyncEvent,
    KnowledgeSyncStatus,
)
import structlog

from app.services.connectors.base import ConnectorFetchError, FetchedContent
from app.services.connectors.registry import get_connector

logger = structlog.get_logger(__name__)


# ── Stale thresholds ──────────────────────────────────────────────────────────

_STALE_THRESHOLDS_HOURS: dict[str, int] = {
    KnowledgeSourceType.JIRA_ISSUE.value: 24,
    KnowledgeSourceType.JIRA_EPIC.value: 24,
    KnowledgeSourceType.CONFLUENCE_PAGE.value: 24,
    KnowledgeSourceType.INTERNAL_URL.value: 168,
    KnowledgeSourceType.EXTERNAL_URL.value: 168,
    KnowledgeSourceType.UPLOADED_DOC.value: -1,  # never stale
}


# Source types whose sync fetches from an EXTERNAL / hosted system over the
# network. These must not egress when ``AI_OFFLINE_MODE`` is on. ``INTERNAL_URL``
# (same-network by definition) and ``UPLOADED_DOC`` (read from local/MinIO) are
# deliberately excluded — they don't reach hosted services.
_EXTERNAL_FETCH_TYPES: frozenset[str] = frozenset({
    KnowledgeSourceType.JIRA_ISSUE.value,
    KnowledgeSourceType.JIRA_EPIC.value,
    KnowledgeSourceType.CONFLUENCE_PAGE.value,
    KnowledgeSourceType.EXTERNAL_URL.value,
})


def _effective_threshold(source_type: str) -> int:
    default = _STALE_THRESHOLDS_HOURS.get(source_type, 24)
    if default == -1:
        return -1
    if source_type in (KnowledgeSourceType.JIRA_ISSUE.value, KnowledgeSourceType.JIRA_EPIC.value,
                       KnowledgeSourceType.CONFLUENCE_PAGE.value):
        return settings.KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS
    if source_type in (KnowledgeSourceType.INTERNAL_URL.value, KnowledgeSourceType.EXTERNAL_URL.value):
        return settings.KNOWLEDGE_STALE_THRESHOLD_URL_HOURS
    return default


# ── Sync orchestration ────────────────────────────────────────────────────────


async def run_sync(
    db: AsyncSession,
    source: KnowledgeSource,
    trigger: str = "manual",
) -> dict:
    """
    Full sync pipeline for one source. Never raises — errors are recorded
    as sync events and returned in the result dict.
    """
    t_start = time.monotonic()
    previous_hash = source.content_hash

    # Offline-mode hard gate (above the KNOWLEDGE_RAG_ENABLED flag): a source
    # that fetches from a hosted/external system must not egress when
    # AI_OFFLINE_MODE is on. Short-circuit BEFORE mutating state or hitting the
    # network — and without writing an event, so a re-sync beat tick doesn't
    # spam skipped events every cycle in an air-gapped deployment.
    if settings.AI_OFFLINE_MODE and source.source_type in _EXTERNAL_FETCH_TYPES:
        logger.info(
            "knowledge sync skipped — offline mode",
            source_id=str(source.id),
            source_type=source.source_type,
            trigger=trigger,
        )
        return {
            "status": "skipped",
            "reason": "offline_mode",
            "content_changed": False,
            "chunk_count": 0,
            "duration_ms": int((time.monotonic() - t_start) * 1000),
        }

    try:
        # Mark as syncing
        source.sync_status = KnowledgeSyncStatus.SYNCING.value
        source.sync_error = None
        await db.commit()

        # Fetch content via connector
        connector = get_connector(source.source_type)
        content = await connector.fetch_content(source.canonical_url, source.external_id)

        # Check for unchanged content (skip if not initial sync)
        if content.content_hash == previous_hash and trigger != "on_create":
            duration_ms = int((time.monotonic() - t_start) * 1000)
            source.sync_status = KnowledgeSyncStatus.SYNCED.value
            source.last_synced_at = datetime.now(timezone.utc)

            event = KnowledgeSyncEvent(
                source_id=source.id,
                project_id=source.project_id,
                trigger=trigger,
                status="skipped",
                content_hash=content.content_hash,
                previous_hash=previous_hash,
                content_changed=False,
                duration_ms=duration_ms,
            )
            db.add(event)
            await db.commit()

            return {
                "status": "skipped",
                "content_changed": False,
                "chunk_count": 0,
                "duration_ms": duration_ms,
            }

        # Store raw content in MinIO
        storage_path = await store_raw_content(
            str(source.id), str(source.project_id), content,
        )

        # Compute sync version
        max_ver_result = await db.execute(
            select(func.max(KnowledgeChunk.sync_version)).where(
                KnowledgeChunk.source_id == source.id,
            )
        )
        max_ver = max_ver_result.scalar() or 0
        sync_version = max_ver + 1

        # Chunk and index
        from app.services.knowledge_chunking_service import chunk_and_index
        chunk_count = await chunk_and_index(db, source, content, sync_version)

        duration_ms = int((time.monotonic() - t_start) * 1000)
        content_changed = content.content_hash != previous_hash

        # Update source
        source.content_hash = content.content_hash
        source.sync_status = KnowledgeSyncStatus.SYNCED.value
        source.last_synced_at = datetime.now(timezone.utc)
        source.storage_path = storage_path
        source.sync_error = None

        # Record event
        event = KnowledgeSyncEvent(
            source_id=source.id,
            project_id=source.project_id,
            trigger=trigger,
            status="synced",
            content_hash=content.content_hash,
            previous_hash=previous_hash,
            content_changed=content_changed,
            chunk_count=chunk_count,
            duration_ms=duration_ms,
        )
        db.add(event)
        await db.commit()

        # RAG-12: Mark generated cases stale when content changed
        if content_changed:
            try:
                from app.services.rag_staleness_service import mark_cases_stale_for_source
                stale_count = await mark_cases_stale_for_source(db, source.id)
                if stale_count > 0:
                    await db.commit()
                    logger.info("marked_generated_cases_stale", stale_count=stale_count, source_id=str(source.id))
            except Exception as stale_exc:
                logger.warning("staleness_check_failed", source_id=str(source.id), error=str(stale_exc))

        logger.info(
            "knowledge_source_synced",
            source_id=str(source.id),
            chunk_count=chunk_count,
            content_changed=content_changed,
            duration_ms=duration_ms,
        )
        return {
            "status": "synced",
            "content_changed": content_changed,
            "chunk_count": chunk_count,
            "duration_ms": duration_ms,
            "content_hash": content.content_hash,
        }

    except ConnectorFetchError as exc:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        source.sync_status = KnowledgeSyncStatus.FAILED.value
        source.sync_error = str(exc)[:500]

        event = KnowledgeSyncEvent(
            source_id=source.id,
            project_id=source.project_id,
            trigger=trigger,
            status="failed",
            previous_hash=previous_hash,
            content_changed=False,
            duration_ms=duration_ms,
            error_message=str(exc)[:500],
        )
        db.add(event)
        await db.commit()

        if exc.retryable:
            raise  # let Celery retry
        return {"status": "failed", "error": str(exc), "duration_ms": duration_ms}

    except Exception as exc:
        duration_ms = int((time.monotonic() - t_start) * 1000)
        source.sync_status = KnowledgeSyncStatus.FAILED.value
        source.sync_error = str(exc)[:500]

        event = KnowledgeSyncEvent(
            source_id=source.id,
            project_id=source.project_id,
            trigger=trigger,
            status="failed",
            previous_hash=previous_hash,
            content_changed=False,
            duration_ms=duration_ms,
            error_message=str(exc)[:500],
        )
        db.add(event)
        await db.commit()

        logger.error("knowledge_source_sync_failed", source_id=str(source.id), error=str(exc))
        raise


# ── MinIO storage ─────────────────────────────────────────────────────────────


async def store_raw_content(
    source_id: str,
    project_id: str,
    content: FetchedContent,
) -> str:
    """Upload raw content to MinIO. Returns storage path."""
    key = f"{project_id}/{source_id}/{content.content_hash}.txt"
    try:
        from app.db.storage import get_storage_provider
        storage = get_storage_provider()
        await storage.put_object(
            key=key,
            content=content.raw_text.encode("utf-8"),
            content_type="text/plain",
            bucket=settings.KNOWLEDGE_DOCS_BUCKET,
        )
        return key
    except Exception as exc:
        logger.warning("minio_upload_failed", source_id=str(source_id), error=str(exc))
        return f"upload_failed/{key}"


# ── Sync history ──────────────────────────────────────────────────────────────


async def get_sync_history(
    db: AsyncSession,
    source_id: uuid.UUID,
    limit: int = 20,
) -> list:
    result = await db.execute(
        select(KnowledgeSyncEvent)
        .where(KnowledgeSyncEvent.source_id == source_id)
        .order_by(KnowledgeSyncEvent.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ── Freshness computation (RAG-6) ────────────────────────────────────────────


def compute_staleness(source: KnowledgeSource) -> tuple[bool, Optional[datetime]]:
    """Returns (is_stale, stale_since)."""
    threshold_h = _effective_threshold(source.source_type)
    if threshold_h == -1:
        return False, None  # uploaded_document: never stale
    if source.last_synced_at is None:
        return True, source.created_at  # never synced = immediately stale
    now = datetime.now(timezone.utc)
    age_h = (now - source.last_synced_at).total_seconds() / 3600
    is_stale = age_h > threshold_h
    stale_since = source.last_synced_at + timedelta(hours=threshold_h) if is_stale else None
    return is_stale, stale_since


async def get_freshness(db: AsyncSession, source: KnowledgeSource) -> dict:
    """Compute full freshness state for one source."""
    is_stale, stale_since = compute_staleness(source)
    threshold_h = _effective_threshold(source.source_type)

    hours_since = None
    if source.last_synced_at:
        hours_since = round(
            (datetime.now(timezone.utc) - source.last_synced_at).total_seconds() / 3600, 1
        )

    # Active chunk count
    chunk_count_result = await db.execute(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.source_id == source.id,
            KnowledgeChunk.is_active.is_(True),
        )
    )
    active_chunks = chunk_count_result.scalar() or 0

    # Last sync event
    last_event_result = await db.execute(
        select(KnowledgeSyncEvent)
        .where(KnowledgeSyncEvent.source_id == source.id)
        .order_by(KnowledgeSyncEvent.created_at.desc())
        .limit(1)
    )
    last_event = last_event_result.scalar_one_or_none()

    # Total sync events
    event_count_result = await db.execute(
        select(func.count(KnowledgeSyncEvent.id)).where(
            KnowledgeSyncEvent.source_id == source.id,
        )
    )
    event_count = event_count_result.scalar() or 0

    return {
        "source_id": source.id,
        "is_stale": is_stale,
        "stale_since": stale_since,
        "hours_since_sync": hours_since,
        "staleness_threshold_hours": threshold_h if threshold_h != -1 else 0,
        "content_changed_on_last_sync": last_event.content_changed if last_event else False,
        "active_chunk_count": active_chunks,
        "last_sync_status": last_event.status if last_event else None,
        "sync_event_count": event_count,
    }


# ── Stale source listing (for scheduled re-sync) ─────────────────────────────


async def list_stale_sources(
    db: AsyncSession,
    project_id: Optional[uuid.UUID] = None,
) -> list[KnowledgeSource]:
    """Return non-archived sources that are stale or failed (for re-sync scheduling)."""
    now = datetime.now(timezone.utc)
    jira_cutoff = now - timedelta(hours=settings.KNOWLEDGE_STALE_THRESHOLD_JIRA_HOURS)
    url_cutoff = now - timedelta(hours=settings.KNOWLEDGE_STALE_THRESHOLD_URL_HOURS)

    results: list[KnowledgeSource] = []

    # Jira/Confluence sources older than threshold
    jira_types = [
        KnowledgeSourceType.JIRA_ISSUE.value,
        KnowledgeSourceType.JIRA_EPIC.value,
        KnowledgeSourceType.CONFLUENCE_PAGE.value,
    ]
    stmt = (
        select(KnowledgeSource)
        .where(
            KnowledgeSource.source_type.in_(jira_types),
            KnowledgeSource.is_archived.is_(False),
            KnowledgeSource.sync_status != KnowledgeSyncStatus.SYNCING.value,
        )
        .where(
            (KnowledgeSource.last_synced_at < jira_cutoff)
            | (KnowledgeSource.last_synced_at.is_(None))
            | (KnowledgeSource.sync_status == KnowledgeSyncStatus.FAILED.value)
        )
    )
    if project_id:
        stmt = stmt.where(KnowledgeSource.project_id == project_id)
    result = await db.execute(stmt)
    results.extend(result.scalars().all())

    # URL sources older than threshold
    url_types = [
        KnowledgeSourceType.INTERNAL_URL.value,
        KnowledgeSourceType.EXTERNAL_URL.value,
    ]
    stmt = (
        select(KnowledgeSource)
        .where(
            KnowledgeSource.source_type.in_(url_types),
            KnowledgeSource.is_archived.is_(False),
            KnowledgeSource.sync_status != KnowledgeSyncStatus.SYNCING.value,
        )
        .where(
            (KnowledgeSource.last_synced_at < url_cutoff)
            | (KnowledgeSource.last_synced_at.is_(None))
            | (KnowledgeSource.sync_status == KnowledgeSyncStatus.FAILED.value)
        )
    )
    if project_id:
        stmt = stmt.where(KnowledgeSource.project_id == project_id)
    result = await db.execute(stmt)
    results.extend(result.scalars().all())

    return results


# -- Stuck-SYNCING reaper -----------------------------------------------------


async def reap_stuck_syncing_sources(
    db: AsyncSession,
    stuck_minutes: Optional[int] = None,
) -> list[KnowledgeSource]:
    """Return SYNCING sources that can no longer be syncing, marked FAILED.

    ``run_sync`` writes SYNCING and **commits it** before a fetch + chunk +
    embed that can outlive the process. Every terminal write (SYNCED / FAILED)
    lives in an ``except`` block, and a process death runs no ``except`` block:
    an OOM kill, a pod eviction, a worker redeploy or Celery's hard
    ``task_time_limit`` (1860s) all leave the row on SYNCING for ever.

    Nothing rescued it. ``list_stale_sources`` -- the only re-sync sweep --
    excludes ``sync_status != SYNCING`` on purpose, so that one source is never
    re-fetched again: its RAG chunks stay frozen at the last good sync while
    the UI shows a sync that is still "in progress" months later.

    This is the same shape as the re-quarantine dead end, and the same shape
    ``reap_stuck_agent_pipelines`` already handles for agent runs: a live state
    with no exit. The cure is the same -- age it out.

    The cutoff MUST stay above Celery's hard ``task_time_limit``; below it, a
    sync that is merely slow would be failed while it is still running and
    would then be re-enqueued alongside itself. ``updated_at`` carries
    ``onupdate=func.now()``, so writing SYNCING stamps it and it is the honest
    age of the attempt.

    Marks each row FAILED with an explanatory ``sync_error`` (which
    ``list_stale_sources`` already selects on, so the very next sweep re-queues
    it) and records a ``KnowledgeSyncEvent`` so the recovery is auditable
    rather than a silent status flip.

    Stage-only -- the caller owns ``db.commit()``.
    """
    minutes = (
        stuck_minutes
        if stuck_minutes is not None
        else getattr(settings, "KNOWLEDGE_SYNC_STUCK_MINUTES", 45)
    )
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    result = await db.execute(
        select(KnowledgeSource).where(
            KnowledgeSource.sync_status == KnowledgeSyncStatus.SYNCING.value,
            KnowledgeSource.updated_at < cutoff,
        )
    )
    stuck = list(result.scalars().all())

    for source in stuck:
        source.sync_status = KnowledgeSyncStatus.FAILED.value
        source.sync_error = (
            "sync did not finish within " + str(minutes) + " minutes and the "
            "worker is no longer running it (process killed mid-sync); "
            "released for re-sync"
        )
        db.add(
            KnowledgeSyncEvent(
                source_id=source.id,
                project_id=source.project_id,
                trigger="reaper",
                status="failed",
                content_changed=False,
                error_message="stuck in SYNCING past the " + str(minutes)
                + "-minute cutoff; no worker owns it",
            )
        )

    if stuck:
        logger.warning(
            "reaped knowledge sources stranded in SYNCING",
            count=len(stuck),
            stuck_minutes=minutes,
            source_ids=[str(s.id) for s in stuck],
        )

    return stuck
