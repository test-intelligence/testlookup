"""Row-level persistence helpers for the Investigator (Agentic plan AI-1).

The investigation row (``agent_investigations``) is the progress surface the
UI polls, so hypothesis nodes persist their result the moment they finish.
Parallel nodes each open their own session; the read-modify-write on the
``hypotheses`` JSONB list is serialized with ``SELECT ... FOR UPDATE`` so two
concurrently-finishing hypotheses never clobber each other's element.

No classes here on purpose — this is a support module (like the chassis
``workflow.py``), not an agent.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AgentInvestigation

logger = structlog.get_logger("agents.investigator.persistence")


async def is_cancel_requested(investigation_id: str) -> bool:
    """Cooperative-cancel check, called between nodes. Fail-safe: a broken
    read returns False (keep running) rather than killing the graph."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentInvestigation.cancel_requested).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
            return bool(result.scalar_one_or_none())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cancel_check_failed",
            investigation_id=investigation_id,
            error=str(exc),
        )
        return False


async def persist_hypothesis(investigation_id: str, hypothesis: dict[str, Any]) -> None:
    """Replace the hypothesis element (matched by ``id``) on the row's
    ``hypotheses`` JSONB list. FOR UPDATE serializes parallel finishers.
    Best-effort: a write fault must not fail the node — the final state is
    persisted again by the workflow runner at completion."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentInvestigation)
                .where(AgentInvestigation.id == uuid.UUID(investigation_id))
                .with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            existing = list(row.hypotheses or [])
            replaced = False
            for i, entry in enumerate(existing):
                if entry.get("id") == hypothesis.get("id"):
                    existing[i] = hypothesis
                    replaced = True
                    break
            if not replaced:
                existing.append(hypothesis)
            row.hypotheses = existing
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hypothesis_persist_failed",
            investigation_id=investigation_id,
            hypothesis_id=hypothesis.get("id"),
            error=str(exc),
        )


async def set_investigation_status(
    investigation_id: str,
    status: str,
    *,
    started_at: Optional[datetime] = None,
) -> None:
    """Advance the lifecycle status (queued→running→synthesizing). Terminal
    states are written by the workflow runner's finalizer, not here."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentInvestigation).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.status = status
            if started_at is not None and row.started_at is None:
                row.started_at = started_at
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "investigation_status_write_failed",
            investigation_id=investigation_id,
            status=status,
            error=str(exc),
        )


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
