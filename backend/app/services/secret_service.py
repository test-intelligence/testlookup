"""
Secret Service — manages sensitive values in secret_refs table.

Secrets are encrypted at rest using Fernet symmetric encryption derived from
APP_SECRET_KEY.  The encryption key is derived via PBKDF2-HMAC-SHA256 so the
raw APP_SECRET_KEY is never used directly as an AES key.

Provides:
  - store_secret: encrypt + save, return masked value
  - read_secret: retrieve + decrypt for runtime use
  - mask_value: generate a display-safe masked string
  - is_secret_field: identify which config fields are secrets

Future providers (Vault, AWS SM) can be added by extending the read path
based on SecretRef.provider.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import SecretRef

logger = logging.getLogger("services.secret")

# ── Encryption helpers ───────────────────────────────────────────────────────

_fernet_instance: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """
    Derive a Fernet key from APP_SECRET_KEY using PBKDF2.
    Cached after first call.
    """
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    from app.core.config import settings
    key_material = settings.APP_SECRET_KEY.encode("utf-8")
    # PBKDF2 with a fixed salt — deterministic so the same key always produces
    # the same Fernet key.  The salt is not secret; it just prevents rainbow tables.
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        key_material,
        salt=b"testlookup-secret-refs-v1",
        iterations=100_000,
        dklen=32,
    )
    fernet_key = base64.urlsafe_b64encode(derived)
    _fernet_instance = Fernet(fernet_key)
    return _fernet_instance


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> Optional[str]:
    """Decrypt a ciphertext string. Returns None if decryption fails."""
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception) as exc:
        logger.warning("Failed to decrypt secret value: %s", type(exc).__name__)
        return None


# ── Secret field registry ────────────────────────────────────────────────────

SECRET_FIELDS: dict[str, set[str]] = {
    "smtp_config": {"password"},
    "ai_config": {"openai_api_key", "google_api_key"},
    "integrations_config": {
        "jira_api_token", "splunk_api_token", "ocp_sa_token",
        "slack_bot_token", "github_token",
    },
}


def mask_value(raw: str) -> str:
    """Generate a display-safe masked string: first 4 chars + '...' + last 3 chars."""
    if not raw or len(raw) < 8:
        return "****"
    return f"{raw[:4]}...{raw[-3:]}"


def is_secret_field(scope: str, field_name: str) -> bool:
    """Check if a field in a given scope is a secret."""
    return field_name in SECRET_FIELDS.get(scope, set())


# ── Store / read operations ──────────────────────────────────────────────────

async def store_secret(
    db: AsyncSession,
    scope: str,
    key_name: str,
    raw_value: str,
    actor_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Encrypt and store a secret. Returns the masked value for display.
    Upserts: if a secret_ref for (scope, key_name) exists, updates it.
    """
    masked = mask_value(raw_value)
    ciphertext = encrypt_value(raw_value)

    result = await db.execute(
        select(SecretRef).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = ciphertext
        existing.masked_value = masked
        existing.updated_by = actor_id
        existing.rotation_status = "active"
    else:
        db.add(SecretRef(
            scope=scope,
            provider="db",
            key_name=key_name,
            encrypted_value=ciphertext,
            masked_value=masked,
            updated_by=actor_id,
        ))

    return masked


async def read_secret(
    db: AsyncSession,
    scope: str,
    key_name: str,
) -> Optional[str]:
    """Read and decrypt a secret for runtime use. Returns None if not found or decryption fails."""
    result = await db.execute(
        select(SecretRef).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
            SecretRef.rotation_status != "expired",
        )
    )
    ref = result.scalar_one_or_none()
    if not ref or not ref.encrypted_value:
        return None

    # Try decrypting (new encrypted format)
    decrypted = decrypt_value(ref.encrypted_value)
    if decrypted is not None:
        return decrypted

    # Fallback: if decryption fails, the value might be from before encryption
    # was implemented (legacy plaintext). Return it as-is but log a warning.
    logger.warning(
        "Secret %s/%s appears to be stored in plaintext — will be re-encrypted on next write",
        scope, key_name,
    )
    return ref.encrypted_value


async def get_masked(
    db: AsyncSession,
    scope: str,
    key_name: str,
) -> Optional[str]:
    """Get the masked display value. Returns None if no secret stored."""
    result = await db.execute(
        select(SecretRef.masked_value).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
        )
    )
    return result.scalar_one_or_none()


async def has_secret(
    db: AsyncSession,
    scope: str,
    key_name: str,
) -> bool:
    """Check if a secret exists for a given scope and key."""
    result = await db.execute(
        select(SecretRef.id).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
        )
    )
    return result.scalar_one_or_none() is not None


def extract_secrets_from_config(scope: str, config: dict) -> dict[str, str]:
    """Extract secret fields from a config dict. Returns {field_name: value}."""
    secret_fields = SECRET_FIELDS.get(scope, set())
    secrets = {}
    for field in secret_fields:
        if field in config and config[field]:
            secrets[field] = config[field]
    return secrets


def strip_secrets_from_config(scope: str, config: dict) -> dict:
    """Remove secret values from a config dict, leaving non-secret metadata."""
    secret_fields = SECRET_FIELDS.get(scope, set())
    return {k: v for k, v in config.items() if k not in secret_fields}
