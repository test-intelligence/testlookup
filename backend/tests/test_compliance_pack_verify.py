"""W11 REL-11: a signed pack verifies, and every way of changing it is caught.

The packs are built by the REAL path -- ``compliance_pack_service.generate_pack``
(``_build_pack_payload``, ``_sign_pack``) with the section gatherers, the
storage provider and the key lookup replaced by fakes -- and checked by the
REAL ``compliance_pack_verify.verify_pack_bytes`` on the server path (the row's
digest, signature and key id). Pinned:

* a fresh pack: ``verified`` + ``valid``, 12 files, the panel's sentence;
* one byte changed in ``decision.json`` (re-zipped, or flipped in place) ->
  ``tampered``, that file ``mismatch``, the summary names it;
* the manifest edited -> ``manifest_matches_record`` False; an extra file ->
  ``unexpected``; a duplicated member -> ``unexpected``;
* ``signature.json`` replaced by a signature over another manifest, or
  removed while the row says signed, or by a key the table does not hold ->
  ``invalid`` / ``invalid`` / ``unknown_key``;
* a pre-0225 pack (no signature, row unsigned) -> ``verified`` + ``unsigned``;
  a non-ZIP -> ``unreadable``;
* §0.27: ``manifest.json``'s bytes and digest are identical with and without
  signing, ``signature.json`` is not listed, ``file_count`` is unchanged;
* an unusable key: the pack is generated UNSIGNED, says so in its snapshot,
  counts the failure and logs no key material.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import metrics
from app.services import compliance_pack_service as svc
from app.services import compliance_pack_verify as verify
from app.services import compliance_signing as signing

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
RELEASE_ID = uuid.uuid4()
LISTED = 12  # README, release, run, decision, release_gate_decision, policy_snapshot, decision_trail,
#              clusters, defects, audit_events, agent_activity, ai_history


def _key(seed: int = 7) -> signing.SigningKey:
    private = Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
    public = private.public_key()
    return signing.SigningKey(
        key_id=signing.key_id_for(public), public_key_pem=signing.public_key_pem(public), private_key=private,
    )


class _DB:
    """The generate path's session: rows are staged, nothing commits."""

    def __init__(self):
        self.added: list = []
        self.commits = 0

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):  # pragma: no cover - asserted never called
        self.commits += 1

    def begin_nested(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _savepoint():
            yield

        return _savepoint()


class _Storage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, *, key, content, content_type, bucket):
        self.objects[key] = content


@pytest.fixture
def world(monkeypatch):
    """Every section stubbed; the real payload, signing and verification run."""
    return install_world(monkeypatch)


def install_world(monkeypatch):
    """The ``world`` fixture's body (``tests/test_compliance_pack_cli_parity.py`` reuses it)."""
    run = SimpleNamespace(id=RUN_ID, build_number="1234", project_id=PROJECT_ID)
    decision = SimpleNamespace(recommendation="GO", risk_score=12, original_recommendation=None, policy_id=None)

    async def _decision_and_run(db, release):
        return decision, run

    async def _section(*_args, **_kwargs):
        return {"section": "stub"}

    async def _enabled(*_args, **_kwargs):
        return True

    monkeypatch.setattr(svc, "_gather_decision_and_run", _decision_and_run)
    for name in ("_gather_release_snapshot", "_gather_policy_snapshot", "_gather_release_gate",
                 "_gather_decision_trail", "_gather_clusters", "_gather_defects", "_gather_audit_events",
                 "_gather_agent_activity"):
        monkeypatch.setattr(svc, name, _section)

    async def _history(db, run):
        return {"available": False, "error": "ai_history_unavailable"}

    monkeypatch.setattr(svc, "_gather_ai_history", _history)
    monkeypatch.setattr(svc, "_feature_enabled", _enabled)
    storage = _Storage()
    import app.db.storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: storage)
    state = SimpleNamespace(storage=storage, key=_key(), key_calls=[])

    async def _active(db, *, actor, create):
        state.key_calls.append(create)
        return state.key

    monkeypatch.setattr(signing, "active_signing_key", _active)
    state.release = SimpleNamespace(id=RELEASE_ID, project_id=PROJECT_ID, name="2026.10", version="2026.10.0")
    return state


async def _generate(world):
    db = _DB()
    row = await svc.generate_pack(db, world.release, actor=SimpleNamespace(id=uuid.uuid4(), username="lee"))
    (stored,) = world.storage.objects.values()
    assert db.commits == 0
    return row, stored, db


def _members(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return [(info.filename, zf.read(info)) for info in zf.infolist()]


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buffer.getvalue()


def _rewrite(zip_bytes: bytes, **changes: bytes | None) -> bytes:
    """Re-zip with members replaced (bytes) or removed (None)."""
    out = []
    for name, data in _members(zip_bytes):
        key = name.replace(".", "_")
        if key in changes:
            if changes[key] is None:
                continue
            data = changes[key]
        out.append((name, data))
    return _zip(out)


def _check(row, zip_bytes, keys=None, **record):
    return verify.verify_pack_bytes(
        zip_bytes,
        recorded_manifest_sha256=record.get("manifest_sha256", row.manifest_sha256),
        public_keys=keys if keys is not None else {},
        pack_id=row.id,
        record_signature=record.get("signature", row.signature),
        record_signing_key_id=record.get("signing_key_id", row.signing_key_id),
    )


def _status(result) -> dict[str, str]:
    return {f["name"]: f["status"] for f in result["files"]}


async def test_a_fresh_signed_pack_verifies(world):
    row, stored, db = await _generate(world)
    keys = {world.key.key_id: world.key.public_key_pem}
    result = _check(row, stored, keys)

    assert (result["result"], result["chain"], result["signature"]) == ("verified", "intact", "valid")
    assert result["manifest_matches_record"] is True
    assert result["signing_key_id"] == world.key.key_id == row.signing_key_id
    assert result["files_checked"] == result["files_ok"] == LISTED
    assert set(_status(result).values()) == {"ok"}
    assert result["summary"] == f"Verified · {LISTED} files · signed by key {world.key.key_id}"
    assert world.key_calls == [True], "the first pack may create the key"
    assert row.metadata_snapshot["signing"] == {"state": "signed", "key_id": world.key.key_id}
    assert row.bytes == len(stored) == row.metadata_snapshot["bytes"]
    assert row.file_count == LISTED + 1, "listed files + the manifest; signature.json is not counted"


async def test_one_changed_byte_in_a_file_is_named(world):
    row, stored, _ = await _generate(world)
    keys = {world.key.key_id: world.key.public_key_pem}
    original = dict(_members(stored))["decision.json"]
    changed = original.replace(b"risk_score=12", b"risk_score=99", 1)
    assert changed != original and len(changed) == len(original)

    result = _check(row, _rewrite(stored, decision_json=changed), keys)
    assert (result["result"], result["chain"]) == ("tampered", "broken")
    assert _status(result)["decision.json"] == "mismatch"
    assert result["signature"] == "valid", "the manifest is intact: only the file moved"
    assert result["summary"] == "Tampered: decision.json does not match the manifest"
    assert result["files_ok"] == LISTED - 1


async def test_a_byte_flipped_in_place_is_a_mismatch_not_a_crash(world):
    """Editing the stored ZIP's bytes directly breaks the member's CRC."""
    row, stored, _ = await _generate(world)
    with zipfile.ZipFile(io.BytesIO(stored)) as zf:
        info = zf.getinfo("decision.json")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    flipped = bytearray(stored)
    flipped[start + 2] ^= 0xFF
    result = _check(row, bytes(flipped), {world.key.key_id: world.key.public_key_pem})
    assert result["result"] == "tampered"
    assert _status(result)["decision.json"] == "mismatch"


async def test_an_edited_manifest_does_not_match_the_recorded_digest(world):
    row, stored, _ = await _generate(world)
    manifest = json.loads(dict(_members(stored))["manifest.json"])
    manifest["recommendation"] = "NO_GO"
    edited = json.dumps(manifest, indent=2, sort_keys=True).encode()
    result = _check(row, _rewrite(stored, manifest_json=edited), {world.key.key_id: world.key.public_key_pem})

    assert result["manifest_matches_record"] is False
    assert result["result"] == "tampered"
    assert result["signature"] == "invalid", "signature.json signed the original manifest's digest"
    assert result["summary"] == (
        "Tampered: the manifest does not match the digest recorded when the pack was generated"
    )


@pytest.mark.filterwarnings("ignore:Duplicate name")
async def test_an_extra_file_and_a_duplicated_member_are_unexpected(world):
    row, stored, _ = await _generate(world)
    keys = {world.key.key_id: world.key.public_key_pem}
    members = _members(stored)
    extra = _check(row, _zip(members + [("notes.txt", b"added later")]), keys)
    assert _status(extra)["notes.txt"] == "unexpected"
    assert (extra["result"], extra["chain"]) == ("tampered", "broken")
    assert extra["summary"] == "Tampered: notes.txt is not listed in the manifest"

    decision = dict(members)["decision.json"]
    duplicated = _check(row, _zip([("decision.json", b'{"forged": true}')] + members), keys)
    assert [f["status"] for f in duplicated["files"] if f["name"] == "decision.json"] == ["ok", "unexpected"]
    assert duplicated["result"] == "tampered"
    assert dict(_members(stored))["decision.json"] == decision


async def test_a_signature_over_another_manifest_is_invalid(world):
    row, stored, _ = await _generate(world)
    doc = json.loads(dict(_members(stored))["signature.json"])
    doc["signature"] = signing.sign_statement(world.key, pack_id=row.id, manifest_sha256="00" * 32)
    forged = _rewrite(stored, signature_json=json.dumps(doc).encode())
    result = _check(row, forged, {world.key.key_id: world.key.public_key_pem}, signature=doc["signature"])
    assert result["signature"] == "invalid"
    assert result["result"] == "tampered"
    assert result["summary"] == "Tampered: the signature does not match this pack"


async def test_a_removed_signature_file_is_invalid_when_the_row_says_signed(world):
    row, stored, _ = await _generate(world)
    stripped = _rewrite(stored, signature_json=None)
    result = _check(row, stripped, {world.key.key_id: world.key.public_key_pem})
    assert (result["signature"], result["result"]) == ("invalid", "tampered")
    assert result["chain"] == "intact"
    assert result["summary"] == "Tampered: the pack's signature file is missing"


async def test_a_key_the_table_does_not_hold_is_an_unknown_key(world):
    row, stored, _ = await _generate(world)
    other = _key(9)
    result = _check(row, stored, {other.key_id: other.public_key_pem})
    assert result["signature"] == "unknown_key"
    assert result["result"] == "tampered"
    assert result["summary"] == f"Signed by an unknown key {world.key.key_id}: the signature cannot be checked"


async def test_the_row_and_the_file_must_agree(world):
    row, stored, _ = await _generate(world)
    keys = {world.key.key_id: world.key.public_key_pem}
    assert _check(row, stored, keys, signing_key_id=None, signature=None)["signature"] == "invalid"
    other_pack = verify.verify_pack_bytes(
        stored, recorded_manifest_sha256=row.manifest_sha256, public_keys=keys, pack_id=uuid.uuid4(),
        record_signature=row.signature, record_signing_key_id=row.signing_key_id,
    )
    assert other_pack["signature"] == "invalid", "a signature.json copied from another pack"


async def test_a_pack_from_before_signing_verifies_as_unsigned(world):
    world.key = None
    with structlog.testing.capture_logs() as events:
        before = metrics.compliance_pack_signing_failures_total._value.get()
        row, stored, _ = await _generate(world)
        after = metrics.compliance_pack_signing_failures_total._value.get()

    assert row.signature is None and row.signing_key_id is None
    assert "signature.json" not in dict(_members(stored))
    assert row.metadata_snapshot["signing"] == {"state": "unsigned", "reason": "key_unreadable"}
    assert after == before + 1
    warning = next(e for e in events if e["event"] == "compliance_pack_generated_unsigned")
    assert warning["log_level"] == "warning" and warning["reason"] == "key_unreadable"

    result = _check(row, stored, {})
    assert (result["result"], result["signature"]) == ("verified", "unsigned")
    assert result["summary"] == f"Verified · {LISTED} files · unsigned (tamper-evident only)"


async def test_a_key_store_fault_still_generates_an_unsigned_pack(world, monkeypatch):
    async def _boom(db, *, actor, create):
        raise RuntimeError("APP_SECRET_KEY is empty")

    monkeypatch.setattr(signing, "active_signing_key", _boom)
    row, stored, _ = await _generate(world)
    assert row.signing_key_id is None
    assert _check(row, stored, {})["result"] == "verified"


def test_not_a_zip_is_unreadable():
    result = verify.verify_pack_bytes(b"PK not really", recorded_manifest_sha256="00" * 32, public_keys={})
    assert result["result"] == "unreadable"
    assert result["summary"] == "Could not read the pack: it is not a ZIP file"
    storage = verify.unreadable_result(pack_id=uuid.uuid4(), signing_key_id="k-0123456789abcdef")
    assert storage["summary"] == "Could not read the pack from storage"
    assert storage["signature"] == "unknown_key", "a storage outage is not a forgery"


def test_a_pack_with_no_manifest_is_tampered():
    result = verify.verify_pack_bytes(_zip([("decision.json", b"{}")]), recorded_manifest_sha256=None, public_keys={})
    assert (result["result"], result["chain"]) == ("tampered", "broken")
    assert result["summary"] == "Tampered: manifest.json is missing from the pack"
    assert _status(result) == {"decision.json": "unexpected"}


async def test_signing_leaves_the_manifest_byte_for_byte(world):
    """§0.27: signature.json is outside the chain it signs."""
    db = _DB()
    zip_bytes, metadata = await svc._build_pack_payload(db, world.release)
    pack_id = uuid.uuid4()
    signed, signature, key_id, snapshot = await svc._sign_pack(
        db, zip_bytes, pack_id=pack_id, manifest_sha256=metadata["manifest_sha256"],
        actor=None, signed_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    unsigned_members = dict(_members(zip_bytes))
    signed_members = dict(_members(signed))
    assert set(signed_members) - set(unsigned_members) == {"signature.json"}
    for name, data in unsigned_members.items():
        assert signed_members[name] == data, name
    manifest = json.loads(signed_members["manifest.json"])
    assert "signature.json" not in {e["name"] for e in manifest["files"]}
    assert svc._sha256(signed_members["manifest.json"]) == metadata["manifest_sha256"]
    assert metadata["file_count"] == len(manifest["files"]) + 1 == LISTED + 1
    doc = json.loads(signed_members["signature.json"])
    assert doc["signature"] == signature and doc["key_id"] == key_id == snapshot["key_id"]
    assert doc["pack_id"] == str(pack_id) and doc["manifest_sha256"] == metadata["manifest_sha256"]


async def test_the_readme_documents_the_signature_and_what_it_does_not_prove(world):
    """The relabel the pre-W11 guard asked for: the README IS the pack's index."""
    zip_bytes, _ = await svc._build_pack_payload(_DB(), world.release)
    readme = dict(_members(zip_bytes))["README.md"].decode()
    row = next(line for line in readme.splitlines() if line.startswith("| `signature.json` |"))
    assert "Ed25519" in row and "does not list" in row
    lowered = readme.lower()
    assert "tamper-evident checksum chain" in lowered
    assert "not against an actor holding both the database and `app_secret_key`" in lowered
    assert "testlookup compliance-pack verify" in readme
    assert "/api/v1/compliance-packs/signing-keys" in readme
    doc = (svc.__doc__ or "").lower()
    assert "signed since" in doc and "app_secret_key" in doc and "worm" in doc


def test_the_summaries():
    files = [{"name": "a.json", "status": "mismatch"}, {"name": "b.json", "status": "missing"}]
    assert verify.verify_summary({"result": "tampered", "files": files}) == (
        "Tampered: a.json does not match the manifest (+1 more)"
    )
    assert verify.verify_summary({"result": "tampered", "files": files[1:]}) == (
        "Tampered: b.json is missing from the pack"
    )
    assert set(verify.VERIFY_RESULTS) == {"verified", "tampered", "unreadable"}
    assert set(verify.SIGNATURE_STATES) == {"valid", "invalid", "unsigned", "unknown_key"}
    assert set(verify.FILE_CHECK_STATUSES) == {"ok", "mismatch", "missing", "unexpected"}
    assert set(verify.CHAIN_STATES) == {"intact", "broken"}
