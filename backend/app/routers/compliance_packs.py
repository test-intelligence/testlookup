"""
Release compliance pack API — Tier 1 item 4.

Three endpoints:

* ``POST /api/v1/releases/{release_id}/compliance-pack``
  Generate a new pack. QA_LEAD+ or higher. Gated by the
  ``release_compliance_pack`` feature flag (503 otherwise).

* ``GET  /api/v1/releases/{release_id}/compliance-packs``
  List every historical pack for a release so reviewers can re-download
  an older version.

* ``GET  /api/v1/compliance-packs/{pack_id}/download``
  Stream the ZIP bytes back. We serve it via StreamingResponse rather
  than a presigned S3 URL so the same endpoint works with the local
  storage provider, MinIO, and a cloud S3 bucket.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import Release, User, UserRole
from app.models.schemas import (
    CompliancePackGenerateRequest,
    CompliancePackRead,
)
from app.services import compliance_pack_service as svc

router = APIRouter(tags=["Compliance Pack"])
logger = structlog.get_logger("routers.compliance_packs")


# ── Generate ───────────────────────────────────────────────────────────────


@router.post(
    "/api/v1/releases/{release_id}/compliance-pack",
    response_model=CompliancePackRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate_compliance_pack(
    release_id: uuid.UUID,
    payload: CompliancePackGenerateRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new compliance pack for a release. QA_LEAD+ only."""
    release = (
        await db.execute(select(Release).where(Release.id == release_id))
    ).scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

    await resolve_project_scope(db, current_user, str(release.project_id))

    try:
        pack = await svc.generate_pack(
            db,
            release,
            actor=current_user,
            notes=payload.notes,
            retention_days=payload.retention_days,
        )
    except svc.CompliancePackDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except svc.CompliancePackNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return pack


# ── List ──────────────────────────────────────────────────────────────────


@router.get(
    "/api/v1/releases/{release_id}/compliance-packs",
    response_model=list[CompliancePackRead],
)
async def list_compliance_packs(
    release_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List every historical pack ever generated for a release."""
    release = (
        await db.execute(select(Release).where(Release.id == release_id))
    ).scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    await resolve_project_scope(db, current_user, str(release.project_id))
    return await svc.list_packs_for_release(db, release_id)


# ── Download ──────────────────────────────────────────────────────────────


@router.get("/api/v1/compliance-packs/{pack_id}/download")
async def download_compliance_pack(
    pack_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream a pack ZIP back to the caller.

    Enforces tenant isolation via the pack's ``project_id``. The response
    includes the SHA-256 of the manifest in the ``X-Manifest-Sha256``
    header so automated verifiers can assert before reading the body.
    """
    pack = await svc.get_pack(db, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Compliance pack not found")

    await resolve_project_scope(db, current_user, str(pack.project_id))

    try:
        data = await svc.load_pack_bytes(pack)
    except Exception as exc:
        logger.error("compliance pack load failed", pack_id=str(pack_id), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to load compliance pack from object storage",
        )

    filename = f"testlookup-compliance-{pack.release_id}-{pack.generated_at.strftime('%Y%m%d')}.zip"
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
            "X-Manifest-Sha256": pack.manifest_sha256,
            "X-Pack-Id": str(pack.id),
        },
    )
