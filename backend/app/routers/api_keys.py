"""API Key management — generate, list, revoke scoped PATs."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_api_key_owner, require_role
from app.db.postgres import get_db
from app.models.postgres import ApiKey, Project, User, UserRole
from app.models.schemas import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from app.services.activity.service import ActorRef, record as record_activity

router = APIRouter(prefix="/api/v1/keys", tags=["API Keys"])


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _normalize_scopes(scopes: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if scopes is None:
        return []
    if isinstance(scopes, str):
        return [scopes] if scopes else []
    return [str(scope) for scope in scopes if scope]


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
async def create_api_key(
    payload: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Generate a new scoped API key. The raw key is only shown once.

    ADMIN can supply ``project_id`` to restrict the key to a single project
    and ``target_user_id`` to create a key on behalf of another user.
    """
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
    raw_key = f"qai_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    key_hint = raw_key[:8] + "..."

    expires_at = None
    if payload.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_days)

    api_key = ApiKey(
        # Identity at construction: the ledger row is staged in this same
        # transaction and needs a stable entity_id without an extra flush.
        id=uuid.uuid4(),
        user_id=owner_id,
        name=payload.name,
        key_hash=key_hash,
        key_hint=key_hint,
        scopes=payload.scopes,
        project_id=payload.project_id,
        expires_at=expires_at,
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
    """
    stmt = select(ApiKey).where(ApiKey.is_active == True)  # noqa: E712

    is_admin = current_user.role == UserRole.ADMIN.value or current_user.role == UserRole.ADMIN
    if is_admin and project_id is not None:
        stmt = stmt.where(ApiKey.project_id == project_id)
    else:
        stmt = stmt.where(ApiKey.user_id == current_user.id)

    stmt = stmt.order_by(ApiKey.created_at.desc())
    result = await db.execute(stmt)
    return [_build_api_key_response(api_key) for api_key in result.scalars().all()]


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
    _: User = Depends(require_api_key_owner()),
):
    """Revoke (soft-delete) an API key. Only the owner can revoke their own keys."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    api_key.is_active = False

    if api_key.project_id is not None:
        await record_activity(
            db,
            project_id=api_key.project_id,
            event_type="api_key.revoked",
            actor=ActorRef.from_user(current_user),
            entity_id=api_key.id,
            entity_label=api_key.name,
            context={"key_hint": api_key.key_hint},
        )

    await db.commit()
    return None
