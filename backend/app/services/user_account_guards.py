"""Account guards shared by the user routes, SCIM, SSO and the erasure (W31 STRAT-12; contract §0.21, E-1, E-4).

* :func:`assert_not_last_admin` — MOVED from ``routers/users.py`` (``users.py``
  re-imports it under its old private name): demoting / deactivating the last
  active ADMIN is refused. :func:`is_last_active_admin` is the same rule as a
  predicate, for the erasure's ``last_admin`` refusal (reused, not copied).
* :func:`refuse_erased` — an erased account (``users.erased_at`` set) is never
  changed or restored: 409 with the string sentence (the user router's style).
* :func:`refuse_reserved_username` — the pseudonym space ``erased-…`` cannot be
  squatted: a username (or an e-mail local part) starting with it is 422.

Stage-only (``STAGE_ONLY_SERVICES``): reads at most, never writes, never commits.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _normalize_user_role
from app.models.postgres import User, UserRole
from app.services.subject_rights_vocab import ERROR_SENTENCES, PSEUDONYM_PREFIX

#: The string ``detail`` of an erased account's refusal (409) on the existing routes.
USER_ERASED_DETAIL = ERROR_SENTENCES["user_erased"]
#: The string ``detail`` of a reserved username / e-mail local part (422).
USERNAME_RESERVED_DETAIL = ERROR_SENTENCES["username_reserved"]
LAST_ADMIN_DETAIL = "Cannot remove the last active administrator"


# ── the last active administrator ────────────────────────────────────────────

def _is_active_admin(user: Any) -> bool:
    try:
        role = _normalize_user_role(user.role)
    except ValueError:
        return False
    return role == UserRole.ADMIN and bool(user.is_active)


async def is_last_active_admin(db: AsyncSession, user: Any) -> bool:
    """True when ``user`` is an ACTIVE ADMIN and no OTHER active ADMIN exists."""
    if not _is_active_admin(user):
        return False
    other_admins = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.role == UserRole.ADMIN,
                User.is_active == True,  # noqa: E712
                User.id != user.id,
            )
        )
    ).scalar() or 0
    return other_admins == 0


async def assert_not_last_admin(db: AsyncSession, target_user: Any) -> None:
    """Block demoting / deactivating the last remaining active ADMIN (400, the user router's sentence).

    Self-changes are already blocked upstream; this guards the case where an
    ADMIN removes admin access from the *only other* admin, locking everyone out.
    """
    if await is_last_active_admin(db, target_user):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=LAST_ADMIN_DETAIL)


# ── erased accounts ──────────────────────────────────────────────────────────

def is_erased(user: Any) -> bool:
    """``users.erased_at`` is set (E-11b: the one meaning of "erased"). Only a real timestamp counts, so a
    test double without the column (a mock's auto-attribute) never reads as erased."""
    return isinstance(getattr(user, "erased_at", None), datetime)


def refuse_erased(user: Any) -> None:
    """409 ``"This account was erased; it cannot be changed or restored."`` for an erased account."""
    if is_erased(user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=USER_ERASED_DETAIL)


# ── the reserved pseudonym prefix ────────────────────────────────────────────

def is_reserved_username(value: Optional[str]) -> bool:
    """True when ``value`` starts with ``erased-`` (case-insensitive, surrounding blanks ignored)."""
    if not value:
        return False
    return str(value).strip().lower().startswith(PSEUDONYM_PREFIX)


def is_reserved_identity(*, username: Optional[str] = None, email: Optional[str] = None) -> bool:
    """A reserved username, or an e-mail whose LOCAL part starts with ``erased-``."""
    if is_reserved_username(username):
        return True
    if email:
        return is_reserved_username(str(email).strip().rsplit("@", 1)[0])
    return False


def refuse_reserved_username(*, username: Optional[str] = None, email: Optional[str] = None) -> None:
    """422 ``'Usernames starting with "erased-" are reserved.'`` (string detail) for a reserved value."""
    if is_reserved_identity(username=username, email=email):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=USERNAME_RESERVED_DETAIL)
