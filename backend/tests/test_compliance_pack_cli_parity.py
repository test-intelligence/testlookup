"""W11 REL-11 (D-15): the CLI's vendored verifier agrees with the server's.

``cli/testlookup_cli/pack_verify.py`` restates ``compliance_pack_verify``
without importing the backend, so a reviewer can verify a pack with the CLI
alone. Two copies of one algorithm drift; this pins them together on packs
built by the REAL generate path (the fixtures of
``tests/test_compliance_pack_verify.py``): signed, a changed file, an extra
file, unsigned, another key, a replaced signature, a wrong recorded digest and
a non-ZIP all give the same ``result``, ``signature``, ``chain``,
``manifest_matches_record`` and per-file statuses. The server is called the
way the CLI is (no row: ``pack_id=None``).

CI's backend job installs the CLI (``pip install -e ../cli``); a checkout
without it skips.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.services import compliance_pack_verify as server
from app.services import compliance_signing as signing
from tests.test_compliance_pack_verify import (
    _generate,
    _key,
    _members,
    _rewrite,
    _zip,
    install_world,
)

cli = pytest.importorskip("testlookup_cli.pack_verify")


@pytest.fixture
def world(monkeypatch):
    return install_world(monkeypatch)


def _shape(v: dict) -> tuple:
    return (
        v["result"], v["signature"], v["chain"], v["manifest_matches_record"],
        v["files_checked"], v["files_ok"], [(f["name"], f["status"]) for f in v["files"]],
        v.get("signing_key_id"),
    )


def _both(blob: bytes, *, digest, keys) -> tuple[dict, dict]:
    ours = server.verify_pack_bytes(blob, recorded_manifest_sha256=digest, public_keys=keys)
    theirs = cli.verify_pack_bytes(blob, recorded_manifest_sha256=digest, public_keys=keys)
    return ours, theirs


async def test_both_verifiers_agree_on_every_case(world):
    row, stored, _ = await _generate(world)
    keys = {world.key.key_id: world.key.public_key_pem}
    other = _key(11)
    doc = json.loads(dict(_members(stored))["signature.json"])
    doc["signature"] = signing.sign_statement(world.key, pack_id=uuid.uuid4(), manifest_sha256=row.manifest_sha256)

    cases = {
        "signed": (stored, row.manifest_sha256, keys),
        "no digest given": (stored, None, keys),
        "changed file": (_rewrite(stored, decision_json=b'{"forged": true}'), row.manifest_sha256, keys),
        "extra file": (_zip(_members(stored) + [("notes.txt", b"x")]), row.manifest_sha256, keys),
        "unsigned": (_rewrite(stored, signature_json=None), row.manifest_sha256, keys),
        "another key": (stored, row.manifest_sha256, {other.key_id: other.public_key_pem}),
        "no key": (stored, row.manifest_sha256, {}),
        "replaced signature": (_rewrite(stored, signature_json=json.dumps(doc).encode()), row.manifest_sha256, keys),
        "wrong digest": (stored, "0" * 64, keys),
        "not a zip": (b"not a zip", row.manifest_sha256, keys),
    }
    seen = {}
    for name, (blob, digest, case_keys) in cases.items():
        ours, theirs = _both(blob, digest=digest, keys=case_keys)
        if name == "not a zip":
            assert (ours["result"], theirs["result"]) == ("unreadable", "unreadable")
            continue
        assert _shape(ours) == _shape(theirs), name
        seen[name] = (ours["result"], ours["signature"])

    assert seen == {
        "signed": ("verified", "valid"),
        "no digest given": ("verified", "valid"),
        "changed file": ("tampered", "valid"),
        "extra file": ("tampered", "valid"),
        "unsigned": ("verified", "unsigned"),
        "another key": ("tampered", "unknown_key"),
        "no key": ("tampered", "unknown_key"),
        "replaced signature": ("tampered", "invalid"),
        "wrong digest": ("tampered", "valid"),
    }


def test_the_format_constants_are_one_format():
    assert cli.SIGNED_STATEMENT == signing.SIGNED_STATEMENT
    assert cli.STATEMENT_LABEL == signing.STATEMENT_LABEL
    assert cli.SIGNATURE_FILENAME == signing.SIGNATURE_FILENAME
    assert cli.SIGNING_ALGORITHM == signing.SIGNING_ALGORITHM
    assert cli.VERIFY_RESULTS == server.VERIFY_RESULTS
    assert cli.SIGNATURE_STATES == server.SIGNATURE_STATES
    assert cli.FILE_CHECK_STATUSES == server.FILE_CHECK_STATUSES
    assert cli.CHAIN_STATES == server.CHAIN_STATES
    assert cli.STATUS_PHRASES == server.STATUS_PHRASES
    key = _key(4)
    public = signing.Ed25519PrivateKey.from_private_bytes(bytes([4]) * 32).public_key()
    assert cli.key_id_for(public) == signing.key_id_for(public) == key.key_id
