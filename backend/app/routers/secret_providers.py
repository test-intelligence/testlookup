"""W20 ADM-10: the deployment's external secret providers, for the admin's Data & storage card.

``GET /api/v1/settings/secret-providers`` (ADMIN): each provider's configuration and health — for Vault the
host (never a token), the auth method, whether login works — and how many stored secrets are references to it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.db.postgres import get_db
from app.models.postgres import SecretRef, User, UserRole
from app.services.secret_providers import vault

router = APIRouter(prefix="/api/v1/settings", tags=["Secret providers"])


@router.get("/secret-providers")
async def get_secret_providers(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    references = (await db.execute(
        select(func.count()).select_from(SecretRef).where(SecretRef.provider == "vault", SecretRef.rotation_status != "expired")
    )).scalar() or 0
    status = await vault.health()
    return {"providers": [{**status, "references": int(references)}], "reference_syntax": "vault:<path>#<key>"}
