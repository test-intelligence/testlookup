"""
Multi-factor authentication (TOTP) + account lockout.

Design notes that are load-bearing — read before changing anything here.

**The second factor is never expressed as a claim on an access token.**
``core/deps.get_current_user`` trusts any JWT whose ``type`` is ``"access"``,
and ``bootstrap.register_routers`` mounts that dependency router-wide, so a
"half-authenticated" access token would be fully authenticated everywhere. The
interstitial credentials live in ``core/security.create_mfa_token`` with their
own ``type`` claims and are rejected at the decode layer.

**``users.mfa_enabled`` is the only authority on whether MFA applies.** The
TOTP seed lives in ``secret_refs`` (scope ``user_totp``) and
``secret_service.read_secret`` returns ``None`` both for "never stored" and for
"stored but will not decrypt" — after an APP_SECRET_KEY rotation that lost the
previous key, every enrolled seed reads back as absent. Treating that as "MFA
is off" would silently switch the control off for the entire workspace at the
exact moment the operator most needs it on. :func:`load_totp_secret` therefore
distinguishes the two cases and the login path turns "enrolled but unreadable"
into a hard denial (503), never a pass.

**Lockout state is Postgres, not Redis.** The brief for this work assumed
Redis because the existing ``main.rate_limit_auth`` limiter keeps its counters
in process memory and is therefore per-worker. Postgres solves the same
problem — it is shared by every worker, it is already being read on the login
path (we ``SELECT`` the user by username anyway), and it is durable across a
restart. It also has no fail-open/fail-closed dilemma: a Redis outage already
makes every authenticated request 503 (see ``core/token_revocation``), so
putting lockout there would mean either "a Redis outage unlocks every locked
account" or "a Redis outage locks out everyone" — both worse than "lockout
keeps working". The one thing Postgres cannot do is throttle attempts against
usernames that do not exist; that stays the job of the IP rate limiter, and
counting failures for non-existent accounts would be an enumeration oracle
anyway.

**TOTP replay is also Postgres.** ``users.mfa_last_used_step`` records the
highest accepted time-step; a code is only honoured when its step is strictly
greater. That makes replaying a code inside its own ±1-step window impossible
without a second store that can be unavailable.

All functions here are **stage-only** — they ``db.add`` / mutate / ``flush``
and return. The router handler owns ``await db.commit()``.
"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import pyotp
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import _normalize_user_role, _ROLE_ORDER
from app.core.security import hash_token
from app.models.postgres import (
    AppSetting,
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    MfaRecoveryCode,
    User,
    UserRole,
)
from app.services.secret_service import expire_secret, has_secret, read_secret, store_secret

logger = logging.getLogger("services.mfa")

# ``secret_refs.scope`` used for per-user TOTP seeds; ``key_name`` is the user id.
TOTP_SECRET_SCOPE = "user_totp"

MFA_POLICY_KEY = "mfa_policy"

# RFC 6238 defaults. ±1 step (30 s) of drift tolerance either way.
TOTP_INTERVAL_SECONDS = 30
TOTP_DIGITS = 6
TOTP_VALID_WINDOW = 1

# Recovery-code alphabet: Crockford-ish base32 with I/O/0/1 removed so a code
# read off a screen and typed by hand cannot be transcribed ambiguously.
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_RECOVERY_LENGTH = 16  # 16 chars from a 32-symbol alphabet = 80 bits
_RECOVERY_GROUP = 4

_CODE_RE = re.compile(r"^\d{6}$")


# ── Seed state ───────────────────────────────────────────────────────────────


class SecretState(str, Enum):
    """Why :func:`load_totp_secret` did or did not return a seed."""

    OK = "ok"
    MISSING = "missing"          # nothing stored for this user
    UNREADABLE = "unreadable"    # a row exists but will not decrypt


@dataclass(frozen=True)
class LoadedSecret:
    state: SecretState
    secret: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.state is SecretState.OK and bool(self.secret)


def _secret_key(user_id: uuid.UUID) -> str:
    return str(user_id)


async def load_totp_secret(db: AsyncSession, user: User) -> LoadedSecret:
    """Load a user's TOTP seed, distinguishing "absent" from "undecryptable".

    ``secret_service.read_secret`` collapses both into ``None``. The difference
    matters enormously: for a user with ``mfa_enabled`` set, "absent" is a data
    inconsistency and "undecryptable" is a key-management incident, but *both*
    must deny the login rather than wave it through. Callers get the state so
    they can say which one happened; neither may be read as "MFA is off".
    """
    key = _secret_key(user.id)
    raw = await read_secret(db, TOTP_SECRET_SCOPE, key)
    if raw:
        return LoadedSecret(SecretState.OK, raw)
    if await has_secret(db, TOTP_SECRET_SCOPE, key):
        # A live (non-expired) row exists but decryption returned nothing.
        logger.error(
            "TOTP seed for user_id=%s exists but could not be decrypted — "
            "MFA for this account is broken, not disabled. Likely an "
            "APP_SECRET_KEY rotation without APP_SECRET_KEY_PREVIOUS.",
            user.id,
        )
        return LoadedSecret(SecretState.UNREADABLE)
    return LoadedSecret(SecretState.MISSING)


# ── TOTP ─────────────────────────────────────────────────────────────────────


def generate_totp_secret() -> str:
    """A fresh base32 TOTP seed (160 bits, the RFC 4226 recommendation)."""
    return pyotp.random_base32(length=32)


def build_otpauth_uri(user: User, secret: str) -> str:
    """``otpauth://`` provisioning URI for authenticator apps.

    Returned to the client as a *string*; the SPA renders the QR code. The
    backend deliberately does not depend on ``qrcode``/``Pillow`` — that would
    drag a native imaging toolchain into the offline bundle to produce a
    picture of data we already have.
    """
    return pyotp.TOTP(
        secret, interval=TOTP_INTERVAL_SECONDS, digits=TOTP_DIGITS
    ).provisioning_uri(
        name=user.username or user.email,
        issuer_name=settings.MFA_ISSUER_NAME,
    )


def normalize_totp_code(raw: str) -> Optional[str]:
    """Strip spaces/dashes and validate shape. ``None`` if it is not 6 digits."""
    if not raw:
        return None
    cleaned = re.sub(r"[\s-]", "", str(raw))
    return cleaned if _CODE_RE.match(cleaned) else None


def verify_totp(
    secret: str,
    code: str,
    last_used_step: Optional[int],
    *,
    at: Optional[datetime] = None,
) -> Optional[int]:
    """Verify ``code`` and return the time-step it matched, else ``None``.

    Two properties beyond a plain ``pyotp.verify``:

    * **±1 step of drift** is accepted (the RFC 6238 recommendation), so a
      clock a few seconds out still works.
    * **Replay is rejected**: a step at or below ``last_used_step`` is skipped
      before the comparison, so the same code cannot be presented twice inside
      its own validity window. The caller must persist the returned step.

    Comparison is constant-time; a timing oracle on a 6-digit code is not much
    of an attack but there is no reason to leave one lying around.
    """
    normalized = normalize_totp_code(code)
    if not normalized or not secret:
        return None
    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL_SECONDS, digits=TOTP_DIGITS)
    now = at or datetime.now(timezone.utc)
    current_step = int(now.timestamp()) // TOTP_INTERVAL_SECONDS
    for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
        step = current_step + offset
        if last_used_step is not None and step <= last_used_step:
            continue
        expected = totp.at(step * TOTP_INTERVAL_SECONDS)
        if hmac.compare_digest(expected, normalized):
            return step
    return None


# ── Recovery codes ───────────────────────────────────────────────────────────


def _format_recovery_code(raw: str) -> str:
    return "-".join(
        raw[i:i + _RECOVERY_GROUP] for i in range(0, len(raw), _RECOVERY_GROUP)
    )


def normalize_recovery_code(raw: str) -> str:
    """Uppercase and drop separators so display formatting is irrelevant."""
    return re.sub(r"[\s-]", "", str(raw or "")).upper()


def generate_recovery_codes(count: Optional[int] = None) -> list[str]:
    n = count if count is not None else settings.MFA_RECOVERY_CODE_COUNT
    return [
        _format_recovery_code(
            "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH))
        )
        for _ in range(max(1, int(n)))
    ]


async def issue_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    """Replace the user's recovery codes and return the new plaintext set.

    The plaintext is returned exactly once — only digests are persisted, so a
    lost set can be reissued but never recovered.
    """
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    codes = generate_recovery_codes()
    for code in codes:
        db.add(
            MfaRecoveryCode(
                user_id=user.id,
                code_hash=hash_token(normalize_recovery_code(code)),
            )
        )
    await db.flush()
    return codes


async def count_unused_recovery_codes(db: AsyncSession, user: User) -> int:
    rows = await db.execute(
        select(MfaRecoveryCode.id).where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.used_at.is_(None),
        )
    )
    return len(rows.all())


async def consume_recovery_code(db: AsyncSession, user: User, raw_code: str) -> bool:
    """Burn a recovery code. Returns False if it is unknown or already used.

    Single-use is enforced by the ``used_at IS NULL`` predicate on the lookup
    plus the timestamp written here — the row is kept so "a recovery code was
    used on <date>" stays answerable after the fact.
    """
    normalized = normalize_recovery_code(raw_code)
    if not normalized:
        return False
    result = await db.execute(
        select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_hash == hash_token(normalized),
            MfaRecoveryCode.used_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    row.used_at = datetime.now(timezone.utc)
    await db.flush()
    return True


# ── Enrollment lifecycle ─────────────────────────────────────────────────────


async def start_enrollment(db: AsyncSession, user: User) -> tuple[str, str]:
    """Stage a new (unconfirmed) TOTP seed. Returns ``(secret, otpauth_uri)``.

    The seed is stored immediately but ``mfa_enabled`` is untouched, so an
    abandoned enrollment leaves a dormant seed and changes nothing about how
    the account authenticates. Restarting enrollment overwrites it, which also
    means a user who loses their phone mid-enrollment simply starts again.
    """
    secret = generate_totp_secret()
    await store_secret(db, TOTP_SECRET_SCOPE, _secret_key(user.id), secret, actor_id=user.id)
    await db.flush()
    return secret, build_otpauth_uri(user, secret)


async def confirm_enrollment(
    db: AsyncSession, user: User, code: str
) -> Optional[list[str]]:
    """Verify a code against the staged seed and enable MFA.

    Returns the one-time recovery codes on success, ``None`` if the code did
    not verify. Enabling and issuing recovery codes happen together on purpose:
    an account must never reach ``mfa_enabled=True`` without a way back in.
    """
    loaded = await load_totp_secret(db, user)
    if not loaded.usable:
        return None
    # ``last_used_step=None``: this seed has no history yet.
    step = verify_totp(loaded.secret or "", code, None)
    if step is None:
        return None
    user.mfa_enabled = True
    user.mfa_enrolled_at = datetime.now(timezone.utc)
    user.mfa_last_used_step = step
    codes = await issue_recovery_codes(db, user)
    await db.flush()
    return codes


async def disable_mfa(db: AsyncSession, user: User) -> None:
    """Turn MFA off and destroy the material behind it.

    Order matters only in that everything must go: the flag, the seed
    ciphertext, the replay cursor, and every recovery code. Leaving any of them
    would make a later re-enrollment inherit state from the old device.
    """
    user.mfa_enabled = False
    user.mfa_enrolled_at = None
    user.mfa_last_used_step = None
    await expire_secret(db, TOTP_SECRET_SCOPE, _secret_key(user.id))
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    await db.flush()


# ── SSO exemption ────────────────────────────────────────────────────────────


async def is_sso_managed(db: AsyncSession, user: User) -> bool:
    """True when an external IdP owns this account's authentication.

    Two signals, because one is not enough:

    1. A row in ``federated_identities`` — the account has authenticated
       through, or was linked to, a SAML configuration.
    2. A ``SCIM_USER_CREATED`` identity event for the user. This closes a real
       gap: ``scim_service.scim_create_user`` only creates the federated link
       ``if sso_config_id and external_id``, so a SCIM provisioning call that
       omits either one produces a user that is IdP-managed in every practical
       sense but looks purely local. The audit row is written unconditionally
       on the same code path, so it is the reliable signal.

    Used only to decide whether the workspace MFA *requirement* applies. It
    never suppresses MFA a user has voluntarily enrolled — an SSO-managed user
    with ``mfa_enabled`` set is still challenged on the local password path.
    """
    linked = await db.execute(
        select(FederatedIdentity.id).where(FederatedIdentity.user_id == user.id).limit(1)
    )
    if linked.scalar_one_or_none() is not None:
        return True
    scim = await db.execute(
        select(IdentityEvent.id)
        .where(
            IdentityEvent.user_id == user.id,
            IdentityEvent.event_type == IdentityEventType.SCIM_USER_CREATED,
        )
        .limit(1)
    )
    return scim.scalar_one_or_none() is not None


# ── Workspace policy ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MfaPolicy:
    """Workspace-wide MFA + lockout policy.

    Stored as a single ``app_settings`` row keyed ``mfa_policy`` rather than in
    a dedicated table. The obvious alternative — mirroring
    ``SSOConfiguration.enforcement_mode`` — exists because a SAML config is a
    large object (certificates, URLs, role maps) that has to be modelled
    anyway, and its enforcement mode is one field on it. This policy is four
    scalars with no accompanying object; a table would buy a single-active-row
    lifecycle we have no use for. The ``app_settings`` route also gets
    ``settings_audit_service.log_settings_change`` for free, which is the
    audit trail an auditor will ask for.
    """

    require_mfa: bool = False
    # MFA is required for this role **and every role above it** in
    # ``deps._ROLE_ORDER``. ``None`` with ``require_mfa`` set means "everyone".
    required_for_role: Optional[str] = None
    lockout_enabled: bool = True
    lockout_threshold: int = 10
    lockout_duration_minutes: int = 15

    def as_dict(self) -> dict:
        return {
            "require_mfa": self.require_mfa,
            "required_for_role": self.required_for_role,
            "lockout_enabled": self.lockout_enabled,
            "lockout_threshold": self.lockout_threshold,
            "lockout_duration_minutes": self.lockout_duration_minutes,
        }


DEFAULT_POLICY = MfaPolicy()


def _coerce_int(value, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def policy_from_dict(raw: Optional[dict]) -> MfaPolicy:
    """Build a policy from a stored row, discarding anything unusable.

    A stored value that fails validation is dropped back to the default rather
    than surfaced as if it were in force — the same rule the AI confidence
    threshold follows, so the settings page and the enforcement path can never
    disagree about what is actually applied.
    """
    if not raw:
        return DEFAULT_POLICY
    role = raw.get("required_for_role")
    if role is not None:
        try:
            role = _normalize_user_role(role).value
        except (ValueError, AttributeError):
            role = None
    return MfaPolicy(
        require_mfa=bool(raw.get("require_mfa", DEFAULT_POLICY.require_mfa)),
        required_for_role=role,
        lockout_enabled=bool(raw.get("lockout_enabled", DEFAULT_POLICY.lockout_enabled)),
        lockout_threshold=_coerce_int(
            raw.get("lockout_threshold"), DEFAULT_POLICY.lockout_threshold, 3, 100
        ),
        lockout_duration_minutes=_coerce_int(
            raw.get("lockout_duration_minutes"),
            DEFAULT_POLICY.lockout_duration_minutes,
            1,
            1440,
        ),
    )


async def load_policy(db: AsyncSession) -> MfaPolicy:
    result = await db.execute(select(AppSetting).where(AppSetting.key == MFA_POLICY_KEY))
    row = result.scalar_one_or_none()
    return policy_from_dict(row.value if row else None)


async def save_policy(
    db: AsyncSession, policy: MfaPolicy, actor_id: Optional[uuid.UUID] = None
) -> MfaPolicy:
    result = await db.execute(select(AppSetting).where(AppSetting.key == MFA_POLICY_KEY))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key=MFA_POLICY_KEY, value=policy.as_dict(), updated_by=actor_id))
    else:
        row.value = policy.as_dict()
        row.updated_by = actor_id
    await db.flush()
    return policy


def policy_requires_mfa(user: User, policy: MfaPolicy) -> bool:
    """Does the workspace policy require this user to hold a second factor?"""
    if not policy.require_mfa:
        return False
    if policy.required_for_role is None:
        return True
    try:
        floor = _ROLE_ORDER.index(UserRole(policy.required_for_role))
        actual = _ROLE_ORDER.index(_normalize_user_role(user.role))
    except (ValueError, KeyError):
        # An unrecognised role on either side is not a licence to skip the
        # requirement — a role we cannot place in the hierarchy is treated as
        # in scope.
        return True
    return actual >= floor


# ── Login gate ───────────────────────────────────────────────────────────────


# Shared wording for the "enrolled, but the seed will not decrypt" denial, so
# the login path and the MFA endpoints tell the operator the same story.
SEED_UNREADABLE_DETAIL = (
    "This account has MFA enabled but its stored secret cannot be read. "
    "Authentication is denied until an administrator resets MFA for the "
    "account (see scripts/mfa_breakglass.py)."
)


def mfa_gate_applies(user: User) -> bool:
    """Does second-factor policy apply to the credential behind this request?

    **No.** for an API key. A key embedded in a CI pipeline cannot type a TOTP
    code, and gating it would break every automated ingest the moment an admin
    turned the policy on. MFA is enforced where interactive credentials are
    *minted* — ``POST /auth/login`` — not on every request.

    Written as ``== CREDENTIAL_KIND_JWT`` rather than ``!= CREDENTIAL_KIND_API_KEY``
    so an unknown credential kind falls out of the "interactive" bucket instead
    of being assumed to be a browser session.
    """
    from app.core.deps import CREDENTIAL_KIND_JWT, credential_kind

    return credential_kind(user) == CREDENTIAL_KIND_JWT


class LoginRequirement(str, Enum):
    """What the login path must do after the password checks out."""

    NONE = "none"                # issue tokens
    CHALLENGE = "challenge"      # enrolled — ask for a second factor
    ENROLL = "enroll"            # policy requires MFA, user has none yet
    BROKEN = "broken"            # enrolled but the seed is unusable — deny


async def evaluate_login_requirement(
    db: AsyncSession, user: User, policy: Optional[MfaPolicy] = None
) -> LoginRequirement:
    """Decide the post-password step for a local (password) login.

    Not consulted by ``/api/v1/sso/acs`` — see ``routers/sso.py`` and
    ``architecture/SECURITY.md`` for why the IdP path is exempt.
    """
    policy = policy if policy is not None else await load_policy(db)
    if user.mfa_enabled:
        loaded = await load_totp_secret(db, user)
        if not loaded.usable:
            return LoginRequirement.BROKEN
        return LoginRequirement.CHALLENGE
    if policy_requires_mfa(user, policy) and not await is_sso_managed(db, user):
        return LoginRequirement.ENROLL
    return LoginRequirement.NONE


# ── Account lockout ──────────────────────────────────────────────────────────


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Postgres gives us tz-aware values; SQLite/unit fixtures may not."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def locked_until(user: User, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """The moment the lock lifts, or ``None`` when the account is not locked."""
    until = _aware(user.locked_until)
    if until is None:
        return None
    return until if until > (now or datetime.now(timezone.utc)) else None


async def register_failed_attempt(
    db: AsyncSession,
    user: User,
    policy: MfaPolicy,
    *,
    reason: str,
    ip_address: Optional[str] = None,
) -> Optional[datetime]:
    """Count one failed password/second-factor attempt; lock if over threshold.

    Returns the new ``locked_until`` when this attempt tripped the lock, else
    ``None``. The counter is reset when the lock is applied so a second lock
    costs the attacker another full threshold of attempts rather than one.
    """
    if not policy.lockout_enabled:
        return None
    user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
    if user.failed_login_attempts < policy.lockout_threshold:
        await db.flush()
        return None

    until = datetime.now(timezone.utc) + timedelta(minutes=policy.lockout_duration_minutes)
    user.locked_until = until
    user.failed_login_attempts = 0
    db.add(
        IdentityEvent(
            event_type=IdentityEventType.ACCOUNT_LOCKED,
            user_id=user.id,
            detail={
                "reason": reason,
                "threshold": policy.lockout_threshold,
                "locked_until": until.isoformat(),
                "duration_minutes": policy.lockout_duration_minutes,
            },
            ip_address=ip_address,
            success=False,
        )
    )
    await db.flush()
    logger.warning(
        "Account locked after %s consecutive failures: user_id=%s reason=%s until=%s",
        policy.lockout_threshold, user.id, reason, until.isoformat(),
    )
    return until


async def register_successful_login(db: AsyncSession, user: User) -> None:
    """Clear failure state and stamp ``last_login_at``.

    Called only when authentication is *completely* finished — a correct
    password that still owes a second factor deliberately does not reset the
    counter, so password-guessing cannot be reset by guessing one password
    correctly while failing MFA.
    """
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()


async def clear_lockout(
    db: AsyncSession,
    user: User,
    *,
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
    reason: str = "manual",
) -> None:
    """Administratively unlock an account (also used by the breakglass script)."""
    user.failed_login_attempts = 0
    user.locked_until = None
    db.add(
        IdentityEvent(
            event_type=IdentityEventType.ACCOUNT_UNLOCKED,
            user_id=user.id,
            actor_id=actor_id,
            actor_name=actor_name,
            detail={"reason": reason},
        )
    )
    await db.flush()
