"""Who owns a release (REL-02).

A release's owner is an optional person who can work on its project. Until W05
the release card printed the PROJECT name where an owner belonged, so every
release of a project read as "owned" by the same team label, and searching for
a person found nothing.

Three rules live here, in one place, so the create, update, list and detail
paths cannot drift apart:

* **Who may own.** An active, interactive human: not a service account, not a
  synthetic account (the per-project QA lead the seeder creates), and either an
  ADMIN or a member of the release's project. An ADMIN is deliberately allowed
  without a membership row: ``get_accessible_project_ids`` answers ``None`` for
  an ADMIN (they reach every project), so refusing them would make a person who
  can act on the release unable to own it.
* **What the reader sees.** ``{"id", "name"}`` for an eligible owner, ``None``
  otherwise. A stored owner that is a service / synthetic account, or that was
  deactivated, reads as "No owner": naming a robot or a departed user as the
  person responsible is worse than saying nobody is.
* **The default.** The creating human, when the create came from an
  interactive sign-in. An API key or a service account creates releases from CI
  and owns nothing.

One refusal text for every way an id can be ineligible (malformed, unknown,
inactive, service, synthetic, not a member): a distinct message per case would
let any QA lead probe which user ids exist and what they are.
"""
from __future__ import annotations

import uuid
from typing import Any, Collection, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ProjectMember, User, UserRole

#: The 422 text for every ineligible owner id (no user enumeration).
OWNER_INELIGIBLE = "That user cannot own releases in this project."

#: What the UI prints when ``owner`` is ``None``. Used for ledger diffs, which
#: carry names, never ids.
NO_OWNER_LABEL = "No owner"


def _is_admin(role: Any) -> bool:
    """The same role reading the auth guards use (``_normalize_user_role``)."""
    from app.core.deps import _normalize_user_role

    try:
        return _normalize_user_role(role) == UserRole.ADMIN
    except (ValueError, TypeError):
        return False


def _eligible(user: Any) -> bool:
    """Active, and an interactive human account."""
    if user is None:
        return False
    if getattr(user, "is_active", True) is False:
        return False
    if getattr(user, "is_service_account", False):
        return False
    if getattr(user, "is_synthetic", False):
        return False
    return True


def display_name(user: Any) -> str:
    return getattr(user, "full_name", None) or getattr(user, "username", None) or ""


def _refuse() -> HTTPException:
    return HTTPException(status_code=422, detail=OWNER_INELIGIBLE)


async def validate_owner(
    db: AsyncSession, project_id: uuid.UUID, owner_user_id: str
) -> uuid.UUID:
    """The owner id to store, or 422 ``OWNER_INELIGIBLE``.

    ``owner_user_id`` is a BODY id and is checked like any path id: it must name
    a user who may work on ``project_id``. The ownership ratchet cannot see body
    ids, so this check is the only thing between a QA lead and a release
    "owned" by a user of another tenant.
    """
    try:
        wanted = uuid.UUID(str(owner_user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise _refuse() from exc

    user = await db.get(User, wanted)
    if not _eligible(user):
        raise _refuse()
    if _is_admin(getattr(user, "role", None)):
        return wanted

    member = await db.scalar(
        select(ProjectMember.id)
        .where(
            ProjectMember.user_id == wanted,
            ProjectMember.project_id == project_id,
        )
        .limit(1)
    )
    if member is None:
        raise _refuse()
    return wanted


async def owners_by_id(
    db: AsyncSession, owner_ids: Collection[Optional[uuid.UUID]]
) -> dict[uuid.UUID, dict]:
    """``{owner_id: {"id", "name"}}`` for the ELIGIBLE owners among ``owner_ids``.

    ONE statement for a whole list, however many releases it covers, and none
    at all when no release has an owner. An id that is missing from the answer
    (deleted, deactivated, service, synthetic) is how a reader learns "No owner".
    """
    wanted = {oid for oid in owner_ids if oid is not None}
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(
                User.id,
                User.full_name,
                User.username,
                User.is_active,
                User.is_service_account,
                User.is_synthetic,
            ).where(User.id.in_(list(wanted)))
        )
    ).all()
    owners: dict[uuid.UUID, dict] = {}
    for row in rows:
        if not _eligible(row):
            continue
        owners[row.id] = {"id": str(row.id), "name": display_name(row)}
    return owners


async def owner_for(db: AsyncSession, owner_id: Optional[uuid.UUID]) -> Optional[dict]:
    """The ``owner`` value for a single release (``None`` = "No owner")."""
    if owner_id is None:
        return None
    return (await owners_by_id(db, [owner_id])).get(owner_id)


def default_owner_for(actor: Any) -> Optional[uuid.UUID]:
    """The creating actor when they are an interactive human, else ``None``.

    Interactive = authenticated by a JWT sign-in (``credential_kind == "jwt"``).
    An API key, an unknown credential (a row loaded by a service, a fixture),
    a service account and a synthetic account all fall out: a release created by
    CI has no person behind it, and pretending otherwise would put a robot on
    the card.
    """
    if actor is None:
        return None
    from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind

    if credential_kind(actor) != CREDENTIAL_KIND_JWT:
        return None
    if not _eligible(actor):
        return None
    return getattr(actor, "id", None)
