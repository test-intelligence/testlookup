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
    require_release_access,
    require_role,
    resolve_project_scope,
)
from app.models.postgres import Release, User, UserRole
from app.models.schemas import (
    CompliancePackLifecycleResponse,
    RetireCompliancePackRequest,
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
    _: User = Depends(require_release_access()),
):
    """Generate a new compliance pack for a release. QA_LEAD+ only."""
    release = (
        await db.execute(select(Release).where(Release.id == release_id))
    ).scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")

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
    _: User = Depends(require_release_access()),
):
    """List every historical pack ever generated for a release."""
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


@router.post(
    "/api/v1/compliance-packs/{pack_id}/retire",
    response_model=CompliancePackLifecycleResponse,
)
async def retire_compliance_pack(
    pack_id: uuid.UUID,
    body: RetireCompliancePackRequest,
    # N20: a project-bound key may call this; resolve_project_scope on the
    # pack's project below refuses it any other project's pack.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Bring a pack's retention window forward to now (S4).

    Does not delete. The nightly purge already deletes packs past their
    window, using an object-then-row ordering that makes a failed object
    delete retryable; duplicating that here would mean two implementations of
    the one sequence that must not be got wrong.

    ADMIN, typed confirmation, and a reason — a pack is audit evidence
    generated with a seven-year default, so shortening that is a deliberate
    act and is recorded as one.
    """
    from app.services import compliance_pack_lifecycle as lifecycle

    pack = await lifecycle.get_pack_for_write(db, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Compliance pack not found")

    await resolve_project_scope(db, current_user, str(pack.project_id))

    if body.confirmation_id != str(pack.id):
        raise HTTPException(
            status_code=422,
            detail="confirmation_id must match the pack id exactly",
        )

    stamped = lifecycle.retire_early(pack, reason=body.reason)
    await db.commit()

    logger.info(
        "compliance_pack_retired",
        pack_id=str(pack_id),
        actor=str(current_user.id),
        reason=body.reason[:500],
    )
    return CompliancePackLifecycleResponse(
        pack_id=pack.id,
        retention_expires_at=stamped,
        purgeable_now=True,
    )


@router.delete("/api/v1/compliance-packs/{pack_id}", status_code=204)
async def delete_compliance_pack(
    pack_id: uuid.UUID,
    # N20: a project-bound key may call this; resolve_project_scope on the
    # pack's project below refuses it any other project's pack.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a pack whose retention window has already passed.

    **Refused while the window is still open** — 409, naming the expiry. Retire
    it first if it really should go now. That two-step exists because the
    window was previously enforced only on the way out: nothing could shorten
    it, and with no delete route nothing tested it on the way in either.

    S9 adds the legal-hold check alongside this. It is deliberately absent
    rather than stubbed: a hold check against a table that does not exist
    reads as a working guard while never firing.
    """
    from app.db.storage import get_storage_provider
    from app.services import compliance_pack_lifecycle as lifecycle

    pack = await lifecycle.get_pack_for_write(db, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Compliance pack not found")

    await resolve_project_scope(db, current_user, str(pack.project_id))

    blockers = lifecycle.deletion_blockers(pack)
    if blockers:
        raise HTTPException(status_code=409, detail={"blockers": blockers})

    # Object first, then the row — the same ordering the nightly purge uses, so
    # a failed object delete leaves the row for the next sweep rather than
    # orphaning the ZIP with nothing pointing at it.
    storage = get_storage_provider()
    await storage.delete_object(pack.minio_key, bucket="compliance-packs")

    await db.delete(pack)
    await db.commit()

    logger.info(
        "compliance_pack_deleted", pack_id=str(pack_id), actor=str(current_user.id)
    )
    return None
