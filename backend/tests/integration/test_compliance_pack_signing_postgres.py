"""W11 REL-11 on real PostgreSQL: the first-key race, and a rotation.

* Two concurrent FIRST signed generations create exactly ONE key: both pass
  the unlocked read with no key, then ``pg_advisory_xact_lock`` serialises
  them and the second re-reads the first's committed key. Without the lock,
  the partial unique index refuses the second insert and the loser falls
  back to the winner's key (its SAVEPOINT rolled back, its transaction alive).
* A rotation then a verification of a pack signed by the OLDER key ->
  ``valid`` (the retired public key is kept); the retired private key is
  destroyed (ciphertext cleared, ``expired``).

The real ``compliance_signing`` and ``secret_service`` run against SCRATCH
copies of ``compliance_signing_keys`` (``LIKE ... INCLUDING ALL``: the partial
unique index comes with it), ``secret_refs`` and ``settings_audit_log`` --
reached through a dedicated engine whose ``search_path`` puts the scratch
schema first -- so the shared tables are never written.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

from app.services import compliance_pack_verify as verify  # noqa: E402
from app.services import compliance_signing as signing  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TABLES = ("compliance_signing_keys", "secret_refs", "settings_audit_log")


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def factory():
    admin = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    scratch = f"w11a_sig_{uuid.uuid4().hex[:10]}"
    async with admin.connect() as conn:
        base = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
        await conn.execute(text(f'CREATE SCHEMA "{scratch}"'))
        for table in TABLES:
            await conn.execute(text(f'CREATE TABLE "{scratch}".{table} (LIKE "{base}".{table} INCLUDING ALL)'))
        await conn.commit()
    engine = create_async_engine(
        _dsn(), pool_size=4, max_overflow=0,
        connect_args={"server_settings": {"search_path": f"{scratch},{base}"}},
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{scratch}" CASCADE'))
            await conn.commit()
        await admin.dispose()


async def _count(factory, sql: str) -> int:
    async with factory() as db:
        return int((await db.execute(text(sql))).scalar_one())


def _barrier(monkeypatch, *, parties: int, lock: bool) -> list[int]:
    """Both callers reach ``_lock`` (each saw no key) before either goes on.

    Returns a list that grows by one per key generation attempted (``_create``):
    with the lock the second caller re-reads under it and never generates.
    """
    arrived = 0
    gate = asyncio.Event()
    real_lock = signing._lock
    real_create = signing._create
    creates: list[int] = []

    async def _together(db):
        nonlocal arrived
        arrived += 1
        if arrived == parties:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=10)
        if lock:
            await real_lock(db)

    async def _counted_create(db, *, actor):
        creates.append(1)
        return await real_create(db, actor=actor)

    monkeypatch.setattr(signing, "_lock", _together)
    monkeypatch.setattr(signing, "_create", _counted_create)
    return creates


async def _first_generation(factory):
    async with factory() as db:
        key = await signing.active_signing_key(db, actor=None, create=True)
        await db.commit()
        return key


async def test_two_concurrent_first_generations_create_one_key(factory, monkeypatch):
    creates = _barrier(monkeypatch, parties=2, lock=True)
    first, second = await asyncio.wait_for(
        asyncio.gather(_first_generation(factory), _first_generation(factory)), timeout=30,
    )
    assert first is not None and second is not None
    assert first.key_id == second.key_id
    assert len(creates) == 1, "the second caller re-read under the lock and generated nothing"
    assert await _count(factory, "SELECT count(*) FROM compliance_signing_keys") == 1
    assert await _count(factory, "SELECT count(*) FROM secret_refs WHERE scope = 'compliance_signing'") == 1


async def test_without_the_lock_the_partial_unique_index_backstops_the_race(factory, monkeypatch):
    creates = _barrier(monkeypatch, parties=2, lock=False)
    first, second = await asyncio.wait_for(
        asyncio.gather(_first_generation(factory), _first_generation(factory)), timeout=30,
    )
    assert first is not None and second is not None
    assert first.key_id == second.key_id, "the loser uses the winner's key"
    assert len(creates) == 2, "both generated; the index refused the second insert"
    assert await _count(factory, "SELECT count(*) FROM compliance_signing_keys WHERE retired_at IS NULL") == 1
    assert await _count(factory, "SELECT count(*) FROM secret_refs WHERE scope = 'compliance_signing'") == 1


def _signed_pack(key: signing.SigningKey, pack_id: uuid.UUID) -> tuple[bytes, str, str]:
    files = {"decision.json": b'{"recommendation": "GO"}', "README.md": b"# pack"}
    manifest = json.dumps({"files": [
        {"name": n, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in sorted(files.items())
    ]}).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    doc = signing.signature_document(
        key, pack_id=pack_id, manifest_sha256=digest,
        signed_at=datetime.now(timezone.utc),
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", manifest)
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("signature.json", json.dumps(doc).encode())
    return buffer.getvalue(), digest, doc["signature"]


async def test_a_pack_signed_before_a_rotation_still_verifies(factory):
    old = await _first_generation(factory)
    assert old is not None
    pack_id = uuid.uuid4()
    blob, digest, signature = _signed_pack(old, pack_id)

    async with factory() as db:
        new = await signing.rotate(db, actor=None, reason="annual rotation")
        await db.commit()
    async with factory() as db:
        keys = await signing.public_keys(db)
        active = await signing.active_signing_key(db, actor=None, create=False)
        retired_secret = (await db.execute(text(
            "SELECT encrypted_value, rotation_status FROM secret_refs WHERE key_name = :k"
        ), {"k": old.key_id})).one()
        audit = (await db.execute(text(
            "SELECT setting_key, action FROM settings_audit_log WHERE setting_key = 'compliance_signing_key'"
        ))).all()

    assert new.key_id != old.key_id and active is not None and active.key_id == new.key_id
    assert set(keys) == {old.key_id, new.key_id}
    assert tuple(retired_secret) == (None, "expired"), "the retired private key is destroyed"
    assert [tuple(a) for a in audit] == [("compliance_signing_key", "secret_rotated")]
    result = verify.verify_pack_bytes(
        blob, recorded_manifest_sha256=digest, public_keys=keys, pack_id=pack_id,
        record_signature=signature, record_signing_key_id=old.key_id,
    )
    assert (result["result"], result["signature"], result["signing_key_id"]) == ("verified", "valid", old.key_id)
