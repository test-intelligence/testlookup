"""``testlookup compliance-pack verify`` — offline pack verification (W11 REL-11).

The packs are built here, in the shape the server writes them (``manifest.json``
listing every file but itself, ``signature.json`` outside it), from a FIXED
Ed25519 seed: no binary pack and no private-key PEM is committed. Exit codes:
0 verified (or unsigned, or not checked without ``--public-key``), 1 tampered
/ invalid / another key / unsigned under ``--require-signature``, 2 unreadable
input or a usage error. No request is ever made.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typer.testing import CliRunner

from testlookup_cli import client, pack_verify
from testlookup_cli.app import app

PACK_ID = uuid.UUID("2b0f4c9e-55aa-4d7e-9c1b-0d3e6f7a8b9c")
FILES = {
    "README.md": b"# TestLookup Compliance Pack\n",
    "decision.json": b'{\n  "recommendation": "GO"\n}',
    "release.json": b'{\n  "name": "2026.10"\n}',
}


def _private(seed: int = 5) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)


def _public_pem(seed: int = 5) -> str:
    return _private(seed).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _key_id(seed: int = 5) -> str:
    raw = _private(seed).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "k-" + hashlib.sha256(raw).hexdigest()[:16]


def _manifest(files: dict[str, bytes]) -> bytes:
    return json.dumps({
        "format_version": 1,
        "files": [
            {"name": n, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in sorted(files.items())
        ],
    }, indent=2, sort_keys=True).encode()


def _pack(*, signed: bool = True, files: dict[str, bytes] | None = None, seed: int = 5,
          tamper: dict[str, bytes] | None = None, extra: dict[str, bytes] | None = None) -> tuple[bytes, str]:
    files = dict(files or FILES)
    manifest = _manifest(files)
    digest = hashlib.sha256(manifest).hexdigest()
    members = [("manifest.json", manifest)] + sorted({**files, **(tamper or {})}.items()) + sorted((extra or {}).items())
    if signed:
        statement = f"testlookup-compliance-pack-v1\n{PACK_ID}\n{digest}\n{_key_id(seed)}\n".encode()
        doc = {
            "format_version": 1, "algorithm": "ed25519", "statement": "testlookup-compliance-pack-v1",
            "pack_id": str(PACK_ID), "manifest_sha256": digest, "key_id": _key_id(seed),
            "signed_at": "2026-10-23T10:00:00+00:00",
            "signature": base64.b64encode(_private(seed).sign(statement)).decode(),
        }
        members.append(("signature.json", json.dumps(doc, indent=2, sort_keys=True).encode()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buffer.getvalue(), digest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def _refuse(*_args, **_kwargs):  # pragma: no cover - asserted never called
        raise AssertionError("compliance-pack verify must not make a request")

    monkeypatch.setattr(client, "request", _refuse)


def _write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    path = tmp_path / name
    if isinstance(data, str):
        path.write_text(data, encoding="ascii")
    else:
        path.write_bytes(data)
    return path


def _run(*argv: str):
    return CliRunner().invoke(app, ["compliance-pack", "verify", *argv])


def test_a_signed_pack_with_its_public_key_verifies(tmp_path):
    blob, digest = _pack()
    result = _run(str(_write(tmp_path, "pack.zip", blob)), "--public-key", str(_write(tmp_path, "k.pem", _public_pem())),
                  "--manifest-sha256", digest)
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"OK: signature valid (key {_key_id()}), 3/3 files match"


def test_an_edited_file_is_named_and_exits_1(tmp_path):
    blob, _ = _pack(tamper={"decision.json": b'{\n  "recommendation": "NO_GO"\n}'})
    result = _run(str(_write(tmp_path, "pack.zip", blob)), "--public-key", str(_write(tmp_path, "k.pem", _public_pem())))
    assert result.exit_code == 1, result.output
    assert "TAMPERED: decision.json does not match the manifest" in result.output
    assert "OK:" not in result.output


def test_an_unlisted_file_and_a_missing_file_are_named(tmp_path):
    files = dict(FILES)
    blob, _ = _pack(files=files, extra={"notes.txt": b"added"})
    extra = _run(str(_write(tmp_path, "a.zip", blob)))
    assert extra.exit_code == 1 and "TAMPERED: notes.txt is not listed in the manifest" in extra.output

    manifest = _manifest(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("README.md", FILES["README.md"])
    missing = _run(str(_write(tmp_path, "b.zip", buffer.getvalue())))
    assert missing.exit_code == 1
    assert "TAMPERED: decision.json is missing from the pack" in missing.output
    assert "TAMPERED: release.json is missing from the pack" in missing.output


def test_a_wrong_public_key_names_both_keys_and_exits_1(tmp_path):
    blob, _ = _pack()
    result = _run(str(_write(tmp_path, "pack.zip", blob)), "--public-key", str(_write(tmp_path, "k.pem", _public_pem(9))))
    assert result.exit_code == 1, result.output
    assert result.output.strip() == (
        f"TAMPERED: the pack is signed by key {_key_id()}, but --public-key is {_key_id(9)}"
    )


def test_without_a_public_key_the_signature_is_not_checked(tmp_path):
    blob, _ = _pack()
    result = _run(str(_write(tmp_path, "pack.zip", blob)))
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "OK: 3/3 files match; signature not checked (no --public-key)"


def test_an_unsigned_pack_passes_unless_a_signature_is_required(tmp_path):
    blob, _ = _pack(signed=False)
    pack = str(_write(tmp_path, "pack.zip", blob))
    pem = str(_write(tmp_path, "k.pem", _public_pem()))
    plain = _run(pack, "--public-key", pem)
    assert plain.exit_code == 0
    assert plain.output.strip() == "OK: 3/3 files match; the pack is unsigned (tamper-evident only)"
    required = _run(pack, "--public-key", pem, "--require-signature")
    assert required.exit_code == 1
    assert "unsigned (--require-signature)" in required.output
    usage = _run(pack, "--require-signature")
    assert usage.exit_code == 2, "--require-signature needs a key to check"


def test_a_manifest_that_does_not_match_the_recorded_digest(tmp_path):
    blob, digest = _pack()
    pack = str(_write(tmp_path, "pack.zip", blob))
    result = _run(pack, "--manifest-sha256", "0" * 64)
    assert result.exit_code == 1
    assert "TAMPERED: the manifest does not match --manifest-sha256" in result.output
    assert _run(pack, "--manifest-sha256", digest.upper()).exit_code == 0


def test_a_replaced_signature_is_invalid(tmp_path):
    blob, _ = _pack()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = [(i.filename, zf.read(i)) for i in zf.infolist()]
    doc = json.loads(dict(members)["signature.json"])
    doc["signature"] = base64.b64encode(_private().sign(b"another statement")).decode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in members:
            zf.writestr(name, json.dumps(doc).encode() if name == "signature.json" else data)
    result = _run(str(_write(tmp_path, "pack.zip", buffer.getvalue())), "--public-key",
                  str(_write(tmp_path, "k.pem", _public_pem())))
    assert result.exit_code == 1
    assert result.output.strip() == "TAMPERED: the signature does not match this pack"


def test_json_prints_the_verification(tmp_path):
    blob, _ = _pack()
    result = _run(str(_write(tmp_path, "pack.zip", blob)), "--public-key", str(_write(tmp_path, "k.pem", _public_pem())),
                  "--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert (body["result"], body["signature"], body["chain"]) == ("verified", "valid", "intact")
    assert body["pack_id"] == str(PACK_ID) and body["signing_key_id"] == _key_id()
    assert [f["status"] for f in body["files"]] == ["ok", "ok", "ok"]


@pytest.mark.parametrize("payload", [b"not a zip at all", b"PK\x03\x04truncated"])
def test_a_non_zip_exits_2(tmp_path, payload):
    result = _run(str(_write(tmp_path, "pack.zip", payload)))
    assert result.exit_code == 2
    assert "not a ZIP file" in result.output
    as_json = _run(str(_write(tmp_path, "pack2.zip", payload)), "--json")
    assert as_json.exit_code == 2 and json.loads(as_json.output)["result"] == "unreadable"


def test_unreadable_inputs_exit_2(tmp_path):
    blob, _ = _pack()
    pack = str(_write(tmp_path, "pack.zip", blob))
    assert _run(str(tmp_path / "absent.zip")).exit_code == 2
    not_pem = _run(pack, "--public-key", str(_write(tmp_path, "k.pem", "not a key")))
    assert not_pem.exit_code == 2 and "not a PEM public key" in not_pem.output
    rsa_like = _run(pack, "--public-key", str(tmp_path / "absent.pem"))
    assert rsa_like.exit_code == 2


def test_the_key_id_matches_the_servers_rule():
    key_id, _ = pack_verify.load_public_key(_public_pem())
    assert key_id == _key_id()
    assert pack_verify.SIGNED_STATEMENT.format(pack_id="p", manifest_sha256="m", key_id="k") == (
        "testlookup-compliance-pack-v1\np\nm\nk\n"
    )


def test_the_group_is_registered():
    result = CliRunner().invoke(app, ["compliance-pack", "--help"])
    assert result.exit_code == 0
    assert "verify" in result.output
