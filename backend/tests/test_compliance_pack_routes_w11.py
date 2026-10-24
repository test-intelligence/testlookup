"""W11 REL-11: the four new compliance-pack routes.

* ``POST /compliance-packs/{pack_id}/verify`` -- another project's pack is a
  403 before any key or storage read; the request's connection is handed back
  BEFORE the storage read; a storage failure answers 200 ``unreadable``; the
  metric counts ``{result, signature}``; unknown -> 404.
* ``GET /compliance-packs/signing-keys`` -- lists the public halves and
  writes nothing (every statement it runs is a SELECT; no key is created).
* ``GET /compliance-packs/signing-keys/{signing_key_id}/pem`` -- an id that is
  not ``k-<16 hex>`` is a 404 with no statement; the PEM is an attachment.
* ``POST /compliance-packs/signing-keys/rotate`` -- a QA lead and a
  project-bound ADMIN key are refused (the default refusal, no opt-in); a
  wrong confirmation is a 422; a rotation commits ONCE and stages one
  ``settings_audit_log`` row.

The verify cases call the handler over fakes; the key routes run through the
real router (dependencies included) on an in-memory SQLite database holding
the tables the signing service touches.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import metrics
from app.core.deps import (
    CREDENTIAL_KIND_API_KEY,
    PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
    get_db,
)
from app.models.postgres import ComplianceSigningKey, SecretRef, SettingsAuditLog, User, UserRole
from app.routers import compliance_packs as routes
from app.services import compliance_pack_service as svc
from app.services import compliance_signing as signing

PROJECT = uuid.uuid4()
OTHER = uuid.uuid4()


def _user(role=UserRole.ADMIN, *, bound_to=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role.value, is_active=True, username="lee", email="lee@x.test")
    if bound_to is not None:
        _bind_credential_kind(_bind_api_key_project(user, bound_to), CREDENTIAL_KIND_API_KEY)
    return user


def _pack(project_id=PROJECT, **extra):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, release_id=uuid.uuid4(), minio_key="compliance/x.zip",
        manifest_sha256="ab" * 32, signature=extra.get("signature"), signing_key_id=extra.get("signing_key_id"),
    )


class _ReadSession:
    """What the verify handler's session needs: nothing staged, close() counted."""

    new = dirty = deleted = ()

    def __init__(self, order):
        self.order = order

    async def close(self):
        self.order.append("release_connection")


# ── verify ───────────────────────────────────────────────────────────────────


async def test_verify_refuses_another_projects_pack_before_any_key_or_storage_read(monkeypatch):
    pack = _pack(project_id=OTHER)
    monkeypatch.setattr(svc, "get_pack", AsyncMock(return_value=pack))
    load = AsyncMock(return_value=b"")
    keys = AsyncMock(return_value={})
    monkeypatch.setattr(svc, "load_pack_bytes", load)
    monkeypatch.setattr(signing, "public_keys", keys)

    with pytest.raises(HTTPException) as exc:
        await routes.verify_compliance_pack(
            pack_id=pack.id, current_user=_user(bound_to=PROJECT), db=_ReadSession([]),
        )
    assert exc.value.status_code == 403
    load.assert_not_awaited()
    keys.assert_not_awaited()


async def test_verify_of_an_unknown_pack_is_a_404(monkeypatch):
    monkeypatch.setattr(svc, "get_pack", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await routes.verify_compliance_pack(pack_id=uuid.uuid4(), current_user=_user(), db=_ReadSession([]))
    assert (exc.value.status_code, exc.value.detail) == (404, "Compliance pack not found")


async def test_a_storage_failure_is_a_200_unreadable_after_the_connection_is_handed_back(monkeypatch):
    order: list[str] = []
    pack = _pack(signing_key_id="k-0123456789abcdef", signature="c2ln")
    monkeypatch.setattr(svc, "get_pack", AsyncMock(return_value=pack))

    async def _keys(db):
        order.append("public_keys")
        return {}

    async def _load(p):
        order.append("load_pack_bytes")
        raise ConnectionError("minio down")

    monkeypatch.setattr(signing, "public_keys", _keys)
    monkeypatch.setattr(svc, "load_pack_bytes", _load)
    before = metrics.compliance_pack_verifications_total.labels(result="unreadable", signature="unknown_key")._value.get()

    result = await routes.verify_compliance_pack(
        pack_id=pack.id, current_user=_user(bound_to=PROJECT), db=_ReadSession(order),
    )

    assert order == ["public_keys", "release_connection", "load_pack_bytes"]
    assert (result.result, result.summary) == ("unreadable", "Could not read the pack from storage")
    assert result.pack_id == pack.id and result.files == []
    after = metrics.compliance_pack_verifications_total.labels(result="unreadable", signature="unknown_key")._value.get()
    assert after == before + 1


async def test_verify_checks_the_stored_bytes_and_counts_the_result(monkeypatch):
    from tests.test_compliance_pack_verify import _key, _zip

    key = _key(3)
    files = {"decision.json": b'{"recommendation": "GO"}'}
    manifest = json.dumps({"files": [
        {"name": n, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in files.items()
    ]}).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    pack = _pack()
    pack.manifest_sha256 = digest
    doc = {
        "format_version": 1, "algorithm": "ed25519", "statement": "testlookup-compliance-pack-v1",
        "pack_id": str(pack.id), "manifest_sha256": digest, "key_id": key.key_id, "signed_at": "2026-10-23T00:00:00+00:00",
        "signature": signing.sign_statement(key, pack_id=pack.id, manifest_sha256=digest),
    }
    pack.signature, pack.signing_key_id = doc["signature"], key.key_id
    blob = _zip([("manifest.json", manifest), *files.items(), ("signature.json", json.dumps(doc).encode())])
    monkeypatch.setattr(svc, "get_pack", AsyncMock(return_value=pack))
    monkeypatch.setattr(svc, "load_pack_bytes", AsyncMock(return_value=blob))
    monkeypatch.setattr(signing, "public_keys", AsyncMock(return_value={key.key_id: key.public_key_pem}))
    before = metrics.compliance_pack_verifications_total.labels(result="verified", signature="valid")._value.get()

    result = await routes.verify_compliance_pack(pack_id=pack.id, current_user=_user(), db=_ReadSession([]))

    assert (result.result, result.signature, result.chain) == ("verified", "valid", "intact")
    assert result.summary == f"Verified · 1 files · signed by key {key.key_id}"
    after = metrics.compliance_pack_verifications_total.labels(result="verified", signature="valid")._value.get()
    assert after == before + 1


# ── the key routes, through the real router ─────────────────────────────────


@pytest.fixture
async def sqlite():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    tables = [User.__table__, ComplianceSigningKey.__table__, SecretRef.__table__, SettingsAuditLog.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(User.metadata.create_all, tables=tables)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    statements.clear()
    yield SimpleNamespace(factory=factory, statements=statements)
    await engine.dispose()


def _client(sqlite, user, commits: list[int]):
    app = FastAPI()
    app.include_router(routes.router)

    async def _db():
        async with sqlite.factory() as session:
            real_commit = session.commit

            async def _counted():
                commits.append(1)
                await real_commit()

            session.commit = _counted  # type: ignore[method-assign]
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _seed_key(sqlite):
    async with sqlite.factory() as db:
        key = await signing.active_signing_key(db, actor=None, create=True)
        await db.commit()
    sqlite.statements.clear()
    return key


async def test_the_signing_keys_read_writes_nothing_and_creates_no_key(sqlite):
    async with _client(sqlite, _user(UserRole.VIEWER), []) as client:
        empty = await client.get("/api/v1/compliance-packs/signing-keys")
    assert empty.status_code == 200
    assert empty.json() == {"active_key_id": None, "keys": []}
    assert sqlite.statements and all(s.lstrip().upper().startswith(("SELECT", "BEGIN", "COMMIT", "ROLLBACK"))
                                     for s in sqlite.statements), sqlite.statements

    key = await _seed_key(sqlite)
    async with _client(sqlite, _user(UserRole.VIEWER), []) as client:
        listed = (await client.get("/api/v1/compliance-packs/signing-keys")).json()
    assert listed["active_key_id"] == key.key_id
    assert [(k["key_id"], k["active"], k["algorithm"]) for k in listed["keys"]] == [(key.key_id, True, "ed25519")]
    assert "PRIVATE" not in listed["keys"][0]["public_key_pem"]
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sqlite.statements)


@pytest.mark.parametrize("bad", ["k-XYZ", "k-0123456789abcde", "key_1", "k-0123456789ABCDEF"])
async def test_a_pem_id_that_is_not_a_key_id_is_a_404_without_a_statement(sqlite, bad):
    async with _client(sqlite, _user(UserRole.VIEWER), []) as client:
        response = await client.get(f"/api/v1/compliance-packs/signing-keys/{bad}/pem")
    assert (response.status_code, response.json()["detail"]) == (404, "Signing key not found")
    assert not any("compliance_signing_keys" in s for s in sqlite.statements)


async def test_the_public_key_downloads_as_a_pem_attachment(sqlite):
    key = await _seed_key(sqlite)
    async with _client(sqlite, _user(UserRole.VIEWER), []) as client:
        found = await client.get(f"/api/v1/compliance-packs/signing-keys/{key.key_id}/pem")
        missing = await client.get("/api/v1/compliance-packs/signing-keys/k-0000000000000000/pem")
    assert found.status_code == 200
    assert found.headers["content-type"].startswith("application/x-pem-file")
    assert found.headers["content-disposition"] == f'attachment; filename="testlookup-compliance-{key.key_id}.pem"'
    assert found.text == key.public_key_pem and "PRIVATE" not in found.text
    assert missing.status_code == 404


async def test_rotate_refuses_a_qa_lead_and_a_project_bound_admin_key(sqlite):
    body = {"confirmation": "rotate", "reason": "annual rotation"}
    async with _client(sqlite, _user(UserRole.QA_LEAD), []) as client:
        lead = await client.post("/api/v1/compliance-packs/signing-keys/rotate", json=body)
    async with _client(sqlite, _user(UserRole.ADMIN, bound_to=PROJECT), []) as client:
        bound = await client.post("/api/v1/compliance-packs/signing-keys/rotate", json=body)
    assert lead.status_code == 403
    assert (bound.status_code, bound.json()["detail"]) == (403, PROJECT_KEY_NOT_INSTANCE_ADMIN_DETAIL)
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE")) for s in sqlite.statements)


async def test_rotate_needs_the_typed_confirmation_and_a_reason(sqlite):
    async with _client(sqlite, _user(UserRole.ADMIN), []) as client:
        wrong = await client.post("/api/v1/compliance-packs/signing-keys/rotate",
                                  json={"confirmation": "yes", "reason": "annual rotation"})
        short = await client.post("/api/v1/compliance-packs/signing-keys/rotate",
                                  json={"confirmation": "rotate", "reason": "x"})
    assert (wrong.status_code, wrong.json()["detail"]) == (422, "Type rotate to confirm.")
    assert short.status_code == 422


async def test_a_rotation_commits_once_and_stages_one_audit_row(sqlite):
    old = await _seed_key(sqlite)
    admin = _user(UserRole.ADMIN)
    commits: list[int] = []
    async with _client(sqlite, admin, commits) as client:
        response = await client.post("/api/v1/compliance-packs/signing-keys/rotate",
                                     json={"confirmation": "rotate", "reason": "annual rotation"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"] is True and body["key_id"] != old.key_id and body["retired_at"] is None
    assert len(commits) == 1
    async with sqlite.factory() as db:
        audit = list((await db.execute(select(SettingsAuditLog))).scalars().all())
        rows = {r.key_id: r for r in (await db.execute(select(ComplianceSigningKey))).scalars().all()}
    assert [(a.setting_key, a.action, a.changed_fields) for a in audit] == [
        ("compliance_signing_key", "secret_rotated", ["key_id"]),
    ]
    assert rows[old.key_id].retired_reason == "annual rotation"
    assert rows[body["key_id"]].retired_at is None


async def test_the_static_key_paths_are_not_captured_as_a_pack_id(sqlite):
    """``/signing-keys`` is declared before every ``/{pack_id}`` route."""
    paths = [r.path for r in routes.router.routes]
    first_pack_route = min(i for i, p in enumerate(paths) if "{pack_id}" in p)
    for static in ("/api/v1/compliance-packs/signing-keys",
                   "/api/v1/compliance-packs/signing-keys/{signing_key_id}/pem",
                   "/api/v1/compliance-packs/signing-keys/rotate"):
        assert paths.index(static) < first_pack_route, static
