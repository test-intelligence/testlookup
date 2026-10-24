"""Offline compliance-pack verification (W11 REL-11) — no server, no network.

Vendors the backend's algorithm (``backend/app/services/compliance_pack_verify.py``)
without importing the backend: a reviewer verifies a downloaded pack with the
CLI alone. ``backend/tests/test_compliance_pack_cli_parity.py`` pins that the
two implementations agree on the same packs.

Offline there is no ``compliance_packs`` row, so the row checks of the server
path (the pack id, the row's signature and key id) are not made; the recorded
manifest digest is whatever ``--manifest-sha256`` says (not checked without
it), and the public keys are the one ``--public-key`` names.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# ── The format (the same constants as the backend's compliance_signing) ──────
SIGNING_ALGORITHM = "ed25519"
SIGNATURE_FILENAME = "signature.json"
MANIFEST_FILENAME = "manifest.json"
STATEMENT_LABEL = "testlookup-compliance-pack-v1"
SIGNED_STATEMENT = "testlookup-compliance-pack-v1\n{pack_id}\n{manifest_sha256}\n{key_id}\n"

VERIFY_RESULTS = ("verified", "tampered", "unreadable")
CHAIN_STATES = ("intact", "broken")
SIGNATURE_STATES = ("valid", "invalid", "unsigned", "unknown_key")
FILE_CHECK_STATUSES = ("ok", "mismatch", "missing", "unexpected")

MAX_MANIFEST_BYTES = 10_000_000
MAX_SIGNATURE_BYTES = 64_000

REASON_NOT_A_ZIP = "not_a_zip"
REASON_MANIFEST_MISSING = "manifest_missing"
REASON_MANIFEST_UNREADABLE = "manifest_unreadable"

STATUS_PHRASES = {
    "mismatch": "does not match the manifest",
    "missing": "is missing from the pack",
    "unexpected": "is not listed in the manifest",
}


class PublicKeyError(ValueError):
    """The ``--public-key`` file is not an Ed25519 public key PEM."""


def key_id_for(public_key: Ed25519PublicKey) -> str:
    """``"k-"`` + the first 16 hex of SHA-256 over the raw 32-byte public key."""
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "k-" + hashlib.sha256(raw).hexdigest()[:16]


def load_public_key(pem: str) -> tuple[str, Ed25519PublicKey]:
    """``(key_id, key)`` from a SubjectPublicKeyInfo PEM; ``PublicKeyError`` otherwise."""
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise PublicKeyError("the --public-key file is not a PEM public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise PublicKeyError("the --public-key file is not an Ed25519 public key")
    return key_id_for(key), key


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _member_sha256(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with zf.open(info) as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                digest.update(chunk)
    except Exception:  # noqa: BLE001 - an unreadable member is a changed member
        return None
    return digest.hexdigest()


def _read_small(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> Optional[bytes]:
    if info.file_size > limit:
        return None
    try:
        return zf.read(info)
    except Exception:  # noqa: BLE001
        return None


def _check_signature(doc: Mapping[str, Any], *, manifest_sha256: str, public_keys: Mapping[str, str]) -> str:
    key_id = doc.get("key_id")
    signature = doc.get("signature")
    pack_id = doc.get("pack_id")
    if (
        doc.get("algorithm") != SIGNING_ALGORITHM
        or doc.get("statement") != STATEMENT_LABEL
        or not isinstance(key_id, str)
        or not isinstance(signature, str)
        or not isinstance(pack_id, str)
        or str(doc.get("manifest_sha256") or "").lower() != manifest_sha256
    ):
        return "invalid"
    pem = public_keys.get(key_id)
    if pem is None:
        return "unknown_key"
    try:
        derived, public = load_public_key(pem)
    except PublicKeyError:
        return "invalid"
    if derived != key_id:
        return "invalid"
    statement = SIGNED_STATEMENT.format(pack_id=pack_id, manifest_sha256=manifest_sha256, key_id=key_id)
    try:
        public.verify(base64.b64decode(signature, validate=True), statement.encode("utf-8"))
    except (InvalidSignature, binascii.Error, ValueError):
        return "invalid"
    return "valid"


def verify_pack_bytes(
    zip_bytes: bytes,
    *,
    recorded_manifest_sha256: Optional[str],
    public_keys: Mapping[str, str],
) -> dict[str, Any]:
    """Verify one pack offline. The same keys as the server's verification."""
    checked_at = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "pack_id": None,
        "result": "tampered",
        "chain": "broken",
        "manifest_matches_record": None,
        "signature": "unsigned",
        "signing_key_id": None,
        "files_checked": 0,
        "files_ok": 0,
        "files": [],
        "reason": None,
        "checked_at": checked_at,
    }
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        infos = zf.infolist()
    except Exception:  # noqa: BLE001 - not a ZIP, or a truncated one
        base.update(result="unreadable", reason=REASON_NOT_A_ZIP)
        return base

    with zf:
        counts = Counter(info.filename for info in infos)
        by_name = {info.filename: info for info in infos}
        manifest_info = by_name.get(MANIFEST_FILENAME)
        manifest_bytes = _read_small(zf, manifest_info, MAX_MANIFEST_BYTES) if manifest_info else None
        manifest: Any = None
        if manifest_bytes is not None:
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                manifest = None
        entries = manifest.get("files") if isinstance(manifest, dict) else None
        if manifest_info is None:
            base["reason"] = REASON_MANIFEST_MISSING
        elif not isinstance(entries, list) or not all(
            isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("sha256"), str)
            for e in entries
        ):
            base["reason"] = REASON_MANIFEST_UNREADABLE
            entries = None

        manifest_digest = _sha256(manifest_bytes) if manifest_bytes is not None else None
        if recorded_manifest_sha256 is not None:
            base["manifest_matches_record"] = (
                manifest_digest is not None and manifest_digest == recorded_manifest_sha256.strip().lower()
            )

        listed: list[dict[str, Any]] = []
        listed_names: set[str] = set()
        for entry in sorted(entries or [], key=lambda e: e["name"]):
            name = entry["name"]
            expected = entry["sha256"].lower()
            listed_names.add(name)
            info = by_name.get(name)
            if info is None:
                listed.append({"name": name, "status": "missing", "expected_sha256": expected, "actual_sha256": None})
                continue
            actual = _member_sha256(zf, info)
            listed.append({
                "name": name,
                "status": "ok" if actual == expected else "mismatch",
                "expected_sha256": expected,
                "actual_sha256": actual,
            })

        allowed = listed_names | {MANIFEST_FILENAME, SIGNATURE_FILENAME}
        unexpected: list[dict[str, Any]] = []
        for name in sorted(counts):
            occurrences = [info for info in infos if info.filename == name]
            flagged = occurrences[:-1] if name in allowed else occurrences
            for info in flagged:
                unexpected.append({
                    "name": name, "status": "unexpected", "expected_sha256": None,
                    "actual_sha256": _member_sha256(zf, info),
                })

        base["files"] = listed + unexpected
        base["files_checked"] = len(listed)
        base["files_ok"] = sum(1 for f in listed if f["status"] == "ok")
        intact = base["reason"] is None and all(f["status"] == "ok" for f in listed) and not unexpected
        base["chain"] = "intact" if intact else "broken"

        signature_info = by_name.get(SIGNATURE_FILENAME)
        if signature_info is None:
            base["signature"] = "unsigned"
        else:
            raw_doc = _read_small(zf, signature_info, MAX_SIGNATURE_BYTES)
            doc: Any = None
            if raw_doc is not None:
                try:
                    doc = json.loads(raw_doc.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    doc = None
            if not isinstance(doc, dict) or manifest_digest is None:
                base["signature"] = "invalid"
            else:
                if isinstance(doc.get("key_id"), str):
                    base["signing_key_id"] = doc["key_id"]
                if isinstance(doc.get("pack_id"), str):
                    base["pack_id"] = doc["pack_id"]
                base["signature"] = _check_signature(
                    doc, manifest_sha256=manifest_digest, public_keys=public_keys,
                )

        verified = (
            base["chain"] == "intact"
            and base["manifest_matches_record"] is not False
            and base["signature"] in ("valid", "unsigned")
        )
        base["result"] = "verified" if verified else "tampered"
        return base


def bad_file_lines(verification: Mapping[str, Any]) -> list[str]:
    """One ``<name> <phrase>`` per file that is not ``ok``."""
    return [
        f"{f.get('name')} {STATUS_PHRASES.get(str(f.get('status')), 'was changed')}"
        for f in (verification.get("files") or [])
        if f.get("status") != "ok"
    ]
