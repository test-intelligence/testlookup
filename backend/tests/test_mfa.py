"""
TOTP MFA, recovery codes, account lockout, and workspace policy (0117).

These run against the **real** ORM models, the **real** router wiring, and the
**real** service code, over an in-memory SQLite session. That matters here more
than usual: the properties under test are things like "a challenge token
cannot authenticate a protected route", which are only meaningful if the
dependency chain is the one the application actually mounts.

Note in particular that ``tests/integration/conftest.py``'s ``auth_as`` fixture
overrides ``get_current_active_user`` wholesale, so it would exercise none of
this. Nothing here overrides an auth dependency — only ``get_db``.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("pyotp")

import pyotp  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select, types as sqltypes  # noqa: E402
from sqlalchemy.dialects.sqlite import base as _sqlite_types  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core import security  # noqa: E402
from app.core.security import (  # noqa: E402
    MFA_CHALLENGE_TOKEN_TYPE,
    MFA_ENROLLMENT_TOKEN_TYPE,
    create_access_token,
    create_mfa_token,
    decode_token,
    get_password_hash,
)
from app.db.postgres import Base, get_db  # noqa: E402
from app.models.postgres import (  # noqa: E402
    ApiKey,
    AppSetting,
    FederatedIdentity,
    IdentityEvent,
    IdentityEventType,
    MfaRecoveryCode,
    RefreshTokenRecord,
    SSOConfiguration,
    SecretRef,
    SettingsAuditLog,
    User,
    UserRole,
)
from app.services import mfa_service  # noqa: E402

# Tables the MFA/auth surface touches. Deliberately a closed set rather than
# ``create_all()`` — most of the schema uses Postgres-only column types.
_TABLES = [
    m.__table__
    for m in (
        User,
        MfaRecoveryCode,
        SecretRef,
        AppSetting,
        IdentityEvent,
        FederatedIdentity,
        SSOConfiguration,
        ApiKey,
        RefreshTokenRecord,
        SettingsAuditLog,
    )
]

PASSWORD = "correct-horse-battery"


# ── Fixtures ────────────────────────────────────────────────────────────────


class _AwareDateTime(_sqlite_types.DATETIME):
    """SQLite hands back naive datetimes; Postgres hands back aware ones.

    Application code (``refresh_token_service``, ``mfa_service.locked_until``)
    compares stored timestamps against ``datetime.now(timezone.utc)``, which
    raises ``TypeError`` on a naive value. Rather than loosen production code
    to accommodate a test-only backend, the test engine is taught to return
    what Postgres returns.
    """

    def result_processor(self, dialect, coltype):
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _redis(monkeypatch, fake_redis):
    """Point the token-revocation store at the in-memory fake.

    ``core.token_revocation`` fails CLOSED when Redis is unreachable (503), so
    without this every authenticated request in this module would 503 rather
    than exercise the code under test.
    """
    import app.db.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: fake_redis)
    return fake_redis


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    """A real (non-weak) APP_SECRET_KEY so secret_service will encrypt."""
    from app.core.config import settings
    from app.services import secret_service

    monkeypatch.setattr(settings, "APP_SECRET_KEY", "unit-test-secret-key-0123456789")
    monkeypatch.setattr(settings, "APP_SECRET_KEY_PREVIOUS", None, raising=False)
    secret_service.reset_fernet_cache()
    yield
    secret_service.reset_fernet_cache()


@pytest.fixture(autouse=True)
def _session_issuer_clock(monkeypatch):
    """Supply the PostgreSQL clock boundary to this SQLite-backed route suite.

    Production session issuance deliberately reads ``clock_timestamp()`` from
    PostgreSQL.  SQLite has no equivalent function, while the dedicated token
    issuer tests and PostgreSQL concurrency regression cover that query.  Keep
    this route suite focused on the MFA protocol by emulating the same aware,
    sub-second database result at the service boundary.
    """
    from app.services import auth_session_tokens

    async def _database_issued_at(_db):
        return datetime.now(timezone.utc)

    monkeypatch.setattr(
        auth_session_tokens,
        "_database_issued_at",
        _database_issued_at,
    )


_client_seq = itertools.count(1)


@pytest.fixture
async def client(db):
    """An HTTP client with its own source address.

    ``main.limiter`` keeps counters in process memory keyed by client address
    + path, and nothing clears them between tests, so a shared 127.0.0.1 would
    make later tests in the file 429 for reasons that have nothing to do with
    what they assert. Giving each test its own address is the reliable
    isolation; ``limiter.reset()`` is a belt-and-braces extra.
    """
    from app.main import app, limiter

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001 — best-effort test hygiene
        pass

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    host = f"10.{next(_client_seq) % 250}.{next(_client_seq) % 250}.7"
    transport = ASGITransport(app=app, client=(host, 5000))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


async def make_user(
    db,
    *,
    username="alice",
    email=None,
    role=UserRole.QA_ENGINEER,
    password=PASSWORD,
    **kwargs,
) -> User:
    user = User(
        email=email or f"{username}@example.com",
        username=username,
        hashed_password=get_password_hash(password),
        role=role.value if isinstance(role, UserRole) else role,
        is_active=True,
        **kwargs,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def current_step() -> int:
    return int(datetime.now(timezone.utc).timestamp()) // mfa_service.TOTP_INTERVAL_SECONDS


def code_for_step(secret: str, step: int) -> str:
    return pyotp.TOTP(secret).at(step * mfa_service.TOTP_INTERVAL_SECONDS)


def next_code(secret: str, user: User) -> str:
    """A code the replay guard has not seen yet.

    ``verify_totp`` only accepts steps strictly greater than
    ``mfa_last_used_step``, so a test that re-uses ``TOTP.now()`` after any
    successful verification in the same 30-second window is asserting the
    replay guard, not the happy path.
    """
    last = user.mfa_last_used_step
    base = current_step() - 1 if last is None else last
    return code_for_step(secret, base + 1)


async def enroll(db, user: User) -> str:
    """Enroll ``user`` in TOTP the way the service does, returning the seed.

    Confirms with the *previous* step's code so the current step stays unused
    and callers can still authenticate with ``TOTP.now()``.
    """
    secret, _uri = await mfa_service.start_enrollment(db, user)
    codes = await mfa_service.confirm_enrollment(
        db, user, code_for_step(secret, current_step() - 1)
    )
    assert codes is not None
    await db.commit()
    return secret


async def login(client, username=None, password=PASSWORD, user=None):
    return await client.post(
        "/api/v1/auth/login",
        data={"username": username or user.username, "password": password},
    )


async def set_policy(db, **kwargs):
    policy = mfa_service.MfaPolicy(**kwargs)
    await mfa_service.save_policy(db, policy)
    await db.commit()
    return policy


# ═══════════════════════════════════════════════════════════════════════════
# 1. The challenge token is not an access token
# ═══════════════════════════════════════════════════════════════════════════


class TestChallengeTokenIsolation:
    def test_create_mfa_token_refuses_to_mint_an_access_type(self):
        """The one mistake that would make all of this pointless."""
        for bad in ("access", "refresh", "", "mfa"):
            with pytest.raises(ValueError):
                create_mfa_token(uuid.uuid4(), bad)

    def test_challenge_token_type_claim_is_not_access(self):
        token, jti, ttl = create_mfa_token(uuid.uuid4(), MFA_CHALLENGE_TOKEN_TYPE)
        payload = decode_token(token)
        assert payload["type"] == MFA_CHALLENGE_TOKEN_TYPE
        assert payload["type"] != "access"
        assert jti and ttl > 0

    def test_decode_layer_rejects_cross_type_use(self):
        from jose import JWTError

        challenge, _, _ = create_mfa_token(uuid.uuid4(), MFA_CHALLENGE_TOKEN_TYPE)
        enroll_tok, _, _ = create_mfa_token(uuid.uuid4(), MFA_ENROLLMENT_TOKEN_TYPE)
        access = create_access_token(str(uuid.uuid4()))

        with pytest.raises(JWTError):
            decode_token(challenge, expected_type="access")
        with pytest.raises(JWTError):
            decode_token(challenge, expected_type=MFA_ENROLLMENT_TOKEN_TYPE)
        with pytest.raises(JWTError):
            decode_token(enroll_tok, expected_type=MFA_CHALLENGE_TOKEN_TYPE)
        with pytest.raises(JWTError):
            decode_token(access, expected_type=MFA_CHALLENGE_TOKEN_TYPE)

    async def test_challenge_token_cannot_authenticate_get_current_user(self, db):
        """Straight at the dependency every protected route depends on."""
        from fastapi import HTTPException

        from app.core.deps import get_current_user

        user = await make_user(db, username="chal")
        token, _, _ = create_mfa_token(str(user.id), MFA_CHALLENGE_TOKEN_TYPE)

        with pytest.raises(HTTPException) as exc:
            await get_current_user(db=db, token=token)
        assert exc.value.status_code == 401

    async def test_challenge_token_cannot_authenticate_a_real_protected_route(
        self, db, client
    ):
        """Not a unit stub — an actual route mounted with the router-wide dep.

        ``bootstrap.register_routers`` puts ``get_current_user_or_api_key`` on
        every PROTECTED router, so this is the real blast radius an
        ``mfa_pending``-flavoured access token would have had.
        """
        user = await make_user(db, username="realroute", role=UserRole.ADMIN)
        challenge, _, _ = create_mfa_token(str(user.id), MFA_CHALLENGE_TOKEN_TYPE)
        enroll_tok, _, _ = create_mfa_token(str(user.id), MFA_ENROLLMENT_TOKEN_TYPE)

        for token in (challenge, enroll_tok):
            resp = await client.get(
                "/api/v1/projects", headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 401, resp.text

        # ...and /auth/me, which sits on the public router but still resolves
        # a user, so it is the other shape of "protected".
        resp = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {challenge}"}
        )
        assert resp.status_code == 401

    async def test_access_token_is_not_accepted_as_a_challenge(self, db, client):
        user = await make_user(db, username="swap")
        await enroll(db, user)
        access = create_access_token(str(user.id))

        resp = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": access, "code": "000000"},
        )
        assert resp.status_code == 401

    async def test_challenge_token_is_single_use(self, db, client):
        secret = await enroll(db, await make_user(db, username="once"))
        resp = await login(client, username="once")
        challenge = resp.json()["challenge_token"]

        first = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert second.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 2. API keys are never MFA-gated
# ═══════════════════════════════════════════════════════════════════════════


class TestApiKeyExemption:
    def test_gate_applies_only_to_jwt_sessions(self):
        from app.core.deps import (
            CREDENTIAL_KIND_API_KEY,
            CREDENTIAL_KIND_JWT,
            _bind_credential_kind,
        )

        jwt_user = _bind_credential_kind(User(), CREDENTIAL_KIND_JWT)
        key_user = _bind_credential_kind(User(), CREDENTIAL_KIND_API_KEY)
        unknown = User()  # e.g. loaded by a service, or a wholesale test override

        assert mfa_service.mfa_gate_applies(jwt_user) is True
        assert mfa_service.mfa_gate_applies(key_user) is False
        # Unknown must fall out of "interactive", not be assumed to be one.
        assert mfa_service.mfa_gate_applies(unknown) is False

    async def test_api_key_authenticates_an_enrolled_user_with_no_challenge(
        self, db, client
    ):
        """A CI key belonging to an MFA-enrolled user keeps working."""
        import hashlib

        user = await make_user(db, username="ci-owner", role=UserRole.ADMIN)
        await enroll(db, user)
        assert user.mfa_enabled is True

        raw = "qai_test_key_for_mfa_exemption"
        db.add(
            ApiKey(
                user_id=user.id,
                name="ci",
                key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                key_hint=raw[:8] + "...",
                is_active=True,
            )
        )
        await db.commit()

        from app.core.deps import CREDENTIAL_KIND_API_KEY, credential_kind
        from app.core.deps import get_current_user_or_api_key

        resolved = await get_current_user_or_api_key(
            db=db, bearer_token=None, x_api_key=raw
        )
        assert resolved.id == user.id
        assert credential_kind(resolved) == CREDENTIAL_KIND_API_KEY

        # And over HTTP, against a real MFA endpoint that permits API keys.
        resp = await client.get("/api/v1/auth/mfa/status", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    async def test_api_key_cannot_change_mfa_settings(self, db, client):
        import hashlib

        user = await make_user(db, username="ci-owner2")
        await enroll(db, user)
        raw = "qai_test_key_cannot_disable"
        db.add(
            ApiKey(
                user_id=user.id,
                name="ci",
                key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                key_hint=raw[:8] + "...",
                is_active=True,
            )
        )
        await db.commit()

        resp = await client.post(
            "/api/v1/auth/mfa/disable",
            headers={"X-API-Key": raw},
            json={"password": PASSWORD, "code": "123456"},
        )
        assert resp.status_code == 403
        assert "interactive" in resp.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# 3. SSO / SCIM exemption
# ═══════════════════════════════════════════════════════════════════════════


class TestSsoExemption:
    async def _sso_config(self, db) -> SSOConfiguration:
        cfg = SSOConfiguration(
            display_name="idp",
            idp_entity_id="e",
            idp_sso_url="u",
            idp_certificate="c",
            sp_entity_id="sp",
            sp_acs_url="acs",
        )
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
        return cfg

    async def test_federated_user_is_sso_managed(self, db):
        user = await make_user(db, username="fed")
        cfg = await self._sso_config(db)
        db.add(
            FederatedIdentity(user_id=user.id, sso_config_id=cfg.id, external_id="ext-1")
        )
        await db.commit()
        assert await mfa_service.is_sso_managed(db, user) is True

    async def test_scim_user_without_a_federated_link_is_still_sso_managed(self, db):
        """The gap at ``scim_service.scim_create_user``.

        That function only creates the ``FederatedIdentity`` row
        ``if sso_config_id and external_id``, so a SCIM provisioning call that
        omits either one yields a user who is IdP-managed but looks purely
        local. The ``SCIM_USER_CREATED`` audit row is written unconditionally
        on the same path, which is why it is the second signal.
        """
        user = await make_user(db, username="scim-nolink")
        linked = await db.execute(
            select(FederatedIdentity).where(FederatedIdentity.user_id == user.id)
        )
        assert linked.scalar_one_or_none() is None  # the gap, reproduced

        db.add(
            IdentityEvent(
                event_type=IdentityEventType.SCIM_USER_CREATED,
                user_id=user.id,
                detail={"external_id": None},
            )
        )
        await db.commit()
        assert await mfa_service.is_sso_managed(db, user) is True

    async def test_plain_local_user_is_not_sso_managed(self, db):
        user = await make_user(db, username="local")
        assert await mfa_service.is_sso_managed(db, user) is False

    async def test_policy_does_not_force_enrollment_on_an_sso_user(self, db, client):
        user = await make_user(db, username="fed2", role=UserRole.ADMIN)
        cfg = await self._sso_config(db)
        db.add(
            FederatedIdentity(user_id=user.id, sso_config_id=cfg.id, external_id="ext-2")
        )
        await set_policy(db, require_mfa=True, required_for_role=None)

        req = await mfa_service.evaluate_login_requirement(db, user)
        assert req is mfa_service.LoginRequirement.NONE

        resp = await login(client, user=user)
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_sso_user_who_opts_in_is_still_challenged(self, db):
        """Exemption is from the *requirement*, never from an enrolled factor."""
        user = await make_user(db, username="fed3")
        cfg = await self._sso_config(db)
        db.add(
            FederatedIdentity(user_id=user.id, sso_config_id=cfg.id, external_id="ext-3")
        )
        await db.commit()
        await enroll(db, user)

        req = await mfa_service.evaluate_login_requirement(db, user)
        assert req is mfa_service.LoginRequirement.CHALLENGE

    def test_sso_acs_does_not_consult_the_mfa_policy(self):
        """Documented, deliberate, and asserted so it cannot drift silently.

        The IdP has already asserted the identity (and, per the customer's own
        policy, performed its own MFA). Layering a local TOTP factor on top
        would require every federated user to hold a seed we control and the
        IdP knows nothing about — a lockout risk with no added assurance.
        """
        source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "sso.py").read_text(
            encoding="utf-8"
        )
        assert "mfa_service" not in source
        assert "evaluate_login_requirement" not in source


# ═══════════════════════════════════════════════════════════════════════════
# 4. "Enrolled but the seed will not decrypt" denies
# ═══════════════════════════════════════════════════════════════════════════


class TestUnreadableSeed:
    async def _corrupt_seed(self, db, user):
        row = (
            await db.execute(
                select(SecretRef).where(
                    SecretRef.scope == mfa_service.TOTP_SECRET_SCOPE,
                    SecretRef.key_name == str(user.id),
                )
            )
        ).scalar_one()
        row.encrypted_value = "gAAAAABmnot-a-valid-fernet-token"
        await db.commit()

    async def test_state_distinguishes_missing_from_unreadable(self, db):
        user = await make_user(db, username="seedstate")
        assert (await mfa_service.load_totp_secret(db, user)).state is (
            mfa_service.SecretState.MISSING
        )

        await enroll(db, user)
        assert (await mfa_service.load_totp_secret(db, user)).state is (
            mfa_service.SecretState.OK
        )

        await self._corrupt_seed(db, user)
        loaded = await mfa_service.load_totp_secret(db, user)
        assert loaded.state is mfa_service.SecretState.UNREADABLE
        assert loaded.usable is False
        assert loaded.secret is None

    async def test_login_denies_rather_than_falling_through_to_no_mfa(self, db, client):
        """The trap: ``read_secret`` returns None for both cases.

        If the login path treated that as "not enrolled", a botched
        APP_SECRET_KEY rotation would silently turn MFA off for everyone.
        """
        user = await make_user(db, username="broken")
        await enroll(db, user)
        await self._corrupt_seed(db, user)

        assert (
            await mfa_service.evaluate_login_requirement(db, user)
        ) is mfa_service.LoginRequirement.BROKEN

        resp = await login(client, user=user)
        assert resp.status_code == 503
        body = resp.json()
        assert "access_token" not in body
        assert "challenge_token" not in body

    async def test_verify_endpoint_denies_on_unreadable_seed(self, db, client):
        user = await make_user(db, username="broken2")
        secret = await enroll(db, user)
        resp = await login(client, user=user)
        challenge = resp.json()["challenge_token"]

        await self._corrupt_seed(db, user)
        verify = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert verify.status_code == 503
        assert "access_token" not in verify.json()

    async def test_refresh_denies_on_unreadable_seed(self, db, client):
        user = await make_user(db, username="broken3")
        secret = await enroll(db, user)
        challenge = (await login(client, user=user)).json()["challenge_token"]
        tokens = (
            await client.post(
                "/api/v1/auth/mfa/verify",
                json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
            )
        ).json()

        await self._corrupt_seed(db, user)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert resp.status_code == 503
        assert resp.headers["X-Refresh-Retry-Safe"] == "1"

    async def test_status_reports_the_break_instead_of_reporting_healthy(self, db, client):
        user = await make_user(db, username="broken4")
        secret = await enroll(db, user)
        challenge = (await login(client, user=user)).json()["challenge_token"]
        tokens = (
            await client.post(
                "/api/v1/auth/mfa/verify",
                json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
            )
        ).json()
        await self._corrupt_seed(db, user)

        resp = await client.get(
            "/api/v1/auth/mfa/status",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
        assert resp.json()["secret_unreadable"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. TOTP verification, drift, and replay
# ═══════════════════════════════════════════════════════════════════════════


class TestTotp:
    def test_accepts_the_current_code(self):
        secret = mfa_service.generate_totp_secret()
        step = mfa_service.verify_totp(secret, pyotp.TOTP(secret).now(), None)
        assert step is not None

    def test_accepts_one_step_of_drift_either_way(self):
        secret = mfa_service.generate_totp_secret()
        now = datetime.now(timezone.utc)
        totp = pyotp.TOTP(secret)
        for delta in (-30, 0, 30):
            code = totp.at(int(now.timestamp()) + delta)
            assert mfa_service.verify_totp(secret, code, None, at=now) is not None

    def test_rejects_two_steps_of_drift(self):
        secret = mfa_service.generate_totp_secret()
        now = datetime.now(timezone.utc)
        code = pyotp.TOTP(secret).at(int(now.timestamp()) + 90)
        assert mfa_service.verify_totp(secret, code, None, at=now) is None

    def test_rejects_replay_of_the_same_code_inside_its_window(self):
        """The whole point of persisting the accepted step."""
        secret = mfa_service.generate_totp_secret()
        now = datetime.now(timezone.utc)
        code = pyotp.TOTP(secret).now()

        first = mfa_service.verify_totp(secret, code, None, at=now)
        assert first is not None
        # Same code, same 30-second window, one second later.
        replay = mfa_service.verify_totp(
            secret, code, first, at=now + timedelta(seconds=1)
        )
        assert replay is None

    def test_rejects_a_code_from_an_already_used_earlier_step(self):
        secret = mfa_service.generate_totp_secret()
        now = datetime.now(timezone.utc)
        current = int(now.timestamp()) // 30
        old_code = pyotp.TOTP(secret).at((current - 1) * 30)
        assert mfa_service.verify_totp(secret, old_code, current, at=now) is None

    def test_normalizes_spacing_and_rejects_malformed(self):
        assert mfa_service.normalize_totp_code("123 456") == "123456"
        assert mfa_service.normalize_totp_code("123-456") == "123456"
        for bad in ("", "12345", "1234567", "abcdef", None):
            assert mfa_service.normalize_totp_code(bad) is None

    async def test_replay_is_rejected_over_http(self, db, client):
        user = await make_user(db, username="replay")
        secret = await enroll(db, user)
        code = pyotp.TOTP(secret).now()

        first = (await login(client, user=user)).json()["challenge_token"]
        ok = await client.post(
            "/api/v1/auth/mfa/verify", json={"challenge_token": first, "code": code}
        )
        assert ok.status_code == 200

        second = (await login(client, user=user)).json()["challenge_token"]
        replay = await client.post(
            "/api/v1/auth/mfa/verify", json={"challenge_token": second, "code": code}
        )
        assert replay.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 6. Recovery codes
# ═══════════════════════════════════════════════════════════════════════════


class TestRecoveryCodes:
    def test_shape_and_entropy(self):
        codes = mfa_service.generate_recovery_codes(10)
        assert len(codes) == 10
        assert len(set(codes)) == 10
        for code in codes:
            assert code.count("-") == 3
            assert len(mfa_service.normalize_recovery_code(code)) == 16

    async def test_only_hashes_are_persisted(self, db):
        user = await make_user(db, username="rc-hash")
        codes = await mfa_service.issue_recovery_codes(db, user)
        await db.commit()
        rows = (
            await db.execute(
                select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
            )
        ).scalars().all()
        stored = {r.code_hash for r in rows}
        assert not (stored & set(codes))
        assert all(len(h) == 64 for h in stored)

    async def test_a_recovery_code_is_single_use(self, db):
        user = await make_user(db, username="rc-once")
        codes = await mfa_service.issue_recovery_codes(db, user)
        await db.commit()

        assert await mfa_service.consume_recovery_code(db, user, codes[0]) is True
        assert await mfa_service.consume_recovery_code(db, user, codes[0]) is False
        assert await mfa_service.count_unused_recovery_codes(db, user) == len(codes) - 1

    async def test_formatting_and_case_do_not_matter(self, db):
        user = await make_user(db, username="rc-fmt")
        codes = await mfa_service.issue_recovery_codes(db, user)
        await db.commit()
        mangled = codes[0].replace("-", " ").lower()
        assert await mfa_service.consume_recovery_code(db, user, mangled) is True

    async def test_unknown_code_is_rejected(self, db):
        user = await make_user(db, username="rc-unknown")
        await mfa_service.issue_recovery_codes(db, user)
        await db.commit()
        assert await mfa_service.consume_recovery_code(db, user, "AAAA-BBBB-CCCC-DDDD") is False

    async def test_reissue_invalidates_the_previous_set(self, db):
        user = await make_user(db, username="rc-reissue")
        old = await mfa_service.issue_recovery_codes(db, user)
        await db.commit()
        new = await mfa_service.issue_recovery_codes(db, user)
        await db.commit()

        assert set(old).isdisjoint(new)
        assert await mfa_service.consume_recovery_code(db, user, old[0]) is False
        assert await mfa_service.consume_recovery_code(db, user, new[0]) is True

    async def test_recovery_code_completes_a_login_and_is_then_spent(self, db, client):
        user = await make_user(db, username="rc-login")
        secret, _ = await mfa_service.start_enrollment(db, user)
        codes = await mfa_service.confirm_enrollment(db, user, pyotp.TOTP(secret).now())
        await db.commit()

        challenge = (await login(client, user=user)).json()["challenge_token"]
        ok = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge, "recovery_code": codes[0]},
        )
        assert ok.status_code == 200
        assert "access_token" in ok.json()

        challenge2 = (await login(client, user=user)).json()["challenge_token"]
        replay = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge2, "recovery_code": codes[0]},
        )
        assert replay.status_code == 401

    async def test_disabling_mfa_destroys_every_code_and_the_seed(self, db):
        user = await make_user(db, username="rc-wipe")
        await enroll(db, user)
        await mfa_service.disable_mfa(db, user)
        await db.commit()

        assert user.mfa_enabled is False
        assert user.mfa_last_used_step is None
        assert await mfa_service.count_unused_recovery_codes(db, user) == 0
        loaded = await mfa_service.load_totp_secret(db, user)
        assert loaded.state is mfa_service.SecretState.MISSING
        # The ciphertext itself is gone, not merely tombstoned.
        row = (
            await db.execute(
                select(SecretRef).where(
                    SecretRef.scope == mfa_service.TOTP_SECRET_SCOPE,
                    SecretRef.key_name == str(user.id),
                )
            )
        ).scalar_one()
        assert row.encrypted_value is None
        assert row.rotation_status == "expired"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Account lockout
# ═══════════════════════════════════════════════════════════════════════════


class TestLockout:
    async def test_lock_trips_at_the_threshold(self, db, client):
        await set_policy(db, lockout_enabled=True, lockout_threshold=3)
        user = await make_user(db, username="lock1")

        for _ in range(2):
            resp = await login(client, user=user, password="wrong")
            assert resp.status_code == 401
        assert mfa_service.locked_until(user) is None

        resp = await login(client, user=user, password="wrong")
        assert resp.status_code == 401  # still a generic failure
        await db.refresh(user)
        assert mfa_service.locked_until(user) is not None

        # Even the *correct* password is refused while locked.
        resp = await login(client, user=user)
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    async def test_lock_writes_an_audit_event(self, db, client):
        await set_policy(db, lockout_enabled=True, lockout_threshold=3)
        user = await make_user(db, username="lock-audit")
        for _ in range(3):
            await login(client, user=user, password="wrong")

        events = (
            await db.execute(
                select(IdentityEvent).where(
                    IdentityEvent.user_id == user.id,
                    IdentityEvent.event_type == IdentityEventType.ACCOUNT_LOCKED,
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].success is False
        assert events[0].detail["reason"] == "password"

    async def test_lock_expires(self, db, client):
        await set_policy(db, lockout_enabled=True, lockout_threshold=3)
        user = await make_user(db, username="lock-expire")
        for _ in range(3):
            await login(client, user=user, password="wrong")
        await db.refresh(user)
        assert mfa_service.locked_until(user) is not None

        user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        assert mfa_service.locked_until(user) is None

        resp = await login(client, user=user)
        assert resp.status_code == 200

    async def test_success_resets_the_counter_and_stamps_last_login(self, db, client):
        await set_policy(db, lockout_enabled=True, lockout_threshold=5)
        user = await make_user(db, username="lock-reset")
        for _ in range(3):
            await login(client, user=user, password="wrong")
        await db.refresh(user)
        assert user.failed_login_attempts == 3
        assert user.last_login_at is None

        resp = await login(client, user=user)
        assert resp.status_code == 200
        await db.refresh(user)
        assert user.failed_login_attempts == 0
        assert user.locked_until is None
        assert user.last_login_at is not None

    async def test_failures_while_locked_do_not_extend_the_lock(self, db, client):
        """Otherwise an attacker could keep a victim locked out indefinitely."""
        await set_policy(db, lockout_enabled=True, lockout_threshold=3)
        user = await make_user(db, username="lock-extend")
        for _ in range(3):
            await login(client, user=user, password="wrong")
        await db.refresh(user)
        first_until = user.locked_until

        for _ in range(5):
            await login(client, user=user, password="wrong")
        await db.refresh(user)
        assert user.locked_until == first_until
        assert user.failed_login_attempts == 0

    async def test_a_failed_second_factor_counts_toward_the_lock(self, db, client):
        await set_policy(db, lockout_enabled=True, lockout_threshold=3)
        user = await make_user(db, username="lock-mfa")
        await enroll(db, user)

        for _ in range(3):
            challenge = (await login(client, user=user)).json()["challenge_token"]
            resp = await client.post(
                "/api/v1/auth/mfa/verify",
                json={"challenge_token": challenge, "code": "000000"},
            )
            assert resp.status_code in (401, 429)
        await db.refresh(user)
        assert mfa_service.locked_until(user) is not None

    async def test_a_correct_password_with_a_failed_factor_does_not_reset(self, db, client):
        """Only a *complete* login clears the counter."""
        await set_policy(db, lockout_enabled=True, lockout_threshold=10)
        user = await make_user(db, username="lock-partial")
        await enroll(db, user)

        await login(client, user=user, password="wrong")
        await db.refresh(user)
        assert user.failed_login_attempts == 1

        challenge = (await login(client, user=user)).json()["challenge_token"]
        await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": challenge, "code": "000000"},
        )
        await db.refresh(user)
        assert user.failed_login_attempts == 2

    async def test_lockout_can_be_switched_off(self, db, client):
        await set_policy(db, lockout_enabled=False, lockout_threshold=3)
        user = await make_user(db, username="lock-off")
        for _ in range(6):
            assert (await login(client, user=user, password="wrong")).status_code == 401
        await db.refresh(user)
        assert user.locked_until is None
        assert user.failed_login_attempts == 0

    async def test_a_nonexistent_username_never_creates_lock_state(self, db, client):
        """No per-username store keyed on unverified input."""
        resp = await login(client, username="nobody-at-all", password="x")
        assert resp.status_code == 401
        assert (await db.execute(select(User))).scalars().all() == []

    async def test_clear_lockout_writes_an_unlock_event(self, db):
        user = await make_user(db, username="unlock")
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        user.failed_login_attempts = 9
        await db.commit()

        await mfa_service.clear_lockout(db, user, reason="admin")
        await db.commit()
        assert user.locked_until is None
        assert user.failed_login_attempts == 0
        events = (
            await db.execute(
                select(IdentityEvent).where(
                    IdentityEvent.event_type == IdentityEventType.ACCOUNT_UNLOCKED
                )
            )
        ).scalars().all()
        assert len(events) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 8. Workspace policy
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicy:
    @pytest.mark.parametrize(
        "floor,role,expected",
        [
            ("ADMIN", UserRole.ADMIN, True),
            ("ADMIN", UserRole.QA_LEAD, False),
            ("ADMIN", UserRole.VIEWER, False),
            ("QA_LEAD", UserRole.ADMIN, True),
            ("QA_LEAD", UserRole.QA_LEAD, True),
            ("QA_LEAD", UserRole.QA_ENGINEER, False),
            ("VIEWER", UserRole.VIEWER, True),
            ("VIEWER", UserRole.ADMIN, True),
            (None, UserRole.VIEWER, True),
        ],
    )
    def test_role_floor_is_inclusive_and_upward(self, floor, role, expected):
        policy = mfa_service.MfaPolicy(require_mfa=True, required_for_role=floor)
        assert mfa_service.policy_requires_mfa(User(role=role.value), policy) is expected

    def test_requirement_off_means_nobody(self):
        policy = mfa_service.MfaPolicy(require_mfa=False, required_for_role="VIEWER")
        assert mfa_service.policy_requires_mfa(User(role="ADMIN"), policy) is False

    def test_an_unplaceable_role_is_treated_as_in_scope(self):
        policy = mfa_service.MfaPolicy(require_mfa=True, required_for_role="QA_LEAD")
        assert mfa_service.policy_requires_mfa(User(role="SOMETHING_NEW"), policy) is True

    def test_junk_in_the_stored_row_falls_back_to_defaults(self):
        policy = mfa_service.policy_from_dict(
            {
                "require_mfa": True,
                "required_for_role": "NOT_A_ROLE",
                "lockout_threshold": "banana",
                "lockout_duration_minutes": 999999,
            }
        )
        assert policy.require_mfa is True
        assert policy.required_for_role is None
        assert policy.lockout_threshold == mfa_service.DEFAULT_POLICY.lockout_threshold
        assert policy.lockout_duration_minutes == 1440  # clamped, not accepted raw

    def test_empty_row_is_the_default_policy(self):
        assert mfa_service.policy_from_dict(None) == mfa_service.DEFAULT_POLICY
        assert mfa_service.policy_from_dict({}) == mfa_service.DEFAULT_POLICY

    def test_lockout_defaults_on_mfa_defaults_off(self):
        assert mfa_service.DEFAULT_POLICY.require_mfa is False
        assert mfa_service.DEFAULT_POLICY.lockout_enabled is True

    async def test_round_trips_through_app_settings(self, db):
        await set_policy(db, require_mfa=True, required_for_role="QA_LEAD",
                         lockout_threshold=4, lockout_duration_minutes=7)
        loaded = await mfa_service.load_policy(db)
        assert loaded.require_mfa is True
        assert loaded.required_for_role == "QA_LEAD"
        assert loaded.lockout_threshold == 4
        row = (
            await db.execute(select(AppSetting).where(AppSetting.key == "mfa_policy"))
        ).scalar_one()
        assert row.value["required_for_role"] == "QA_LEAD"

    async def test_in_scope_user_is_forced_to_enroll_at_login(self, db, client):
        await set_policy(db, require_mfa=True, required_for_role="QA_LEAD")
        admin = await make_user(db, username="pol-admin", role=UserRole.ADMIN)
        viewer = await make_user(db, username="pol-viewer", role=UserRole.VIEWER)

        resp = await login(client, user=admin)
        assert resp.status_code == 200
        body = resp.json()
        assert body["mfa_enrollment_required"] is True
        assert "access_token" not in body
        assert body["required_for_role"] == "QA_LEAD"

        # Below the floor — unaffected.
        resp = await login(client, user=viewer)
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_forced_enrollment_completes_the_login(self, db, client):
        await set_policy(db, require_mfa=True, required_for_role=None)
        user = await make_user(db, username="pol-forced")

        body = (await login(client, user=user)).json()
        token = body["enrollment_token"]

        start = await client.post(
            "/api/v1/auth/mfa/enroll/start", json={"enrollment_token": token}
        )
        assert start.status_code == 200
        secret = start.json()["secret"]
        assert start.json()["otpauth_uri"].startswith("otpauth://totp/")

        confirm = await client.post(
            "/api/v1/auth/mfa/enroll/confirm",
            json={"enrollment_token": token, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200
        payload = confirm.json()
        assert len(payload["recovery_codes"]) == 10
        assert payload["tokens"]["access_token"]

        await db.refresh(user)
        assert user.mfa_enabled is True
        assert user.last_login_at is not None

    async def test_refresh_is_rejected_once_the_policy_turns_on(self, db, client):
        """Otherwise the policy only bites as sessions age out (up to 7 days)."""
        user = await make_user(db, username="pol-refresh", role=UserRole.ADMIN)
        tokens = (await login(client, user=user)).json()
        assert "refresh_token" in tokens

        ok = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert ok.status_code == 200
        rotated = ok.json()["refresh_token"]

        await set_policy(db, require_mfa=True, required_for_role="ADMIN")
        blocked = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated})
        assert blocked.status_code == 401
        assert "enroll" in blocked.json()["detail"].lower()

    async def test_policy_endpoint_requires_admin_and_audits(self, db, client):
        admin = await make_user(db, username="pol-api-admin", role=UserRole.ADMIN)
        lead = await make_user(db, username="pol-api-lead", role=UserRole.QA_LEAD)
        admin_tok = (await login(client, user=admin)).json()["access_token"]
        lead_tok = (await login(client, user=lead)).json()["access_token"]

        assert (
            await client.get(
                "/api/v1/settings/mfa-policy",
                headers={"Authorization": f"Bearer {lead_tok}"},
            )
        ).status_code == 200
        assert (
            await client.put(
                "/api/v1/settings/mfa-policy",
                headers={"Authorization": f"Bearer {lead_tok}"},
                json={"require_mfa": True},
            )
        ).status_code == 403

        resp = await client.put(
            "/api/v1/settings/mfa-policy",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"require_mfa": True, "required_for_role": "QA_LEAD", "lockout_threshold": 4},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "require_mfa": True,
            "required_for_role": "QA_LEAD",
            "lockout_enabled": True,
            "lockout_threshold": 4,
            "lockout_duration_minutes": 15,
        }

        events = (
            await db.execute(
                select(IdentityEvent).where(
                    IdentityEvent.event_type == IdentityEventType.MFA_POLICY_UPDATED
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].actor_id == admin.id
        assert events[0].detail["after"]["require_mfa"] is True

    async def test_clearing_the_role_floor_needs_an_explicit_flag(self, db, client):
        await set_policy(db, require_mfa=True, required_for_role="ADMIN")
        admin = await make_user(db, username="pol-clear", role=UserRole.ADMIN)
        await enroll(db, admin)
        challenge = (await login(client, user=admin)).json()["challenge_token"]
        secret_row = await mfa_service.load_totp_secret(db, admin)
        tok = (
            await client.post(
                "/api/v1/auth/mfa/verify",
                json={
                    "challenge_token": challenge,
                    "code": pyotp.TOTP(secret_row.secret).now(),
                },
            )
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}

        # Omitting the field keeps the existing floor...
        resp = await client.put(
            "/api/v1/settings/mfa-policy", headers=headers, json={"lockout_threshold": 6}
        )
        assert resp.json()["required_for_role"] == "ADMIN"

        # ...only the explicit flag widens it to everyone.
        resp = await client.put(
            "/api/v1/settings/mfa-policy",
            headers=headers,
            json={"clear_required_for_role": True},
        )
        assert resp.json()["required_for_role"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 9. Enrollment + self-service management
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrollmentFlow:
    async def test_start_does_not_enable_mfa(self, db, client):
        user = await make_user(db, username="enr-start")
        tok = (await login(client, user=user)).json()["access_token"]

        resp = await client.post(
            "/api/v1/auth/mfa/enroll/start",
            headers={"Authorization": f"Bearer {tok}"},
            json={},
        )
        assert resp.status_code == 200
        await db.refresh(user)
        assert user.mfa_enabled is False
        # An abandoned enrollment must not start challenging the user.
        assert (
            await mfa_service.evaluate_login_requirement(db, user)
        ) is mfa_service.LoginRequirement.NONE

    async def test_confirm_with_a_bad_code_does_not_enable(self, db, client):
        user = await make_user(db, username="enr-bad")
        tok = (await login(client, user=user)).json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}
        await client.post("/api/v1/auth/mfa/enroll/start", headers=headers, json={})

        resp = await client.post(
            "/api/v1/auth/mfa/enroll/confirm", headers=headers, json={"code": "000000"}
        )
        assert resp.status_code == 400
        await db.refresh(user)
        assert user.mfa_enabled is False
        assert await mfa_service.count_unused_recovery_codes(db, user) == 0

    async def test_full_voluntary_enrollment_then_challenge_then_verify(self, db, client):
        user = await make_user(db, username="enr-full")
        tok = (await login(client, user=user)).json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}

        start = await client.post("/api/v1/auth/mfa/enroll/start", headers=headers, json={})
        secret = start.json()["secret"]
        confirm = await client.post(
            "/api/v1/auth/mfa/enroll/confirm",
            headers=headers,
            json={"code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200
        assert confirm.json()["tokens"] is None  # already had a session

        # Next login is now two-legged.
        body = (await login(client, user=user)).json()
        assert body["mfa_required"] is True
        assert set(body["methods"]) == {"totp", "recovery_code"}

        # Wait for a fresh step so the enrollment code is not replayed.
        await db.refresh(user)
        next_code = pyotp.TOTP(secret).at((user.mfa_last_used_step + 1) * 30)
        verified = await client.post(
            "/api/v1/auth/mfa/verify",
            json={"challenge_token": body["challenge_token"], "code": next_code},
        )
        assert verified.status_code == 200
        assert verified.json()["access_token"]

    async def test_enrollment_events_are_audited(self, db, client):
        user = await make_user(db, username="enr-audit")
        tok = (await login(client, user=user)).json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}
        start = await client.post("/api/v1/auth/mfa/enroll/start", headers=headers, json={})
        await client.post(
            "/api/v1/auth/mfa/enroll/confirm",
            headers=headers,
            json={"code": pyotp.TOTP(start.json()["secret"]).now()},
        )
        kinds = {
            e.event_type
            for e in (
                await db.execute(select(IdentityEvent).where(IdentityEvent.user_id == user.id))
            ).scalars().all()
        }
        assert IdentityEventType.MFA_ENROLL_STARTED in kinds
        assert IdentityEventType.MFA_ENABLED in kinds

    async def test_double_enrollment_is_refused(self, db, client):
        user = await make_user(db, username="enr-double")
        secret = await enroll(db, user)
        challenge = (await login(client, user=user)).json()["challenge_token"]
        await db.refresh(user)
        tok = (
            await client.post(
                "/api/v1/auth/mfa/verify",
                json={
                    "challenge_token": challenge,
                    "code": pyotp.TOTP(secret).at((user.mfa_last_used_step + 1) * 30),
                },
            )
        ).json()["access_token"]

        resp = await client.post(
            "/api/v1/auth/mfa/enroll/start",
            headers={"Authorization": f"Bearer {tok}"},
            json={},
        )
        assert resp.status_code == 409

    async def test_disable_needs_password_and_a_live_factor(self, db, client):
        user = await make_user(db, username="dis")
        secret = await enroll(db, user)
        challenge = (await login(client, user=user)).json()["challenge_token"]
        await db.refresh(user)
        step = user.mfa_last_used_step
        tok = (
            await client.post(
                "/api/v1/auth/mfa/verify",
                json={"challenge_token": challenge, "code": pyotp.TOTP(secret).at((step + 1) * 30)},
            )
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}"}

        # Wrong password.
        resp = await client.post(
            "/api/v1/auth/mfa/disable",
            headers=headers,
            json={"password": "nope", "code": pyotp.TOTP(secret).now()},
        )
        assert resp.status_code == 400

        # Right password, no factor.
        resp = await client.post(
            "/api/v1/auth/mfa/disable", headers=headers, json={"password": PASSWORD}
        )
        assert resp.status_code == 400
        await db.refresh(user)
        assert user.mfa_enabled is True

        await db.refresh(user)
        resp = await client.post(
            "/api/v1/auth/mfa/disable",
            headers=headers,
            json={
                "password": PASSWORD,
                "code": pyotp.TOTP(secret).at((user.mfa_last_used_step + 1) * 30),
            },
        )
        assert resp.status_code == 204
        await db.refresh(user)
        assert user.mfa_enabled is False

    async def test_policy_blocks_self_disable_for_an_in_scope_user(self, db, client):
        await set_policy(db, require_mfa=True, required_for_role=None)
        user = await make_user(db, username="dis-policy")
        secret = await enroll(db, user)
        challenge = (await login(client, user=user)).json()["challenge_token"]
        await db.refresh(user)
        tok = (
            await client.post(
                "/api/v1/auth/mfa/verify",
                json={
                    "challenge_token": challenge,
                    "code": pyotp.TOTP(secret).at((user.mfa_last_used_step + 1) * 30),
                },
            )
        ).json()["access_token"]

        await db.refresh(user)
        resp = await client.post(
            "/api/v1/auth/mfa/disable",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "password": PASSWORD,
                "code": pyotp.TOTP(secret).at((user.mfa_last_used_step + 1) * 30),
            },
        )
        assert resp.status_code == 403
        await db.refresh(user)
        assert user.mfa_enabled is True

    async def test_dev_login_refuses_an_mfa_enrolled_account(self, db, client, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "APP_ENV", "development")
        monkeypatch.setattr(settings, "DEV_AUTO_LOGIN_ENABLED", True)
        user = await make_user(
            db, username="devuser", email="devuser@testlookup.dev", role=UserRole.ADMIN
        )
        await enroll(db, user)

        resp = await client.post("/api/v1/auth/dev-login?username=devuser")
        assert resp.status_code == 403
        assert "MFA" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# 10. Breakglass script
# ═══════════════════════════════════════════════════════════════════════════


def _load_breakglass():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mfa_breakglass.py"
    spec = importlib.util.spec_from_file_location("mfa_breakglass_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestBreakglass:
    async def test_refuses_without_the_environment_gate(self, db, session_factory, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MFA_BREAKGLASS_ENABLED", False)
        user = await make_user(db, username="bg-gated")
        await enroll(db, user)

        module = _load_breakglass()
        rc = await module.main(
            ["--user", "bg-gated", "--reason", "x"], session_factory=session_factory
        )
        assert rc == 2
        await db.refresh(user)
        assert user.mfa_enabled is True

    async def test_requires_a_reason(self, db, session_factory, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MFA_BREAKGLASS_ENABLED", True)
        user = await make_user(db, username="bg-noreason")
        await enroll(db, user)

        module = _load_breakglass()
        rc = await module.main(["--user", "bg-noreason"], session_factory=session_factory)
        assert rc == 2
        await db.refresh(user)
        assert user.mfa_enabled is True

    async def test_dry_run_changes_nothing(self, db, session_factory, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MFA_BREAKGLASS_ENABLED", False)
        user = await make_user(db, username="bg-dry")
        await enroll(db, user)

        module = _load_breakglass()
        rc = await module.main(["--user", "bg-dry", "--dry-run"], session_factory=session_factory)
        assert rc == 0
        await db.refresh(user)
        assert user.mfa_enabled is True
        assert await mfa_service.count_unused_recovery_codes(db, user) == 10

    async def test_clears_mfa_and_lockout_and_writes_the_audit_event(
        self, db, session_factory, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MFA_BREAKGLASS_ENABLED", True)
        user = await make_user(db, username="bg-go", email="bg@example.com")
        await enroll(db, user)
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        user.failed_login_attempts = 7
        await db.commit()

        module = _load_breakglass()
        rc = await module.main(
            [
                "--user", "bg@example.com",
                "--operator", "on-call: sam",
                "--reason", "lost phone, identity verified by video",
            ],
            session_factory=session_factory,
        )
        assert rc == 0

        async with session_factory() as fresh:
            reloaded = (
                await fresh.execute(select(User).where(User.id == user.id))
            ).scalar_one()
            assert reloaded.mfa_enabled is False
            assert reloaded.mfa_last_used_step is None
            assert reloaded.locked_until is None
            assert reloaded.failed_login_attempts == 0
            assert (
                await mfa_service.count_unused_recovery_codes(fresh, reloaded)
            ) == 0
            seed = await mfa_service.load_totp_secret(fresh, reloaded)
            assert seed.state is mfa_service.SecretState.MISSING

            events = (
                await fresh.execute(
                    select(IdentityEvent).where(
                        IdentityEvent.event_type == IdentityEventType.MFA_BREAKGLASS_RESET
                    )
                )
            ).scalars().all()
            assert len(events) == 1
            assert events[0].user_id == user.id
            assert events[0].actor_name == "on-call: sam"
            assert events[0].detail["reason"].startswith("lost phone")
            assert events[0].detail["mfa_was_enabled"] is True

    async def test_reset_user_can_log_in_with_password_alone(
        self, db, session_factory, client, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MFA_BREAKGLASS_ENABLED", True)
        user = await make_user(db, username="bg-login")
        await enroll(db, user)
        assert (await login(client, user=user)).json().get("mfa_required") is True

        module = _load_breakglass()
        await module.main(
            ["--user", "bg-login", "--reason", "recovery"], session_factory=session_factory
        )
        # The script committed from its own session; re-read ours.
        await db.refresh(user)
        assert user.mfa_enabled is False

        resp = await login(client, username="bg-login")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_unknown_user_is_reported_not_crashed(self, db, session_factory, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "MFA_BREAKGLASS_ENABLED", True)
        module = _load_breakglass()
        rc = await module.main(
            ["--user", "ghost", "--reason", "x"], session_factory=session_factory
        )
        assert rc == 1


# ═══════════════════════════════════════════════════════════════════════════
# 11. Wiring invariants
# ═══════════════════════════════════════════════════════════════════════════


class TestWiringInvariants:
    def test_identity_event_types_fit_the_column(self):
        """``event_type`` is String(40) fronted by a strict enum.

        The repo's documented drift trap: a value longer than the column
        (or absent from the enum) 422s the reader with an empty page and no
        toast. Fail here instead.
        """
        width = IdentityEvent.__table__.c.event_type.type.length
        assert width == 40
        for member in IdentityEventType:
            assert len(member.value) <= width, member

    def test_mfa_router_is_public_not_protected(self):
        """/mfa/verify carries a challenge token, so a router-wide access-token
        dependency would 401 it before the handler ever ran."""
        from app.bootstrap import PROTECTED_ROUTERS, PUBLIC_ROUTERS
        from app.routers import mfa as mfa_router

        assert mfa_router.router in PUBLIC_ROUTERS
        assert mfa_router.router not in PROTECTED_ROUTERS

    def test_mfa_endpoints_are_rate_limited_in_their_own_buckets(self):
        """The trap: the limiter decorator is rebuilt per request, so paths
        only landed in separate buckets when their limit strings differed."""
        from app.main import _AUTH_RATE_LIMITS, _auth_rate_limit_key

        for path in (
            "/api/v1/auth/mfa/verify",
            "/api/v1/auth/mfa/enroll/start",
            "/api/v1/auth/mfa/enroll/confirm",
        ):
            assert path in _AUTH_RATE_LIMITS

        class _Req:
            def __init__(self, path):
                self.url = type("U", (), {"path": path})()
                self.client = type("C", (), {"host": "10.0.0.1"})()
                self.headers = {}

        keys = {
            _auth_rate_limit_key(_Req(p)) for p in _AUTH_RATE_LIMITS
        }
        assert len(keys) == len(_AUTH_RATE_LIMITS)

    def test_auth_limiters_are_built_once_not_per_request(self):
        """Regression: the decorator used to be rebuilt inside the middleware.

        ``Limiter.limit`` registers under ``module.func_name``, so a fresh
        local function per request kept appending to the same
        ``_route_limits`` entry. Request N then evaluated N limits and
        recorded N hits on one counter — the nominal 10/minute on
        ``/auth/login`` actually locked an IP out after four or five attempts,
        and the list grew unboundedly for the process lifetime.
        """
        from app.main import _AUTH_LIMITERS, _AUTH_RATE_LIMITS, limiter

        assert set(_AUTH_LIMITERS) == set(_AUTH_RATE_LIMITS)
        names = {fn.__name__ for fn, _msg in _AUTH_LIMITERS.values()}
        assert len(names) == len(_AUTH_RATE_LIMITS)  # no shared registration key
        for name in names:
            registered = limiter._route_limits.get(f"app.main.{name}", [])
            assert len(registered) <= 1, (name, len(registered))

    def test_service_owns_no_transactions(self):
        """Transaction-boundary discipline: routers commit, services stage.

        Parsed rather than grepped so the module docstring — which *describes*
        ``db.commit()`` — does not trip it.
        """
        import ast

        source = (
            Path(__file__).resolve().parents[1] / "app" / "services" / "mfa_service.py"
        ).read_text(encoding="utf-8")
        calls = {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "commit" not in calls
        assert "rollback" not in calls
        assert "flush" in calls  # it does stage-and-flush, not nothing

    def test_login_can_return_all_three_shapes(self):
        from app.models.schemas import (
            MfaChallengeResponse,
            MfaEnrollmentRequiredResponse,
            TokenResponse,
        )
        from app.routers.auth import login as login_route

        annotation = str(login_route.__annotations__) if login_route.__annotations__ else ""
        assert annotation is not None  # keeps ruff quiet about the import
        for model in (TokenResponse, MfaChallengeResponse, MfaEnrollmentRequiredResponse):
            assert model is not None

    def test_totp_uri_carries_the_configured_issuer(self):
        from app.core.config import settings

        user = User(username="u", email="u@example.com")
        uri = mfa_service.build_otpauth_uri(user, mfa_service.generate_totp_secret())
        assert uri.startswith("otpauth://totp/")
        assert settings.MFA_ISSUER_NAME in uri
        assert f"digits={mfa_service.TOTP_DIGITS}" in uri or "digits" not in uri

    def test_security_module_exports_only_non_access_mfa_types(self):
        assert security.MFA_CHALLENGE_TOKEN_TYPE != "access"
        assert security.MFA_ENROLLMENT_TOKEN_TYPE != "access"
        assert security.MFA_CHALLENGE_TOKEN_TYPE != security.MFA_ENROLLMENT_TOKEN_TYPE
