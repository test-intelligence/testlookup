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

* ``GET  /api/v1/compliance-packs?project_id=&limit=`` (W05 REL-04)
  The newest packs of one project, or of every project the caller can see,
  for the releases page's side rail.

W11 REL-11 (D14) -- packs are signed with the deployment's Ed25519 key:

* ``GET  /api/v1/compliance-packs/signing-keys`` -- every key's PUBLIC half;
  never creates a key.
* ``GET  /api/v1/compliance-packs/signing-keys/{signing_key_id}/pem`` -- one
  public key as a PEM download.
* ``POST /api/v1/compliance-packs/signing-keys/rotate`` -- ADMIN; retires the
  active key (its private key destroyed, its public key kept) and creates one.
* ``POST /api/v1/compliance-packs/{pack_id}/verify`` -- checks the stored ZIP
  against its manifest, the recorded digest and the signature; writes nothing.

The static ``signing-keys`` paths are declared before every ``{pack_id}``
route.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_active_user,
    get_db,
    require_release_access,
    require_role,
    resolve_project_scope,
)
from app.db.postgres import release_read_connection
from app.models.postgres import Release, User, UserRole
from app.models.schemas import (
    CompliancePackKeyRotateRequest,
    CompliancePackLifecycleResponse,
    CompliancePackListRead,
    CompliancePackSigningKeyRead,
    CompliancePackSigningKeysResponse,
    CompliancePackSummaryRead,
    CompliancePackVerification,
    RetireCompliancePackRequest,
    CompliancePackGenerateRequest,
    CompliancePackRead,
)
from app.services import compliance_pack_service as svc
from app.services import compliance_pack_verify, compliance_signing

router = APIRouter(tags=["Compliance Pack"])
logger = structlog.get_logger("routers.compliance_packs")

#: A signing key id as ``compliance_signing.key_id_for`` builds it. Checked
#: before any statement: anything else is simply not a key (404).
_SIGNING_KEY_ID_RE = re.compile(r"^k-[0-9a-f]{16}$")
SIGNING_KEY_NOT_FOUND = "Signing key not found"
PACK_NOT_FOUND = "Compliance pack not found"


def _key_read(row, *, active_key_id: Optional[str]) -> CompliancePackSigningKeyRead:
    return CompliancePackSigningKeyRead(
        key_id=row.key_id,
        algorithm=row.algorithm,
        public_key_pem=row.public_key_pem,
        created_at=row.created_at,
        retired_at=row.retired_at,
        active=row.key_id == active_key_id,
    )


# ── Signing keys (W11 REL-11) — static paths, before every {pack_id} route ──


@router.get(
    "/api/v1/compliance-packs/signing-keys",
    response_model=CompliancePackSigningKeysResponse,
)
async def list_compliance_signing_keys(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Every signing key's PUBLIC half, newest first, retired ones included
    (they verify the packs they signed). Deployment-wide public data.

    A GET never writes: no key is created here. ``active_key_id`` is ``None``
    until the first signed pack creates the deployment's key.
    """
    rows = await compliance_signing.list_keys(db)
    active = next((r.key_id for r in rows if r.retired_at is None), None)
    return CompliancePackSigningKeysResponse(
        active_key_id=active,
        keys=[_key_read(r, active_key_id=active) for r in rows],
    )


@router.get("/api/v1/compliance-packs/signing-keys/{signing_key_id}/pem")
async def download_compliance_signing_key(
    # Not ``key_id``: that name is the API-key id in the authorization
    # ratchet's SCOPED_IDS, and a public, deployment-wide signing key is not a
    # tenant object.
    signing_key_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """One public key as ``testlookup-compliance-<id>.pem`` (never a private key)."""
    if not _SIGNING_KEY_ID_RE.match(signing_key_id):
        raise HTTPException(status_code=404, detail=SIGNING_KEY_NOT_FOUND)
    row = await compliance_signing.get_key(db, signing_key_id)
    if row is None:
        raise HTTPException(status_code=404, detail=SIGNING_KEY_NOT_FOUND)
    return Response(
        content=row.public_key_pem,
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": f'attachment; filename="testlookup-compliance-{row.key_id}.pem"',
            "Cache-Control": "no-store",
        },
    )


# activity: none - deployment-wide signing key, no project; audited in settings_audit_log
@router.post(
    "/api/v1/compliance-packs/signing-keys/rotate",
    response_model=CompliancePackSigningKeyRead,
)
async def rotate_compliance_signing_key(
    body: CompliancePackKeyRotateRequest,
    # ADMIN, and NOT ``allow_project_key``: a deployment-wide key, refused to a
    # project-bound API key by default.
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Retire the active signing key and create a new one (D14, D-13).

    The retired key's private half is destroyed (its ciphertext cleared): it
    never signs again. Its public key stays, so every pack it signed keeps
    verifying. Typed confirmation, and a reason kept on the retired key.
    """
    if body.confirmation != compliance_signing.ROTATE_CONFIRMATION:
        raise HTTPException(status_code=422, detail=compliance_signing.ROTATE_CONFIRMATION_SENTENCE)
    key = await compliance_signing.rotate(db, actor=current_user, reason=body.reason)
    await db.commit()
    row = await compliance_signing.get_key(db, key.key_id)
    logger.info("compliance_signing_key_rotated_by", key_id=key.key_id, actor=str(current_user.id))
    return _key_read(row, active_key_id=key.key_id)


# ── Project-wide list (W05 REL-04) ──────────────────────────────────────────


@router.get("/api/v1/compliance-packs", response_model=CompliancePackListRead)
async def list_project_compliance_packs(
    # A QUERY id, so the authorization ratchet (which reads path params) cannot
    # see it: the handler scopes it itself with ``resolve_project_scope`` --
    # 400 for a malformed id, 403 for a project the caller is not in.
    project_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The newest packs in one project, or across every project the caller can
    see, and how many there are (``total`` is counted, not ``len(items)``).

    Not flag-gated, like the per-release list: generating a pack is what
    ``release_compliance_pack`` controls. A project-bound API key is confined
    to its project by ``get_accessible_project_ids``.
    """
    scoped, allowed = await resolve_project_scope(db, current_user, project_id)
    if scoped is not None:
        project_ids: Optional[set[uuid.UUID]] = {scoped}
    else:
        # ``None`` = an ADMIN naming no project: every active project. An empty
        # set (a user in no project) answers without a query.
        project_ids = allowed
    items, total = await svc.list_packs_for_projects(db, project_ids, limit=limit)
    return CompliancePackListRead(
        items=[CompliancePackSummaryRead(**item) for item in items],
        total=total,
    )


# ── Generate ───────────────────────────────────────────────────────────────


@router.post(
    "/api/v1/releases/{release_id}/compliance-pack",
    response_model=CompliancePackRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate_compliance_pack(
    release_id: uuid.UUID,
    payload: CompliancePackGenerateRequest,
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
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


def _count_verification(result: str, signature: str) -> None:
    try:
        from app.core.metrics import compliance_pack_verifications_total

        compliance_pack_verifications_total.labels(result=result, signature=signature).inc()
    except Exception:  # noqa: BLE001 - telemetry never breaks a verification
        pass


# activity: none - verification reads the stored pack and writes nothing
@router.post(
    "/api/v1/compliance-packs/{pack_id}/verify",
    response_model=CompliancePackVerification,
)
async def verify_compliance_pack(
    pack_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Check a stored pack: every file against the manifest, the manifest
    against the digest recorded at generation, and the signature (W11 REL-11).

    Whoever may download the pack may verify it (the download route's rule:
    the pack's own project through ``resolve_project_scope``). A POST that
    writes nothing. The request's connection is handed back before the storage
    read; a storage failure answers 200 ``unreadable`` (absence is not health,
    and not a 502 toast either).
    """
    pack = await svc.get_pack(db, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=PACK_NOT_FOUND)

    await resolve_project_scope(db, current_user, str(pack.project_id))

    keys = await compliance_signing.public_keys(db)
    recorded = pack.manifest_sha256
    record_signature = getattr(pack, "signature", None)
    record_key_id = getattr(pack, "signing_key_id", None)
    await release_read_connection(db)

    try:
        data = await svc.load_pack_bytes(pack)
    except Exception as exc:  # noqa: BLE001 - any storage fault reads "unreadable"
        logger.warning(
            "compliance_pack_verify_unreadable", pack_id=str(pack_id), error_type=type(exc).__name__,
        )
        result = compliance_pack_verify.unreadable_result(pack_id=pack_id, signing_key_id=record_key_id)
    else:
        result = await asyncio.to_thread(
            compliance_pack_verify.verify_pack_bytes,
            data,
            recorded_manifest_sha256=recorded,
            public_keys=keys,
            pack_id=pack_id,
            record_signature=record_signature,
            record_signing_key_id=record_key_id,
        )
    _count_verification(str(result["result"]), str(result["signature"]))
    logger.info(
        "compliance_pack_verified",
        pack_id=str(pack_id),
        result=result["result"],
        signature=result["signature"],
        signing_key_id=result.get("signing_key_id"),
    )
    return CompliancePackVerification(**result)


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
