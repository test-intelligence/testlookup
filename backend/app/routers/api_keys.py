"""API Key management — generate, list, revoke scoped PATs."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    API_KEY_SCOPES,
    _api_key_bound_project,
    api_key_grant,
    require_api_key_owner,
    require_role,
    takes_scoped_key_writes,
)
from app.db.postgres import get_db
from app.models.postgres import ApiKey, Project, User, UserRole
from app.models.schemas import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from app.services.activity.service import ActorRef, record as record_activity
from app.services.live_event_authz import forget_streaming_key_hash

router = APIRouter(prefix="/api/v1/keys", tags=["API Keys"])


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _normalize_scopes(scopes: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if scopes is None:
        return []
    if isinstance(scopes, str):
        return [scopes] if scopes else []
    # Blanks dropped, duplicates collapsed, first-seen order kept (QA-R4 P4:
    # ["stream:write", "stream:write", ""] was stored with two entries).
    return list(dict.fromkeys(str(scope) for scope in scopes if scope))


def _build_api_key_response(api_key: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=api_key.id,
        name=api_key.name,
        key_hint=api_key.key_hint,
        scopes=_normalize_scopes(api_key.scopes),
        project_id=api_key.project_id,
        is_active=api_key.is_active,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
    )


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
@takes_scoped_key_writes  # mints a subset of the caller's scopes and expiry (below)
async def create_api_key(
    payload: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Generate a new scoped API key. The raw key is only shown once.

    ADMIN can supply ``project_id`` to restrict the key to a single project
    and ``target_user_id`` to create a key on behalf of another user.

    A caller authenticated with a project-bound key may mint only a key bound
    to that same project, for its own owner (re-audit N20). That is what CI
    needs to rotate its key, and nothing wider.

    A caller authenticated with an API key also grants no more than it holds
    (QA-R3-11):

    * scopes: if the caller's key is scoped, the new key's scopes must be a
      subset of them. Omitting ``scopes`` inherits the caller's; an explicit
      empty list (a full-access key) is refused. A legacy unscoped caller
      mints as before.
    * expiry: the new key may not expire later than the caller's key.
      Omitting ``expires_days`` inherits the caller's expiry. A caller with
      no expiry mints as before.

    So ``testlookup keys create``, which sends only a name, rotates a scoped,
    expiring CI key into one with the same scopes and the same end date.

    Scopes: an empty list is a full-access key. ``stream:write`` is required
    by the streaming ingest endpoints. ``project:admin`` is required by the
    project-administration routes a project-bound key may use (run deletion,
    project reset, retention and deletion jobs, member removal, release/phase
    deletion, compliance packs, release-gate policies).
    """
    # ── a project-bound caller stays inside its project (re-audit N20) ───
    # Only an ADMIN can bind a key to a project, so a bound key is usually an
    # ADMIN's CI credential. Omitting ``project_id`` used to mint its owner an
    # UNBOUND key, a credential for every project that passes
    # ``require_instance_admin``, and ``target_user_id`` minted one for anyone.
    bound_project_id = _api_key_bound_project(current_user)
    if bound_project_id is not None:
        if payload.project_id is None:
            # Naming no project means this one. The new key stays bound, and
            # `testlookup keys create`, which sends no project_id, keeps
            # working for CI rotation (lead review of the N20 fix).
            payload = payload.model_copy(update={"project_id": bound_project_id})
        if payload.project_id != bound_project_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This API key is bound to one project; it can only create "
                    "keys bound to that same project. Set project_id to it."
                ),
            )
        if payload.target_user_id is not None and payload.target_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This API key is bound to one project; it can only create "
                    "keys for its own owner"
                ),
            )

    # ── a key minting a key grants no more than it holds (QA-R3-11) ──────
    # A ["stream:write"] CI key used to mint itself a ``scopes: []`` (full
    # access), never-expiring replacement, which outlived revoking the
    # original and every scope and expiry the operator had chosen.
    grant = api_key_grant(current_user)
    scopes = _normalize_scopes(payload.scopes)
    # ── only scopes something enforces (re-audit N31) ────────────────────
    # The key form offered test:read/write, report:read/write and admin:read,
    # which nothing read: a key "limited" to report:read acted with its
    # owner's full role. Checked on what the caller ASKED for; a legacy key
    # rotating an old unknown scope inherits it, and an unknown scope grants
    # nothing (a scoped key without project:write cannot write).
    unknown = sorted(set(scopes) - set(API_KEY_SCOPES))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Unknown API key scope(s): " + ", ".join(unknown)
                + ". Valid scopes: " + ", ".join(API_KEY_SCOPES)
            ),
        )
    if grant is not None and grant.is_scoped:
        if not scopes:
            if "scopes" in payload.model_fields_set:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "This API key is scoped; it cannot create a full-access "
                        "key (an empty scope list). Request a subset of: "
                        + ", ".join(grant.scopes)
                    ),
                )
            scopes = list(grant.scopes)
        beyond = sorted(set(scopes) - set(grant.scopes))
        if beyond:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This API key can grant only the scopes it holds ("
                    + ", ".join(grant.scopes)
                    + "); it does not hold: "
                    + ", ".join(beyond)
                ),
            )

    expires_at = None
    if payload.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_days)
    if grant is not None and grant.expires_at is not None:
        if expires_at is None:
            expires_at = grant.expires_at
        elif expires_at > grant.expires_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A key created with an API key may not outlive it: this key "
                    f"expires at {grant.expires_at.isoformat()}. Omit expires_days "
                    "to inherit that date, or ask for fewer days."
                ),
            )

    # ── target user resolution ────────────────────────────────────────────
    owner_id = current_user.id
    if payload.target_user_id is not None:
        if current_user.role != UserRole.ADMIN.value and current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only ADMIN can create keys for other users",
            )
        target_result = await db.execute(select(User).where(User.id == payload.target_user_id))
        target_user = target_result.scalar_one_or_none()
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")
        owner_id = payload.target_user_id

    # ── project validation ────────────────────────────────────────────────
    if payload.project_id is not None:
        if current_user.role != UserRole.ADMIN.value and current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only ADMIN can create project-scoped API keys",
            )
        project_result = await db.execute(
            select(Project.id).where(Project.id == payload.project_id)
        )
        if not project_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # ── key generation ────────────────────────────────────────────────────
    # ── a key being revoked cannot mint (review R-B45-D-4) ───────────────
    # The caller's key was active when it authenticated, but a revoke can
    # commit between that and this INSERT. The FK's own KEY SHARE lock does
    # not conflict with the revoke's UPDATE, and the revoke's cascade cannot
    # see this uncommitted child, so the child used to commit active under a
    # revoked parent. FOR SHARE on the parent does conflict with that UPDATE:
    # either this mint holds it first -- the revoke waits for our commit, and
    # its cascade then sees the child -- or the revoke does, and we read its
    # committed is_active = false and refuse. Lock order: a mint locks only
    # its parent, so it cannot close a cycle with a revoke.
    if grant is not None:
        await _lock_active_minting_parent(db, grant.key_id)

    raw_key = f"qai_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    key_hint = raw_key[:8] + "..."

    api_key = ApiKey(
        # Identity at construction: the ledger row is staged in this same
        # transaction and needs a stable entity_id without an extra flush.
        id=uuid.uuid4(),
        user_id=owner_id,
        name=payload.name,
        key_hash=key_hash,
        key_hint=key_hint,
        scopes=scopes,
        project_id=payload.project_id,
        expires_at=expires_at,
        # Which key minted this one, so revoking it revokes this too (re-audit
        # N35). NULL when a signed-in user mints.
        minted_by_key_id=grant.key_id if grant is not None else None,
    )
    db.add(api_key)

    # Epic ACT. Only a PROJECT-scoped key produces a ledger row: the ledger is
    # project-scoped, and a user-scoped key belongs to no project, so there is
    # nowhere honest to file it. Filing it under an arbitrary project would be
    # worse than the gap — it is the same reason settings_audit_log rows are
    # invisible to non-admins.
    #
    # ``changed_fields`` only, never values: the raw key is returned to the
    # caller exactly once and must not be reconstructable from the feed.
    if api_key.project_id is not None:
        await record_activity(
            db,
            project_id=api_key.project_id,
            event_type="api_key.created",
            actor=ActorRef.from_user(current_user),
            entity_id=api_key.id,
            entity_label=api_key.name,
            changed_fields=["name", "scopes", "expires_at", "project_id"],
            context={"key_hint": api_key.key_hint, "scopes": api_key.scopes},
        )

    await db.commit()
    await db.refresh(api_key)

    return ApiKeyCreatedResponse(
        **_build_api_key_response(api_key).model_dump(),
        raw_key=raw_key,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    project_id: Optional[uuid.UUID] = Query(None, description="Filter by project (ADMIN only)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """List active API keys. Non-admin users see only their own keys.
    ADMIN can filter by project_id to see all keys bound to a project.

    A caller authenticated with a project-bound key sees only keys bound to
    that project, and naming another project is a 403 (re-audit N20).
    """
    stmt = select(ApiKey).where(ApiKey.is_active == True)  # noqa: E712

    bound_project_id = _api_key_bound_project(current_user)
    if (
        bound_project_id is not None
        and project_id is not None
        and project_id != bound_project_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is restricted to a different project",
        )

    is_admin = current_user.role == UserRole.ADMIN.value or current_user.role == UserRole.ADMIN
    if is_admin and project_id is not None:
        stmt = stmt.where(ApiKey.project_id == project_id)
    else:
        stmt = stmt.where(ApiKey.user_id == current_user.id)
        if bound_project_id is not None:
            # Its owner's keys, but only those bound to the same project: a
            # key for one project does not learn about its owner's others.
            stmt = stmt.where(ApiKey.project_id == bound_project_id)

    stmt = stmt.order_by(ApiKey.created_at.desc())
    result = await db.execute(stmt)
    return [_build_api_key_response(api_key) for api_key in result.scalars().all()]


@router.delete("/{key_id}", status_code=204)
@takes_scoped_key_writes  # a key without project:admin revokes only itself (below)
async def revoke_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_api_key_owner()),
):
    """Revoke (soft-delete) an API key. Only the owner can revoke their own keys.

    A caller authenticated with a project-bound key may revoke only keys bound
    to that project; ``require_api_key_owner`` refuses the rest (re-audit N20).
    """
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    # A scoped key without project:admin may revoke itself (a leaked CI key
    # retiring its own credential), never its owner's other keys (review of
    # QA-R4-1: a stream key revoked its owner's project:admin key).
    from app.core.deps import (  # noqa: PLC0415
        PROJECT_ADMIN_SCOPE,
        PROJECT_ADMIN_SCOPE_DETAIL,
        api_key_grant,
    )

    grant = api_key_grant(current_user)
    if grant is not None and not grant.allows(PROJECT_ADMIN_SCOPE) and api_key.id != grant.key_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_ADMIN_SCOPE_DETAIL)
    api_key.is_active = False

    # ── the keys this key minted go with it (re-audit N35) ───────────────
    # A leaked key could mint itself a replacement, and revoking the leaked
    # key left the replacement working. Every ACTIVE key below this one --
    # at any depth, whoever owns it -- is revoked in this same transaction.
    # Keys minted before migration 0168 have no parent recorded (roots).
    descendants = await _revoke_subtree(db, api_key.id)

    if api_key.project_id is not None:
        await record_activity(
            db,
            project_id=api_key.project_id,
            event_type="api_key.revoked",
            actor=ActorRef.from_user(current_user),
            entity_id=api_key.id,
            entity_label=api_key.name,
            context={"key_hint": api_key.key_hint, "revoked_descendants": len(descendants)},
        )
    for child in descendants:
        if child.project_id is not None:
            await record_activity(
                db,
                project_id=child.project_id,
                event_type="api_key.revoked",
                actor=ActorRef.from_user(current_user),
                entity_id=child.id,
                entity_label=child.name,
                context={"key_hint": child.key_hint, "cascade_from": str(api_key.id)},
            )

    await db.commit()

    # The live-event path caches each credential's project for a few seconds
    # so the full check does not run per event. Revocation is exactly when
    # that lag is least acceptable, and ApiKey.key_hash IS the cache digest,
    # so drop every revoked key's entry now rather than waiting for the TTL.
    # After the commit: dropped before it, a request in between re-cached the
    # key from a row that was still active.
    for key_hash in (api_key.key_hash, *(child.key_hash for child in descendants)):
        await forget_streaming_key_hash(key_hash)
    return None


_MINTING_PARENT = text(
    "SELECT is_active FROM api_keys WHERE id = :parent FOR SHARE"
).bindparams(bindparam("parent", type_=PG_UUID(as_uuid=True)))


async def _lock_active_minting_parent(db: AsyncSession, parent_id: uuid.UUID) -> None:
    """Hold the minting key's row for the mint's transaction; refuse it if revoked."""
    active = (await db.execute(_MINTING_PARENT, {"parent": parent_id})).scalar_one_or_none()
    if not active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This API key has been revoked",
        )


# Passes of the cascade before giving up; a pass only finds more keys when a
# mint committed under the subtree while the previous pass waited for it.
_MAX_CASCADE_PASSES = 64


async def _revoke_subtree(db: AsyncSession, root_id: uuid.UUID) -> list:
    """Revoke every active key below ``root_id``, repeating until a pass finds none.

    One pass is not enough (review R-B45-D-4). A pass that has to wait for a
    descendant's row -- a mint holding it FOR SHARE -- resumes with the
    snapshot it started with, so the key that mint commits is invisible to
    it. Under READ COMMITTED each new statement takes a new snapshot, so the
    next pass sees it. Mints that reach the subtree later wait on the rows
    this transaction holds and then read them revoked.
    """
    revoked: list = []
    for _ in range(_MAX_CASCADE_PASSES):
        batch = (await db.execute(_REVOKE_DESCENDANTS, {"root": root_id})).all()
        if not batch:
            return revoked
        revoked.extend(batch)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Keys are still being minted under this key; retry the revocation",
    )


# Every active descendant of :root, at any depth and whoever owns it. The
# walk passes THROUGH inactive keys: a key revoked some other way may still
# have minted active ones. UNION (not UNION ALL) stops at a repeated id.
_REVOKE_DESCENDANTS = text(
    """
    WITH RECURSIVE descendants(id) AS (
        SELECT id FROM api_keys WHERE minted_by_key_id = :root
      UNION
        SELECT child.id
        FROM api_keys AS child
        JOIN descendants AS parent ON child.minted_by_key_id = parent.id
    )
    UPDATE api_keys AS k
    SET is_active = false
    FROM descendants AS d
    WHERE k.id = d.id AND k.is_active IS TRUE
    RETURNING k.id, k.key_hash, k.project_id, k.name, k.key_hint
    """
).bindparams(bindparam("root", type_=PG_UUID(as_uuid=True)))
