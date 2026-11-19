"""GAP-B02: refresh-token rotation, replay detection and family revocation, over HTTP.

``app/services/refresh_token_service.py`` is the only theft signal a session
has: each refresh rotates the token, and presenting an already-rotated token
means two parties hold the same session, so EVERY live refresh token of the
user is revoked. Nothing asserted it: ``/auth/refresh`` was called only for
the MFA policy (``test_mfa.py``) and subject erasure, so deleting the family
revocation, or the expiry check, left the suite green while a stolen refresh
token kept minting 7-day sessions.

The real routes (``/auth/login``, ``/auth/refresh``) and the real service,
on the same in-memory SQLite harness ``test_mfa.py`` uses (the auth tables
only; Postgres-only types elsewhere). Every refusal is the same 401 detail,
so a caller can't tell a replay from an unknown token.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("aiosqlite")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import func, select, types as sqltypes, update  # noqa: E402
from sqlalchemy.dialects.sqlite import base as _sqlite_types  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.security import (  # noqa: E402
    create_refresh_token,
    decode_token,
    get_password_hash,
    hash_token,
)
from app.db.postgres import Base, get_db  # noqa: E402
from app.models.postgres import (  # noqa: E402
    ApiKey,
    AppSetting,
    FederatedIdentity,
    IdentityEvent,
    MfaRecoveryCode,
    RefreshTokenRecord,
    SSOConfiguration,
    SecretRef,
    SettingsAuditLog,
    User,
    UserRole,
)

pytestmark = pytest.mark.asyncio

#: The auth surface's tables (as in test_mfa.py): most of the schema is Postgres-only.
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

PASSWORD = "ci-refresh-rotation-test-only"
REFUSED = "Invalid or expired refresh token"


class _AwareDateTime(_sqlite_types.DATETIME):
    """SQLite hands back naive datetimes; Postgres (and the service's comparisons) use aware ones."""

    def result_processor(self, dialect, coltype):
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def sessions():
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


@pytest.fixture(autouse=True)
def _redis(monkeypatch, fake_redis):
    """The token-revocation store fails closed (503) without Redis: use the in-memory fake."""
    import app.db.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: fake_redis)
    return fake_redis


@pytest.fixture(autouse=True)
def _session_issuer_clock(monkeypatch):
    """Access tokens read their ``iat`` from PostgreSQL's ``clock_timestamp()``; SQLite has none."""
    from app.services import auth_session_tokens

    async def _database_issued_at(_db):
        return datetime.now(timezone.utc)

    monkeypatch.setattr(auth_session_tokens, "_database_issued_at", _database_issued_at)


_hosts = itertools.count(1)


@pytest.fixture
async def client(sessions):
    """The real app on the SQLite session; its own client address (the login limiter is per address)."""
    from app.main import app, limiter

    try:
        limiter.reset()
    except Exception:  # noqa: BLE001 -- best-effort hygiene, as in test_mfa.py
        pass

    async def _override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    n = next(_hosts)
    transport = ASGITransport(
        app=app, client=(f"10.77.{n % 250}.{(n // 250) % 250 + 1}", 5000)
    )
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


async def _user(sessions, username: str) -> User:
    async with sessions() as db:
        user = User(
            email=f"{username}@example.test",
            username=username,
            hashed_password=get_password_hash(PASSWORD),
            role=UserRole.QA_ENGINEER.value,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def _login(client, user: User) -> str:
    resp = await client.post(
        "/api/v1/auth/login", data={"username": user.username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["refresh_token"]


async def _refresh(client, token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def _record(sessions, token: str) -> RefreshTokenRecord:
    jti = decode_token(token, expected_type="refresh")["jti"]
    async with sessions() as db:
        return (
            await db.execute(
                select(RefreshTokenRecord).where(
                    RefreshTokenRecord.jti_hash == hash_token(jti)
                )
            )
        ).scalar_one()


async def _records(sessions, user: User) -> list[RefreshTokenRecord]:
    async with sessions() as db:
        return list(
            (
                await db.execute(
                    select(RefreshTokenRecord).where(
                        RefreshTokenRecord.user_id == user.id
                    )
                )
            ).scalars()
        )


def _assert_refused(resp) -> None:
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == REFUSED


async def test_a_refresh_rotates_the_token_and_spends_the_old_one(client, sessions):
    user = await _user(sessions, "rot-alice")
    r1 = await _login(client, user)

    resp = await _refresh(client, r1)

    assert resp.status_code == 200, resp.text
    r2 = resp.json()["refresh_token"]
    assert r2 != r1 and resp.json()["access_token"]
    old, new = await _record(sessions, r1), await _record(sessions, r2)
    assert old.rotated_to_id == new.id
    assert old.revoked_at is not None and old.replay_detected is False
    assert new.revoked_at is None and new.rotated_to_id is None


async def test_replaying_a_rotated_token_revokes_every_refresh_token_of_the_user(
    client, sessions
):
    user = await _user(sessions, "rot-bob")
    stolen = await _login(client, user)
    # A second browser: same user, same family.
    other_session = await _login(client, user)
    rotated = (await _refresh(client, stolen)).json()["refresh_token"]

    _assert_refused(await _refresh(client, stolen))  # the thief (or the victim) replays

    # The family is dead: the legitimate successor and the other session are refused too.
    _assert_refused(await _refresh(client, rotated))
    _assert_refused(await _refresh(client, other_session))
    records = await _records(sessions, user)
    assert len(records) == 3, records  # no token was minted by any refused call
    assert all(r.revoked_at is not None for r in records), [
        (r.id, r.revoked_at) for r in records
    ]


async def test_a_replay_revokes_only_that_users_tokens(client, sessions):
    victim, bystander = (
        await _user(sessions, "rot-carol"),
        await _user(sessions, "rot-dave"),
    )
    stolen = await _login(client, victim)
    bystander_token = await _login(client, bystander)
    await _refresh(client, stolen)

    _assert_refused(await _refresh(client, stolen))

    resp = await _refresh(client, bystander_token)
    assert resp.status_code == 200, resp.text


async def test_an_expired_record_is_refused_and_nothing_rotates(client, sessions):
    user = await _user(sessions, "rot-erin")
    token = await _login(client, user)
    record = await _record(sessions, token)
    async with sessions() as db:
        await db.execute(
            update(RefreshTokenRecord)
            .where(RefreshTokenRecord.id == record.id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await db.commit()

    # Refused on the record's expiry: the JWT's own exp is still days away.
    _assert_refused(await _refresh(client, token))

    after = await _record(sessions, token)
    assert after.rotated_to_id is None and after.revoked_at is None
    assert len(await _records(sessions, user)) == 1


async def test_a_token_naming_another_user_is_refused_and_nothing_rotates(
    client, sessions
):
    alice, mallory = (
        await _user(sessions, "rot-frank"),
        await _user(sessions, "rot-mallory"),
    )
    token = await _login(client, alice)
    jti = decode_token(token, expected_type="refresh")["jti"]
    # Alice's live jti under Mallory's subject (signed with the server key, as a forger would need).
    forged, _jti, _exp = create_refresh_token(str(mallory.id), jti=jti)

    _assert_refused(await _refresh(client, forged))

    record = await _record(sessions, token)
    assert (
        record.user_id == alice.id
        and record.rotated_to_id is None
        and record.revoked_at is None
    )
    async with sessions() as db:
        total = (
            await db.execute(select(func.count()).select_from(RefreshTokenRecord))
        ).scalar_one()
    assert total == 1


async def test_an_unknown_jti_is_refused(client, sessions):
    user = await _user(sessions, "rot-grace")
    await _login(client, user)
    # Well signed, never issued.
    unknown, _jti, _exp = create_refresh_token(str(user.id))

    _assert_refused(await _refresh(client, unknown))
    assert len(await _records(sessions, user)) == 1
