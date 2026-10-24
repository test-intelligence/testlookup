"""W11 REL-11 (D14): the deployment's Ed25519 compliance-pack signing key.

Runs the real ``compliance_signing`` and the real ``secret_service`` (Fernet
under ``APP_SECRET_KEY``) over an in-memory SQLite database holding the four
tables they touch. Pinned here:

* the first ``active_signing_key(create=True)`` creates ONE key row and stores
  the private key ONLY through ``secret_service.store_secret`` -- encrypted in
  ``secret_refs``, never in the keys table; a second call reuses it;
* ``create=False`` with no key answers ``None`` and writes nothing (a GET must
  not bootstrap a key); an unreadable secret answers ``None``;
* no private-key material in ``repr(SigningKey)`` or in any log record;
* ``rotate`` retires the old row (who, when, why), expires its secret (the
  ciphertext cleared: it never signs again), creates a new active key and
  stages one ``settings_audit_log`` row naming fields only -- and a statement
  signed with the OLD key still verifies with its kept public key.

The race between two first generations runs on PostgreSQL
(``tests/integration/test_compliance_pack_signing_postgres.py``).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone

import pytest
import structlog
from cryptography.hazmat.primitives import serialization
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import (
    ComplianceSigningKey,
    SecretRef,
    SettingsAuditLog,
    User,
    UserRole,
)
from app.services import compliance_signing as signing
from app.services import secret_service

_TABLES = [User.__table__, ComplianceSigningKey.__table__, SecretRef.__table__, SettingsAuditLog.__table__]
KEY_ID_RE = re.compile(r"^k-[0-9a-f]{16}$")


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # SQLAlchemy's documented pysqlite recipe: let SQLAlchemy emit BEGIN so a
    # SAVEPOINT (begin_nested) behaves as it does on PostgreSQL.
    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as conn:
        await conn.run_sync(User.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def admin(session_factory):
    async with session_factory() as db:
        user = User(
            id=uuid.uuid4(), email="ada@example.com", username="ada",
            hashed_password="x", role=UserRole.ADMIN.value,
        )
        db.add(user)
        await db.commit()
        return user


@pytest.fixture
def store_spy(monkeypatch):
    """Wrap the REAL store_secret: every call is recorded, then performed."""
    calls: list[tuple[str, str, str]] = []
    real = secret_service.store_secret

    async def _spy(db, scope, key_name, raw_value, actor_id=None):
        calls.append((scope, key_name, raw_value))
        return await real(db, scope, key_name, raw_value, actor_id)

    monkeypatch.setattr(secret_service, "store_secret", _spy)
    return calls


def _pem_body(pem: str) -> str:
    """The base64 body of a PEM, without its BEGIN / END lines."""
    return "".join(line for line in pem.strip().splitlines() if not line.startswith("-----"))


async def _rows(db, model):
    return list((await db.execute(select(model))).scalars().all())


async def test_the_first_signed_pack_creates_the_key_and_stores_its_private_half_encrypted(
    session_factory, admin, store_spy
):
    async with session_factory() as db:
        key = await signing.active_signing_key(db, actor=admin, create=True)
        await db.commit()

    assert key is not None and KEY_ID_RE.match(key.key_id)
    assert len(store_spy) == 1
    scope, key_name, private_pem = store_spy[0]
    assert (scope, key_name) == (signing.SIGNING_SECRET_SCOPE, key.key_id)
    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    # The stored secret IS the signing key (the public halves agree).
    loaded = serialization.load_pem_private_key(private_pem.encode(), password=None)
    assert signing.key_id_for(loaded.public_key()) == key.key_id

    async with session_factory() as db:
        (row,) = await _rows(db, ComplianceSigningKey)
        (secret,) = await _rows(db, SecretRef)
    assert row.key_id == key.key_id and row.retired_at is None
    assert row.created_by_user_id == admin.id
    assert row.public_key_pem == key.public_key_pem
    assert "PRIVATE KEY" not in row.public_key_pem
    assert _pem_body(private_pem) not in row.public_key_pem
    # Encrypted at rest: the ciphertext is not the PEM and holds none of it.
    assert secret.scope == signing.SIGNING_SECRET_SCOPE and secret.key_name == key.key_id
    assert secret.encrypted_value and _pem_body(private_pem) not in secret.encrypted_value
    assert "PRIVATE KEY" not in (secret.encrypted_value or "") + (secret.masked_value or "")


async def test_a_second_call_reuses_the_key(session_factory, admin, store_spy):
    async with session_factory() as db:
        first = await signing.active_signing_key(db, actor=admin, create=True)
        await db.commit()
    async with session_factory() as db:
        second = await signing.active_signing_key(db, actor=admin, create=True)
        await db.commit()
        assert len(await _rows(db, ComplianceSigningKey)) == 1
    assert first is not None and second is not None
    assert second.key_id == first.key_id
    assert len(store_spy) == 1, "the private key is stored once"


async def test_without_create_no_key_is_made_and_nothing_is_written(session_factory, admin, store_spy):
    async with session_factory() as db:
        assert await signing.active_signing_key(db, actor=admin, create=False) is None
        assert not db.new and not db.dirty
        await db.commit()
        assert await _rows(db, ComplianceSigningKey) == []
        assert await _rows(db, SecretRef) == []
    assert store_spy == []


async def test_an_unreadable_private_key_answers_none(session_factory, admin, monkeypatch):
    async with session_factory() as db:
        assert await signing.active_signing_key(db, actor=admin, create=True) is not None
        await db.commit()

    async def _gone(db, scope, key_name):  # an APP_SECRET_KEY changed without re-encryption
        return None

    monkeypatch.setattr(secret_service, "read_secret", _gone)
    async with session_factory() as db:
        assert await signing.active_signing_key(db, actor=admin, create=True) is None
        assert len(await _rows(db, ComplianceSigningKey)) == 1, "no second key is minted over an unreadable one"


async def test_a_secret_that_is_not_this_key_answers_none(session_factory, admin, monkeypatch):
    async with session_factory() as db:
        assert await signing.active_signing_key(db, actor=admin, create=True) is not None
        await db.commit()
    other = signing.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()

    async def _swapped(db, scope, key_name):
        return other

    monkeypatch.setattr(secret_service, "read_secret", _swapped)
    async with session_factory() as db:
        assert await signing.active_signing_key(db, actor=admin, create=False) is None


async def test_no_private_key_material_in_repr_or_logs(session_factory, admin, store_spy, caplog):
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        async with session_factory() as db:
            key = await signing.active_signing_key(db, actor=admin, create=True)
            await db.commit()
        async with session_factory() as db:
            rotated = await signing.rotate(db, actor=admin, reason="annual rotation")
            await db.commit()
    assert key is not None
    private_pems = [raw for _scope, _name, raw in store_spy]
    assert len(private_pems) == 2
    raw_private = key.private_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption(),
    )
    haystack = "\n".join(
        [repr(key), repr(rotated), str(key), caplog.text]
        + [repr(e) for e in events]
    )
    for pem in private_pems:
        assert _pem_body(pem) not in haystack
    assert "PRIVATE KEY" not in haystack
    assert raw_private.hex() not in haystack
    assert base64.b64encode(raw_private).decode() not in haystack
    assert "private_key" not in repr(key)
    assert {e.get("event") for e in events} >= {
        "compliance_signing_key_created", "compliance_signing_key_rotated",
    }


async def test_rotate_retires_the_old_key_destroys_its_secret_and_old_signatures_still_verify(
    session_factory, admin, store_spy
):
    pack_id = uuid.uuid4()
    digest = "ab" * 32
    async with session_factory() as db:
        old = await signing.active_signing_key(db, actor=admin, create=True)
        await db.commit()
    assert old is not None
    old_signature = signing.sign_statement(old, pack_id=pack_id, manifest_sha256=digest)

    async with session_factory() as db:
        new = await signing.rotate(db, actor=admin, reason="annual rotation")
        await db.commit()

    assert new.key_id != old.key_id
    async with session_factory() as db:
        rows = {r.key_id: r for r in await _rows(db, ComplianceSigningKey)}
        secrets = {s.key_name: s for s in await _rows(db, SecretRef)}
        audit = await _rows(db, SettingsAuditLog)
        keys = await signing.public_keys(db)
        active = await signing.active_signing_key(db, actor=admin, create=False)

    retired = rows[old.key_id]
    assert retired.retired_at is not None
    assert retired.retired_by_user_id == admin.id
    assert retired.retired_reason == "annual rotation"
    assert rows[new.key_id].retired_at is None
    # The retired private key is destroyed: cleared, expired, unreadable.
    assert secrets[old.key_id].encrypted_value is None
    assert secrets[old.key_id].rotation_status == "expired"
    assert secrets[new.key_id].rotation_status == "active"
    assert active is not None and active.key_id == new.key_id
    # One audit row, field names only.
    assert [(a.setting_key, a.action, a.changed_fields, a.actor_id) for a in audit] == [
        (signing.AUDIT_SETTING_KEY, "secret_rotated", ["key_id"], admin.id),
    ]
    # The old public key is kept, so a pack signed before the rotation verifies.
    assert set(keys) == {old.key_id, new.key_id}
    public = serialization.load_pem_public_key(keys[old.key_id].encode())
    public.verify(
        base64.b64decode(old_signature),
        signing.statement_bytes(pack_id=pack_id, manifest_sha256=digest, key_id=old.key_id),
    )


async def test_rotate_with_no_key_yet_creates_the_first(session_factory, admin, store_spy):
    async with session_factory() as db:
        key = await signing.rotate(db, actor=admin, reason="first key")
        await db.commit()
        rows = await _rows(db, ComplianceSigningKey)
    assert [r.key_id for r in rows] == [key.key_id]
    assert len(store_spy) == 1


def test_the_signed_statement_and_the_key_id():
    private = signing.Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key()
    key = signing.SigningKey(
        key_id=signing.key_id_for(public), public_key_pem=signing.public_key_pem(public), private_key=private,
    )
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert key.key_id == "k-" + hashlib.sha256(raw).hexdigest()[:16]
    pack_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    assert signing.statement_bytes(pack_id=pack_id, manifest_sha256="cd" * 32, key_id=key.key_id) == (
        f"testlookup-compliance-pack-v1\n{pack_id}\n{'cd' * 32}\n{key.key_id}\n".encode()
    )
    signature = signing.sign_statement(key, pack_id=pack_id, manifest_sha256="cd" * 32)
    public.verify(
        base64.b64decode(signature),
        signing.statement_bytes(pack_id=pack_id, manifest_sha256="cd" * 32, key_id=key.key_id),
    )
    doc = signing.signature_document(
        key, pack_id=pack_id, manifest_sha256="cd" * 32,
        signed_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    assert set(doc) == {
        "format_version", "algorithm", "statement", "pack_id", "manifest_sha256", "key_id", "signed_at", "signature",
    }
    assert (doc["algorithm"], doc["statement"], doc["format_version"]) == ("ed25519", "testlookup-compliance-pack-v1", 1)
