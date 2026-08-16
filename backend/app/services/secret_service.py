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

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import SecretRef

logger = logging.getLogger("services.secret")

# ── Encryption helpers ───────────────────────────────────────────────────────

# Minimum APP_SECRET_KEY length we consider non-trivial. Enforced fail-closed
# in production/staging; warned-about elsewhere so dev stays bootable.
_MIN_KEY_LENGTH = 16
_DEFAULT_KEY_SENTINEL = "change-me-in-production"

_multifernet_instance: Optional[MultiFernet] = None


def _derive_fernet(secret: str) -> Fernet:
    """Derive a Fernet key from a secret via PBKDF2 (fixed, non-secret salt so
    the same secret always yields the same key)."""
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt=b"testlookup-secret-refs-v1",
        iterations=100_000,
        dklen=32,
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def _get_fernet() -> MultiFernet:
    """
    Build a MultiFernet for secret-at-rest crypto, derived from APP_SECRET_KEY
    (and APP_SECRET_KEY_PREVIOUS for rotation). Cached after first call.

    Fails CLOSED on a missing/weak key so we never silently "encrypt" with a
    trivial key that offers no real protection:
      - empty key            → RuntimeError everywhere.
      - default/short key in prod/staging → RuntimeError.
      - default/short key in dev → WARN only (keeps `make dev` bootable).

    Encryption always uses the first (current) key; decryption tries current
    then previous, so values written under an old key keep decrypting through
    a rotation.
    """
    global _multifernet_instance
    if _multifernet_instance is not None:
        return _multifernet_instance

    from app.core.config import settings

    key = settings.APP_SECRET_KEY or ""
    is_prod_like = settings.APP_ENV in ("production", "staging")

    if not key:
        raise RuntimeError(
            "APP_SECRET_KEY is empty — refusing to encrypt/decrypt secrets with no key."
        )
    weak = key == _DEFAULT_KEY_SENTINEL or len(key) < _MIN_KEY_LENGTH
    if weak:
        if is_prod_like:
            raise RuntimeError(
                "APP_SECRET_KEY is the default or too short for production — "
                "set a strong random value (>= 16 chars) before storing secrets."
            )
        logger.warning(
            "APP_SECRET_KEY is weak/default — secrets are NOT meaningfully protected. "
            "Set a strong random value for any non-dev use."
        )

    fernets = [_derive_fernet(key)]
    previous = getattr(settings, "APP_SECRET_KEY_PREVIOUS", None)
    if previous:
        fernets.append(_derive_fernet(previous))

    _multifernet_instance = MultiFernet(fernets)
    return _multifernet_instance


def reset_fernet_cache() -> None:
    """Clear the cached MultiFernet (call after rotating APP_SECRET_KEY in-process)."""
    global _multifernet_instance
    _multifernet_instance = None


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> Optional[str]:
    """Decrypt a ciphertext string. Returns None only when the ciphertext is
    not decryptable under any current/previous key (tampered, or a non-Fernet
    value). Config/programming errors (e.g. a missing key) propagate so they
    fail loud instead of being mistaken for a legacy plaintext value."""
    try:
        f = _get_fernet()
    except RuntimeError:
        # Misconfiguration — let it surface rather than silently fall back.
        raise
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, TypeError, ValueError) as exc:
        logger.warning("Failed to decrypt secret value: %s", type(exc).__name__)
        return None


# ── Secret field registry ────────────────────────────────────────────────────

SECRET_FIELDS: dict[str, set[str]] = {
    "smtp_config": {"password"},
    "ai_config": {
        "openai_api_key", "google_api_key",
        "anthropic_api_key", "openrouter_api_key",
    },
    "integrations_config": {
        "jira_api_token", "splunk_api_token", "ocp_sa_token",
        "slack_bot_token", "github_token",
    },
}


def mask_value(raw: str) -> str:
    """Generate a display-safe masked string.

    Reveals at most the last 2 characters, and fully masks anything shorter
    than 16 chars, so a short/medium token (API keys, passwords) never leaks a
    usable prefix through the persisted ``masked_value``.
    """
    if not raw or len(raw) < 16:
        return "****"
    return f"****{raw[-2:]}"


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

    # Try decrypting (the only supported at-rest format).
    decrypted = decrypt_value(ref.encrypted_value)
    if decrypted is not None:
        return decrypted

    # Decrypt failed. Only return the stored value verbatim when the row is
    # explicitly flagged as legacy plaintext (provider == "plaintext"). For a
    # normal encrypted row a decrypt failure means tampered ciphertext or a key
    # rotated without re-encryption — returning the raw ciphertext would serve
    # an attacker-controlled / undecryptable value as "the secret", so fail to
    # None and force the operator to re-enter it.
    if ref.provider == "plaintext":
        logger.warning(
            "Secret %s/%s is flagged legacy plaintext — will be encrypted on next write",
            scope, key_name,
        )
        return ref.encrypted_value

    logger.error(
        "Secret %s/%s failed to decrypt (tampered or key-rotated without re-encryption) — returning None",
        scope, key_name,
    )
    return None


async def expire_secret(
    db: AsyncSession,
    scope: str,
    key_name: str,
) -> bool:
    """Destroy a stored secret. Returns True if a row was affected.

    There is no hard DELETE here on purpose: ``secret_refs`` rows are
    referenced by ``app_settings.secret_ref_id``, so removing one could orphan
    a settings row. Instead the ciphertext is cleared and ``rotation_status``
    is set to ``"expired"`` — the predicate that ``read_secret`` /
    ``has_secret`` / ``get_masked`` already filter on, so every reader agrees
    the secret is gone. Clearing ``encrypted_value`` matters as much as the
    status flag: a status-only tombstone would leave the recoverable plaintext
    of (for example) a revoked TOTP seed sitting in the database.

    Stage-only — the caller owns the commit.
    """
    result = await db.execute(
        select(SecretRef).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
        )
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        return False
    ref.encrypted_value = None
    ref.masked_value = None
    ref.rotation_status = "expired"
    return True


async def get_masked(
    db: AsyncSession,
    scope: str,
    key_name: str,
) -> Optional[str]:
    """Get the masked display value. Returns None if no resolvable secret stored.

    Mirrors ``read_secret``'s ``rotation_status != 'expired'`` predicate so the
    UI never shows "configured" for a secret the runtime would resolve to None.
    """
    result = await db.execute(
        select(SecretRef.masked_value).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
            SecretRef.rotation_status != "expired",
        )
    )
    return result.scalar_one_or_none()


async def has_secret(
    db: AsyncSession,
    scope: str,
    key_name: str,
) -> bool:
    """Check if a resolvable secret exists for a given scope and key.

    Excludes expired rows to stay consistent with ``read_secret``/``get_masked``.
    """
    result = await db.execute(
        select(SecretRef.id).where(
            SecretRef.scope == scope,
            SecretRef.key_name == key_name,
            SecretRef.rotation_status != "expired",
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
