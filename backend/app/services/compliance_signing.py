"""The deployment's Ed25519 compliance-pack signing key (W11 REL-11, D14).

A new compliance pack is signed: ``signature.json`` holds an Ed25519 signature
over ``SIGNED_STATEMENT`` -- the pack id, the SHA-256 of its ``manifest.json``
and the key id -- made with the deployment's own key. Verifying it needs only
the PUBLIC key, which ``GET /api/v1/compliance-packs/signing-keys`` serves.

Custody:

* The key pair is generated in the app, on the FIRST signed pack
  (``active_signing_key(create=True)``) -- never by the migration (it cannot
  reach ``APP_SECRET_KEY``) and never by a GET.
* The PUBLIC key (SubjectPublicKeyInfo PEM) lives in ``compliance_signing_keys``
  for ever, so a pack signed before a rotation keeps verifying.
* The PRIVATE key lives only in ``secret_refs`` (scope ``SIGNING_SECRET_SCOPE``,
  key_name = the key id), Fernet-encrypted by ``secret_service`` under
  ``APP_SECRET_KEY``. It is never logged, never in an exception message, a
  response, an activity row, the pack or a ``repr`` (``SigningKey`` hides it).
* ``rotate`` (ADMIN, typed confirmation) retires the active key, DESTROYS its
  private key (``expire_secret``: the ciphertext is cleared, a retired key
  never signs again) and keeps its public key.

What it does NOT defend against: the private key is stored encrypted beside
the data, so an actor holding both the database and ``APP_SECRET_KEY`` can
sign a forged pack. Keep the public key and the digests off-host (WORM /
object lock) for that.

Commit-free (the services-flush / routers-commit rule): every function stages
on the caller's session; the generate route's ``get_db`` or the rotate route
commits.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ComplianceSigningKey, SettingsAuditLog
from app.services import secret_service

logger = structlog.get_logger("services.compliance_signing")

SIGNING_ALGORITHM = "ed25519"
SIGNING_SECRET_SCOPE = "compliance_signing"         # secret_refs.scope; key_name = the key id
SIGNATURE_FILENAME = "signature.json"               # the ONE file a pack may carry that manifest.json does not list
#: The statement's first line, also written into ``signature.json``.
STATEMENT_LABEL = "testlookup-compliance-pack-v1"
#: Its UTF-8 bytes are what is signed.
SIGNED_STATEMENT = "testlookup-compliance-pack-v1\n{pack_id}\n{manifest_sha256}\n{key_id}\n"
ROTATE_CONFIRMATION = "rotate"
ROTATE_CONFIRMATION_SENTENCE = "Type rotate to confirm."
#: ``settings_audit_log.setting_key`` of a rotation (field names only, never a value).
AUDIT_SETTING_KEY = "compliance_signing_key"
#: ``hashtext()`` input of the advisory lock that serialises key creation.
_LOCK_NAME = "compliance_signing_key"


@dataclass(frozen=True, slots=True)
class SigningKey:
    """A usable signing key. The private half never appears in ``repr``."""

    key_id: str
    public_key_pem: str
    private_key: Ed25519PrivateKey = field(repr=False, compare=False)


# ── Pure helpers (the CLI's offline verifier restates key_id_for / statement) ──


def key_id_for(public_key: Ed25519PublicKey) -> str:
    """``"k-"`` + the first 16 hex of SHA-256 over the raw 32-byte public key."""
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "k-" + hashlib.sha256(raw).hexdigest()[:16]


def public_key_pem(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def statement_bytes(*, pack_id: uuid.UUID | str, manifest_sha256: str, key_id: str) -> bytes:
    return SIGNED_STATEMENT.format(
        pack_id=str(pack_id), manifest_sha256=manifest_sha256, key_id=key_id,
    ).encode("utf-8")


def sign_statement(key: SigningKey, *, pack_id: uuid.UUID, manifest_sha256: str) -> str:
    """The base64 Ed25519 signature over ``SIGNED_STATEMENT``."""
    message = statement_bytes(pack_id=pack_id, manifest_sha256=manifest_sha256, key_id=key.key_id)
    return base64.b64encode(key.private_key.sign(message)).decode("ascii")


def signature_document(
    key: SigningKey, *, pack_id: uuid.UUID, manifest_sha256: str, signed_at: datetime,
) -> dict[str, Any]:
    """The body of ``signature.json`` (the caller serialises it like every pack file)."""
    return {
        "format_version": 1,
        "algorithm": SIGNING_ALGORITHM,
        "statement": STATEMENT_LABEL,
        "pack_id": str(pack_id),
        "manifest_sha256": manifest_sha256,
        "key_id": key.key_id,
        "signed_at": signed_at.isoformat(),
        "signature": sign_statement(key, pack_id=pack_id, manifest_sha256=manifest_sha256),
    }


# ── The key on the session ───────────────────────────────────────────────────


def _dialect_name(db: AsyncSession) -> str:
    try:
        return str(db.get_bind().dialect.name)
    except Exception:  # noqa: BLE001 - a session double has no real bind
        return ""


async def _lock(db: AsyncSession) -> None:
    """Serialise key creation and rotation (Postgres; other dialects skip it).

    Transaction-scoped: released by the request's commit or rollback. The
    partial unique index ``uq_compliance_signing_keys_active`` backstops a
    dialect without the lock.
    """
    if _dialect_name(db) != "postgresql":
        return
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": _LOCK_NAME})


async def _active_row(db: AsyncSession) -> Optional[ComplianceSigningKey]:
    return (
        await db.execute(
            select(ComplianceSigningKey).where(
                ComplianceSigningKey.algorithm == SIGNING_ALGORITHM,
                ComplianceSigningKey.retired_at.is_(None),
            )
        )
    ).scalars().first()


async def _load(db: AsyncSession, row: ComplianceSigningKey) -> Optional[SigningKey]:
    """The row's private key from ``secret_refs``; ``None`` when it cannot be used.

    ``read_secret`` answers ``None`` for a missing, expired or undecryptable
    secret (an ``APP_SECRET_KEY`` changed without ``APP_SECRET_KEY_PREVIOUS``).
    Nothing about the key material is logged: the key id and the error's type
    only.
    """
    try:
        pem = await secret_service.read_secret(db, SIGNING_SECRET_SCOPE, row.key_id)
    except Exception as exc:  # noqa: BLE001 - a key-custody fault signs nothing; the caller counts it
        logger.warning("compliance_signing_key_unreadable", key_id=row.key_id, error_type=type(exc).__name__)
        return None
    if not pem:
        logger.warning("compliance_signing_key_unreadable", key_id=row.key_id, error_type="secret_missing")
        return None
    try:
        private = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    except Exception as exc:  # noqa: BLE001 - never echo the parser's message: it may quote the input
        logger.warning("compliance_signing_key_unreadable", key_id=row.key_id, error_type=type(exc).__name__)
        return None
    if not isinstance(private, Ed25519PrivateKey) or key_id_for(private.public_key()) != row.key_id:
        logger.warning("compliance_signing_key_unreadable", key_id=row.key_id, error_type="key_mismatch")
        return None
    return SigningKey(key_id=row.key_id, public_key_pem=row.public_key_pem, private_key=private)


async def _create(db: AsyncSession, *, actor: Any) -> Optional[SigningKey]:
    """Generate a key pair, store the private half encrypted, add the public row.

    Inside a SAVEPOINT: a lost race on the partial unique index (a dialect
    without the advisory lock) rolls back only this part, and the winner's key
    is used instead.
    """
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    key_id = key_id_for(public)
    pem_public = public_key_pem(public)
    # PKCS8 without a passphrase: secret_service encrypts it at rest.
    pem_private = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    actor_id = getattr(actor, "id", None)
    try:
        async with db.begin_nested():
            await secret_service.store_secret(db, SIGNING_SECRET_SCOPE, key_id, pem_private, actor_id)
            db.add(ComplianceSigningKey(
                key_id=key_id,
                algorithm=SIGNING_ALGORITHM,
                public_key_pem=pem_public,
                created_at=datetime.now(timezone.utc),
                created_by_user_id=actor_id,
            ))
            await db.flush()
    except IntegrityError:
        winner = await _active_row(db)
        return await _load(db, winner) if winner is not None else None
    logger.info("compliance_signing_key_created", key_id=key_id)
    return SigningKey(key_id=key_id, public_key_pem=pem_public, private_key=private)


async def active_signing_key(db: AsyncSession, *, actor: Any, create: bool) -> Optional[SigningKey]:
    """The active key, or ``None`` (no key and ``create`` False, or unusable).

    Double-checked: read the active row without a lock (the common case once a
    key exists); with none and ``create``, take the advisory lock, read again,
    and only if there is still none generate one. ``create=False`` never
    writes (a GET must not bootstrap a key).
    """
    row = await _active_row(db)
    if row is None:
        if not create:
            return None
        await _lock(db)
        row = await _active_row(db)
        if row is None:
            return await _create(db, actor=actor)
    return await _load(db, row)


async def public_keys(db: AsyncSession) -> dict[str, str]:
    """Every key id -> its public PEM, retired keys included (they verify old packs)."""
    rows = (
        await db.execute(select(ComplianceSigningKey.key_id, ComplianceSigningKey.public_key_pem))
    ).all()
    return {str(key_id): str(pem) for key_id, pem in rows}


async def list_keys(db: AsyncSession) -> list[ComplianceSigningKey]:
    """Every key row, newest first (the signing-keys read)."""
    return list(
        (
            await db.execute(
                select(ComplianceSigningKey).order_by(
                    ComplianceSigningKey.created_at.desc(), ComplianceSigningKey.key_id,
                )
            )
        ).scalars().all()
    )


async def get_key(db: AsyncSession, key_id: str) -> Optional[ComplianceSigningKey]:
    return (
        await db.execute(select(ComplianceSigningKey).where(ComplianceSigningKey.key_id == key_id))
    ).scalar_one_or_none()


async def rotate(db: AsyncSession, *, actor: Any, reason: str) -> SigningKey:
    """Retire the active key (destroying its private half) and create a new one.

    Under the advisory lock. The retired row keeps its public key, gains
    ``retired_at`` / ``retired_by_user_id`` / ``retired_reason``; its secret is
    expired (ciphertext cleared). One ``settings_audit_log`` row records the
    rotation by field name only. Stage-only: the router commits.
    """
    await _lock(db)
    current = await _active_row(db)
    actor_id = getattr(actor, "id", None)
    retired_id: Optional[str] = None
    if current is not None:
        current.retired_at = datetime.now(timezone.utc)
        current.retired_by_user_id = actor_id
        current.retired_reason = reason
        await secret_service.expire_secret(db, SIGNING_SECRET_SCOPE, current.key_id)
        retired_id = current.key_id
        # The retirement reaches the partial unique index before the new row.
        await db.flush()
    created = await _create(db, actor=actor)
    if created is None:  # pragma: no cover - only a lost race whose winner is unreadable
        raise RuntimeError("compliance signing key could not be created")
    db.add(SettingsAuditLog(
        setting_key=AUDIT_SETTING_KEY,
        action="secret_rotated",
        actor_id=actor_id,
        actor_name=(getattr(actor, "username", None) or getattr(actor, "email", None)) if actor else None,
        changed_fields=["key_id"],
    ))
    logger.info("compliance_signing_key_rotated", retired_key_id=retired_id, key_id=created.key_id)
    return created
