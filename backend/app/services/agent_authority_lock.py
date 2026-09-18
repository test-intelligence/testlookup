"""Transaction locks for mutable inputs to agent execution authority.

G4 must never combine values from before and after a concurrent settings
write. Writers and evaluators share these lock domains; callers that need both
always acquire the global lock before the project lock.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text


GLOBAL_AGENT_AUTHORITY_LOCK_KEY = "agent-authority:global"


def project_agent_authority_lock_key(project_id: uuid.UUID) -> str:
    return f"agent-config-authority:{project_id}"


async def lock_global_agent_authority(db: Any) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": GLOBAL_AGENT_AUTHORITY_LOCK_KEY},
    )


async def lock_project_agent_authority(db: Any, project_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": project_agent_authority_lock_key(project_id)},
    )


async def lock_agent_authority_snapshot(db: Any, project_id: uuid.UUID) -> None:
    """Freeze global then project-scoped authority for this transaction."""
    await lock_global_agent_authority(db)
    await lock_project_agent_authority(db, project_id)


__all__ = [
    "GLOBAL_AGENT_AUTHORITY_LOCK_KEY",
    "lock_agent_authority_snapshot",
    "lock_global_agent_authority",
    "lock_project_agent_authority",
    "project_agent_authority_lock_key",
]
