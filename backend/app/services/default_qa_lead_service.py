"""Per-project default QA-lead user (auto-provisioned).

Every project gets a dedicated QA-lead user at creation time so failed
test cases always have a deterministic assignee for the ``/my-failures``
inbox. The auto-provisioned user:

* has username/email derived from the project slug (``qalead-<slug>``)
* gets role ``QA_LEAD``
* is added to the project as a ``ProjectMember`` with role ``QA_LEAD``
* is stamped as ``Project.default_qa_lead_user_id``
* starts with a RANDOM, discarded password (nobody can log in as it) so
  operators can rotate it via the password-reset endpoint without first
  pulling it out of a side-channel.

The helpers are idempotent: re-running ``ensure_default_qa_lead`` on a
project that already has one returns the existing user without mutating
anything.
"""
from __future__ import annotations

from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import secrets

from app.core.security import get_password_hash
from app.core.token_revocation import revoke_all_user_tokens
from app.models.postgres import Project, ProjectMember, User, UserRole
from app.services.refresh_token_service import _revoke_family as _revoke_refresh_family

logger = structlog.get_logger(__name__)

# Default password assigned at provision time. Documented so operators
# know the starting state — production setups should rotate this via
# the reset-password endpoint after first login.
def _unguessable_password() -> str:
    """A fresh high-entropy secret for a synthetic account.

    There used to be a module constant here holding one fixed password for
    every synthetic QA-lead account in every deployment. It was hard-coded in
    an open-source file, the accounts are created automatically (one per
    project — 42 on one measured deployment), they are provisioned
    ``is_active=True``, and ``must_change_password`` does not block login (it
    is only a prompt flag on the login response). So anyone who had read this
    file could authenticate as QA_LEAD on any deployment that had ever created
    a project, and enumerate users, projects and runs. Verified against a live
    deployment before this change: HTTP 200 and a token.

    These accounts exist to OWN auto-assignments, which is server-side and
    needs no login. An operator who genuinely wants to use one resets it
    through ``reset_default_qa_lead_password`` — which is the path the module
    docstring already described — and gets a value nobody else shares.
    """
    return secrets.token_urlsafe(32)

# Email domain used for the synthetic accounts. Kept distinct from real
# user domains so directory-style listings can filter them out cheaply.
_DEFAULT_QA_LEAD_DOMAIN = "qa-lead.testlookup.local"


def _slugify_for_username(slug: str) -> str:
    """Trim a project slug to fit the ``users.username`` 100-char cap with
    the ``qalead-`` prefix, while keeping the slug recognisable.
    """
    base = (slug or "").strip().lower()
    if not base:
        base = "project"
    candidate = f"qalead-{base}"
    return candidate[:100]


def _default_email(slug: str) -> str:
    """Email for the synthetic QA-lead user. Matches the username so the
    login form works with either field.
    """
    base = (slug or "").strip().lower() or "project"
    return f"qalead-{base}@{_DEFAULT_QA_LEAD_DOMAIN}"[:255]


def _default_full_name(project_name: str) -> str:
    return f"{project_name} QA Lead"[:255]


async def ensure_default_qa_lead(
    db: AsyncSession,
    project: Project,
) -> User:
    """Return the project's default QA-lead user, creating one if absent.

    Idempotent across:
      * Projects that already have ``default_qa_lead_user_id`` set — returns
        the existing user, no mutation.
      * Projects that don't, but where the synthetic user already exists
        (e.g. a previous create-then-rollback) — attaches the existing user
        and writes the FK without creating a duplicate.

    The caller owns the transaction (single-owner rule: services flush,
    routers commit). This service only ``flush``-es so the new user's
    PK is materialised; commit happens at the caller's request boundary.
    """
    if project.default_qa_lead_user_id is not None:
        existing = await db.get(User, project.default_qa_lead_user_id)
        if existing is not None:
            return existing
        # FK points at a deleted user — fall through and re-provision so
        # the column never dangles.

    target_email = _default_email(project.slug)
    target_username = _slugify_for_username(project.slug)

    # Reuse a synthetic user if one already exists (idempotent retry).
    found = (
        await db.execute(select(User).where(User.email == target_email))
    ).scalar_one_or_none()
    if found is None:
        found = User(
            email=target_email,
            username=target_username,
            full_name=_default_full_name(project.name),
            # Random and immediately discarded: nothing needs to log in as
            # this account, so no caller ever learns the value.
            hashed_password=get_password_hash(_unguessable_password()),
            role=UserRole.QA_LEAD.value,
            is_active=True,
            # Defence in depth. It does not block login on its own, but if an
            # operator later resets this account the UI prompts them.
            must_change_password=True,
            # Nobody logs in as this account, so the review gate must refuse it
            # (migration 0175, architecture section 8.3).
            is_synthetic=True,
        )
        db.add(found)
        await db.flush()
        logger.info(
            "default_qa_lead_user_created",
            project_id=str(project.id),
            user_id=str(found.id),
            email=target_email,
        )
    elif not found.is_synthetic:
        # Provisioned before migration 0175 and missed by its email-domain
        # backfill (an address changed since, say): repair it here.
        found.is_synthetic = True

    # Ensure the synthetic user is a project member at QA_LEAD.
    member_row = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == found.id,
            )
        )
    ).scalar_one_or_none()
    if member_row is None:
        db.add(
            ProjectMember(
                user_id=found.id,
                project_id=project.id,
                role=UserRole.QA_LEAD.value,
            )
        )
        await db.flush()
    elif member_row.role != UserRole.QA_LEAD.value:
        member_row.role = UserRole.QA_LEAD.value

    # Stamp the FK so downstream auto-assignment short-circuits to this user.
    if project.default_qa_lead_user_id != found.id:
        project.default_qa_lead_user_id = found.id
        await db.flush()

    return found


class DefaultQaLeadProjectArchived(Exception):
    """S-D-3: the project is archived, so its shared QA-lead login stays off.

    Archive disables the account on purpose
    (``project_access_revocation_service``); a reset that handed out a password
    for it would either re-open a login into an archived project or (before
    this) answer 200 with a password that cannot sign in.
    """


class DefaultQaLeadTurnedOff(Exception):
    """The shared account is turned off and the caller may not turn it back on.

    ``is_active=False`` does not say who turned it off: archive does
    (``project_access_revocation_service``), and so does an admin with
    ``PATCH /users/{id}/status``, which is ADMIN only. A project QA lead's
    reset must not undo an admin's decision, so only an admin's reset
    re-enables the account (restore is ADMIN only too, so the restore path
    still works).
    """


class DefaultQaLeadIsPersonalAccount(Exception):
    """The project's default QA lead is a person, not the shared account.

    ``Project.default_qa_lead_user_id`` may name a real member (suite-review
    routing uses it that way). The reset below replaces the password and hands
    the new one back to the caller, which is only acceptable for the
    auto-provisioned shared account (``User.is_synthetic``). A person's
    password is changed through the normal account flows.
    """


async def reset_default_qa_lead_password(
    db: AsyncSession,
    project: Project,
    new_password: Optional[str] = None,
    *,
    may_reactivate: bool = False,
) -> tuple[User, str]:
    """Reset the project's default QA-lead user password.

    ``may_reactivate``: the caller is an admin, so a turned-off account comes
    back on. Without it a turned-off account is refused
    (``DefaultQaLeadTurnedOff``) and nothing changes.

    Returns ``(user, password_used)``. When ``new_password`` is omitted a
    fresh random secret is generated and returned, so the caller can still
    hand the operator a known starting value without leaking the live hash —
    the contract is unchanged, but the value is unique to this reset instead
    of a constant shared by every deployment. Caller owns commit.

    **Existing sessions are ended too.** This is the compromise-response
    path for these accounts — it is what an operator runs after the shared
    credential leaked — so changing the hash on its own is not enough. A
    password change that leaves prior sessions alive rotates nothing an
    attacker cares about: their access token stays valid for up to
    ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES``, and their refresh token keeps
    minting fresh ones indefinitely, long after the password it was issued
    against is gone. Verified live against a real deployment before this
    change: after a reset, the pre-reset refresh token still returned 200
    from ``/auth/refresh`` and the access token it minted read ``/users``.

    ``/auth/change-password`` and ``/auth/first-time-reset`` have always
    done both revocations; this path is the one that did neither. The
    revocations live here rather than in the endpoint so they cannot be
    skipped by a second caller.

    Ordering: the refresh-family revocation and durable access-token cutoff
    are DB writes in the caller's transaction, so they land with the new hash
    or not at all. Caller-owned transactions do not publish the cutoff to Redis
    before commit; PostgreSQL is the first authority on every cutoff read.
    """
    if getattr(project, "is_active", True) is False:
        raise DefaultQaLeadProjectArchived()
    user = await ensure_default_qa_lead(db, project)
    # Refused here, not in the router, so no second caller can skip it. ``is
    # not True`` on purpose: a row without the flag is treated as a person.
    if getattr(user, "is_synthetic", False) is not True:
        logger.warning(
            "default_qa_lead_password_reset_refused",
            project_id=str(project.id),
            user_id=str(user.id),
            reason="personal_account",
        )
        raise DefaultQaLeadIsPersonalAccount()
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    await db.refresh(user)
    if getattr(user, "is_active", True) is False and not may_reactivate:
        logger.warning(
            "default_qa_lead_password_reset_refused",
            project_id=str(project.id),
            user_id=str(user.id),
            reason="account_turned_off",
        )
        raise DefaultQaLeadTurnedOff()
    chosen = new_password if new_password else _unguessable_password()
    user.hashed_password = get_password_hash(chosen)
    user.must_change_password = False
    # S-D-3: archive disabled the account; a restored project's QA lead comes
    # back through an admin's reset (the restore route says so). Without this
    # the reset answered 200 with a password that could not sign in. Reached
    # with the account off only when ``may_reactivate`` (checked above).
    user.is_active = True
    await _revoke_refresh_family(db, user.id, reason="password_reset")
    await db.flush()
    await revoke_all_user_tokens(user.id, db)
    logger.info(
        "default_qa_lead_password_reset",
        project_id=str(project.id),
        user_id=str(user.id),
        custom_password=new_password is not None,
    )
    return user, chosen


async def backfill_default_qa_lead_for_all_projects(
    db: AsyncSession,
) -> dict[str, int]:
    """Walk every active project and ensure it has a default QA lead user.

    Used at deploy time (and via the Celery beat alongside the inbox
    backfill) so existing projects don't stay locked out of the
    ``/my-failures`` flow after this feature lands.
    """
    totals = {"provisioned": 0, "already_set": 0, "errors": 0}
    projects = (
        await db.execute(
            select(Project).where(Project.is_active.is_(True))
        )
    ).scalars().all()
    for project in projects:
        had_lead = project.default_qa_lead_user_id is not None
        try:
            await ensure_default_qa_lead(db, project)
            if had_lead:
                totals["already_set"] += 1
            else:
                totals["provisioned"] += 1
        except Exception as exc:  # pragma: no cover - tolerate per-row
            totals["errors"] += 1
            logger.warning(
                "default_qa_lead_backfill_failed",
                project_id=str(project.id),
                error=str(exc),
            )
    return totals
