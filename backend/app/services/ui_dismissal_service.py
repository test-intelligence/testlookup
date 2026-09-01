"""Per-user UI dismissals — remembering that an operator said "not now".

First consumer is the retention-activation nudge (S1). Retention ships
enabled=False and nothing ever asked an operator to turn it on, so the feature
is present, discoverable and inert; the nudge asks once per user.

Transaction model: this module **stages only**. ``record_dismissal`` issues an
idempotent INSERT and returns; the router owns ``await db.commit()`` so the
dismissal and anything else in the request land together.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import UserUIDismissal

logger = structlog.get_logger(__name__)

#: The retention-activation prompt (S1).
RETENTION_ACTIVATION_NUDGE = "retention_activation_nudge"

#: Closed vocabulary. An allowlist keeps this table from becoming a junk drawer
#: of typo'd keys that silently never match on read — a dismissal that does not
#: suppress anything looks identical to one that was never recorded.
KNOWN_DISMISSAL_KEYS: frozenset[str] = frozenset({RETENTION_ACTIVATION_NUDGE})


async def list_dismissal_keys(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Every prompt this user has dismissed. Read-only."""
    result = await db.execute(
        select(UserUIDismissal.dismissal_key).where(
            UserUIDismissal.user_id == user_id
        )
    )
    return sorted(result.scalars().all())


async def record_dismissal(
    db: AsyncSession, user_id: uuid.UUID, dismissal_key: str
) -> None:
    """Stage an idempotent dismissal. The caller commits.

    Raises 422 for a key outside :data:`KNOWN_DISMISSAL_KEYS`.

    Idempotent via ``ON CONFLICT DO NOTHING`` rather than read-then-write: two
    concurrent requests (a double-clicked button, a retried POST) would both
    pass an existence check and the second INSERT would raise on the unique
    constraint, 500-ing a dismissal that had in fact succeeded.
    """
    if dismissal_key not in KNOWN_DISMISSAL_KEYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown dismissal key '{dismissal_key}'. "
                f"Known keys: {', '.join(sorted(KNOWN_DISMISSAL_KEYS))}"
            ),
        )

    await db.execute(
        pg_insert(UserUIDismissal)
        .values(id=uuid.uuid4(), user_id=user_id, dismissal_key=dismissal_key)
        .on_conflict_do_nothing(constraint="uq_user_ui_dismissal")
    )
    logger.info(
        "ui_dismissal_recorded",
        user_id=str(user_id),
        dismissal_key=dismissal_key,
    )
