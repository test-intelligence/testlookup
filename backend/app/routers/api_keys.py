"""API Key management — generate, list, revoke scoped PATs."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import ApiKey, User, UserRole
from app.models.schemas import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse

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
    """Generate a new scoped API key. The raw key is only shown once."""
    raw_key = f"qai_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    key_hint = raw_key[:8] + "..."

    expires_at = None
    if payload.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_days)

    api_key = ApiKey(
        user_id=current_user.id,
        name=payload.name,
        key_hash=key_hash,
        key_hint=key_hint,
        scopes=payload.scopes,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return ApiKeyCreatedResponse(
        **_build_api_key_response(api_key).model_dump(),
        raw_key=raw_key,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """List all active API keys for the current user."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id, ApiKey.is_active == True)  # noqa: E712
        .order_by(ApiKey.created_at.desc())
    )
    return [_build_api_key_response(api_key) for api_key in result.scalars().all()]


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_ENGINEER)),
):
    """Revoke (soft-delete) an API key. Only the owner can revoke their own keys."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    api_key.is_active = False
    await db.commit()
    return None
