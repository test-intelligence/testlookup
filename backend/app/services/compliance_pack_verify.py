"""Verify a compliance pack's bytes: the checksum chain, the recorded digest, the signature (W11 REL-11).

Pure over bytes (no database, no storage, no network): the verify route reads
the row, the public keys and the ZIP, then hands them here in a thread. The
CLI's offline verifier (``cli/testlookup_cli/pack_verify.py``) restates the
same algorithm without importing the backend; ``tests/test_compliance_pack_cli_parity.py``
pins that the two agree. ``verify_archive_file`` is the algorithm over a
seekable file object; ``verify_pack_bytes`` hands it a ``BytesIO``, and the
retention export (W22b) hands it the archive re-read from storage into a
spooled file -- one algorithm for packs and archives.

The algorithm:

1. Open the ZIP (not a ZIP -> ``unreadable``).
2. ``manifest.json`` missing or not a manifest -> ``tampered``, chain broken.
3. ``manifest_matches_record``: SHA-256 of ``manifest.json`` equals the digest
   recorded when the pack was generated (``None`` = not checked: the CLI
   without ``--manifest-sha256``).
4. Each manifest entry: present with an equal SHA-256 -> ``ok``, else
   ``mismatch`` / ``missing``. Any other member -- except ``manifest.json`` and
   ``signature.json``, the ONE name a pack may carry unlisted (it signs the
   manifest's digest, so listing it would be circular) -- and any duplicated
   member name -> ``unexpected``.
5. ``signature.json``: absent -> ``unsigned`` (``invalid`` on the server path
   when the ROW says the pack was signed); present -> its ``manifest_sha256``
   must be the manifest's actual digest, its ``pack_id`` the pack's (server
   path) and its signature / key id the row's (server path), the key id must
   be one of ``public_keys`` (else ``unknown_key``), and Ed25519 ``verify``
   over ``SIGNED_STATEMENT`` decides ``valid`` / ``invalid``.
6. ``verified`` iff the chain is intact, ``manifest_matches_record`` is not
   False and the signature is ``valid`` or ``unsigned``; else ``tampered``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import IO, Any, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.services.compliance_signing import (
    SIGNATURE_FILENAME,
    SIGNING_ALGORITHM,
    STATEMENT_LABEL,
    key_id_for,
    statement_bytes,
)

VERIFY_RESULTS = ("verified", "tampered", "unreadable")
CHAIN_STATES = ("intact", "broken")
SIGNATURE_STATES = ("valid", "invalid", "unsigned", "unknown_key")
FILE_CHECK_STATUSES = ("ok", "mismatch", "missing", "unexpected")

MANIFEST_FILENAME = "manifest.json"
#: Bounds on what is read whole (everything else is hashed in chunks).
MAX_MANIFEST_BYTES = 10_000_000
MAX_SIGNATURE_BYTES = 64_000

#: ``reason`` -- why a result is what it is, when the files alone do not say.
#: Carried in the dict for the summary (the response model ignores it).
REASON_NOT_A_ZIP = "not_a_zip"
REASON_STORAGE = "storage"
REASON_MANIFEST_MISSING = "manifest_missing"
REASON_MANIFEST_UNREADABLE = "manifest_unreadable"
REASON_SIGNATURE_MISSING = "signature_file_missing"

UNREADABLE_STORAGE_SENTENCE = "Could not read the pack from storage"
UNREADABLE_ZIP_SENTENCE = "Could not read the pack: it is not a ZIP file"

#: The non-ok file statuses as the panel and the CLI word them.
STATUS_PHRASES = {
    "mismatch": "does not match the manifest",
    "missing": "is missing from the pack",
    "unexpected": "is not listed in the manifest",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _member_sha256(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Optional[str]:
    """Stream a member through SHA-256; ``None`` when it cannot be read (a CRC
    error from bytes edited in place, an unsupported or encrypted entry)."""
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


def verify_summary(v: Mapping[str, Any]) -> str:
    """The one sentence the panel, the MCP tool and the CLI's ``--json`` show."""
    files_checked = int(v.get("files_checked") or 0)
    result = v.get("result")
    reason = v.get("reason")
    signature = v.get("signature")
    key_id = v.get("signing_key_id") or "?"
    if result == "unreadable":
        return UNREADABLE_STORAGE_SENTENCE if reason == REASON_STORAGE else UNREADABLE_ZIP_SENTENCE
    if result == "verified":
        if signature == "valid":
            return f"Verified · {files_checked} files · signed by key {key_id}"
        return f"Verified · {files_checked} files · unsigned (tamper-evident only)"
    if reason == REASON_MANIFEST_MISSING:
        return "Tampered: manifest.json is missing from the pack"
    if reason == REASON_MANIFEST_UNREADABLE:
        return "Tampered: manifest.json cannot be read"
    if v.get("manifest_matches_record") is False:
        return "Tampered: the manifest does not match the digest recorded when the pack was generated"
    bad = [f for f in (v.get("files") or []) if f.get("status") != "ok"]
    if bad:
        first = bad[0]
        text = f"Tampered: {first.get('name')} {STATUS_PHRASES.get(str(first.get('status')), 'was changed')}"
        if len(bad) > 1:
            text += f" (+{len(bad) - 1} more)"
        return text
    if signature == "unknown_key":
        return f"Signed by an unknown key {key_id}: the signature cannot be checked"
    if reason == REASON_SIGNATURE_MISSING:
        return "Tampered: the pack's signature file is missing"
    if signature == "invalid":
        return "Tampered: the signature does not match this pack"
    return "Tampered"


def _finish(out: dict[str, Any]) -> dict[str, Any]:
    out["summary"] = verify_summary(out)
    return out


def unreadable_result(
    *, pack_id: uuid.UUID | str | None, reason: str = REASON_STORAGE,
    signing_key_id: Optional[str] = None, checked_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """The answer when there are no bytes to check (storage failed) or they are not a ZIP.

    The signature could not be checked either: a pack the row records as signed
    reads ``unknown_key`` ("cannot be checked"), never ``invalid`` -- a storage
    outage is not a forgery, and ``result`` says what happened.
    """
    return _finish({
        "pack_id": pack_id,
        "result": "unreadable",
        "chain": "broken",
        "manifest_matches_record": False,
        "signature": "unsigned" if signing_key_id is None else "unknown_key",
        "signing_key_id": signing_key_id,
        "files_checked": 0,
        "files_ok": 0,
        "files": [],
        "reason": reason,
        "checked_at": checked_at or datetime.now(timezone.utc),
    })


def _public_key(pem: str) -> Optional[Ed25519PublicKey]:
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except Exception:  # noqa: BLE001
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


def _check_signature(
    doc: Mapping[str, Any],
    *,
    manifest_sha256: str,
    public_keys: Mapping[str, str],
    pack_id: Optional[str],
    record_signature: Optional[str],
    record_signing_key_id: Optional[str],
) -> str:
    """``valid`` | ``invalid`` | ``unknown_key`` for a parsed ``signature.json``."""
    key_id = doc.get("key_id")
    signature = doc.get("signature")
    doc_pack_id = doc.get("pack_id")
    if (
        doc.get("algorithm") != SIGNING_ALGORITHM
        or doc.get("statement") != STATEMENT_LABEL
        or not isinstance(key_id, str)
        or not isinstance(signature, str)
        or not isinstance(doc_pack_id, str)
        or str(doc.get("manifest_sha256") or "").lower() != manifest_sha256
    ):
        return "invalid"
    if pack_id is not None:  # the server path: the row and the file must agree
        if doc_pack_id.lower() != pack_id.lower():
            return "invalid"
        if record_signature != signature or record_signing_key_id != key_id:
            return "invalid"
    pem = public_keys.get(key_id)
    if pem is None:
        return "unknown_key"
    public = _public_key(pem)
    if public is None or key_id_for(public) != key_id:
        return "invalid"
    try:
        raw = base64.b64decode(signature, validate=True)
        public.verify(raw, statement_bytes(pack_id=doc_pack_id, manifest_sha256=manifest_sha256, key_id=key_id))
    except (InvalidSignature, binascii.Error, ValueError):
        return "invalid"
    return "valid"


def verify_pack_bytes(
    zip_bytes: bytes,
    *,
    recorded_manifest_sha256: Optional[str],
    public_keys: Mapping[str, str],
    pack_id: uuid.UUID | str | None = None,
    record_signature: Optional[str] = None,
    record_signing_key_id: Optional[str] = None,
    checked_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Verify one pack held in memory -- ``verify_archive_file`` over a ``BytesIO``."""
    return verify_archive_file(
        io.BytesIO(zip_bytes),
        recorded_manifest_sha256=recorded_manifest_sha256,
        public_keys=public_keys,
        pack_id=pack_id,
        record_signature=record_signature,
        record_signing_key_id=record_signing_key_id,
        checked_at=checked_at,
    )


def verify_archive_file(
    fileobj: IO[bytes],
    *,
    recorded_manifest_sha256: Optional[str],
    public_keys: Mapping[str, str],
    pack_id: uuid.UUID | str | None = None,
    record_signature: Optional[str] = None,
    record_signing_key_id: Optional[str] = None,
    checked_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Verify one pack or retention archive read from a seekable file object.

    The ONE algorithm (module docstring): a compliance pack's bytes reach it
    through ``verify_pack_bytes``; a retention archive (W22b, ACT-03) is
    re-read from storage into a spooled file and verified here without ever
    being one ``bytes`` object. ``pack_id`` given = the server path (the row's
    fields are checked too).
    """
    checked_at = checked_at or datetime.now(timezone.utc)
    server_path = pack_id is not None
    pack_id_text = str(pack_id) if server_path else None
    try:
        fileobj.seek(0)
        zf = zipfile.ZipFile(fileobj)
        infos = zf.infolist()
    except Exception:  # noqa: BLE001 - BadZipFile, a truncated archive, a non-ZIP
        return unreadable_result(
            pack_id=pack_id, reason=REASON_NOT_A_ZIP,
            signing_key_id=record_signing_key_id, checked_at=checked_at,
        )

    with zf:
        counts = Counter(info.filename for info in infos)
        by_name = {info.filename: info for info in infos}   # a duplicated name: the LAST entry, as zipfile reads it
        out: dict[str, Any] = {
            "pack_id": pack_id,
            "result": "tampered",
            "chain": "broken",
            "manifest_matches_record": None,
            "signature": "unsigned",
            "signing_key_id": record_signing_key_id,
            "files_checked": 0,
            "files_ok": 0,
            "files": [],
            "reason": None,
            "checked_at": checked_at,
        }

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
            out["reason"] = REASON_MANIFEST_MISSING
        elif not isinstance(entries, list) or not all(
            isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("sha256"), str)
            for e in entries
        ):
            out["reason"] = REASON_MANIFEST_UNREADABLE
            entries = None

        manifest_digest = _sha256(manifest_bytes) if manifest_bytes is not None else None
        if recorded_manifest_sha256 is not None:
            out["manifest_matches_record"] = (
                manifest_digest is not None and manifest_digest == recorded_manifest_sha256.lower()
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
            # An allowed name is read from its LAST entry (zipfile's rule): every
            # earlier copy is a second file under the same name, so unexpected.
            flagged = occurrences[:-1] if name in allowed else occurrences
            for info in flagged:
                unexpected.append({
                    "name": name, "status": "unexpected", "expected_sha256": None,
                    "actual_sha256": _member_sha256(zf, info),
                })

        out["files"] = listed + unexpected
        out["files_checked"] = len(listed)
        out["files_ok"] = sum(1 for f in listed if f["status"] == "ok")
        chain_intact = (
            out["reason"] is None
            and all(f["status"] == "ok" for f in listed)
            and not unexpected
        )
        out["chain"] = "intact" if chain_intact else "broken"

        signature_info = by_name.get(SIGNATURE_FILENAME)
        record_says_signed = server_path and (record_signing_key_id is not None or record_signature is not None)
        if signature_info is None:
            if record_says_signed:
                out["signature"] = "invalid"
                if out["reason"] is None:
                    out["reason"] = REASON_SIGNATURE_MISSING
            else:
                out["signature"] = "unsigned"
        else:
            raw_doc = _read_small(zf, signature_info, MAX_SIGNATURE_BYTES)
            doc: Any = None
            if raw_doc is not None:
                try:
                    doc = json.loads(raw_doc.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    doc = None
            if not isinstance(doc, dict) or manifest_digest is None:
                out["signature"] = "invalid"
            else:
                if isinstance(doc.get("key_id"), str):
                    out["signing_key_id"] = doc["key_id"]
                out["signature"] = _check_signature(
                    doc,
                    manifest_sha256=manifest_digest,
                    public_keys=public_keys,
                    pack_id=pack_id_text,
                    record_signature=record_signature,
                    record_signing_key_id=record_signing_key_id,
                )
            if not server_path and isinstance(doc, dict) and isinstance(doc.get("pack_id"), str):
                out["pack_id"] = doc["pack_id"]

        verified = (
            out["chain"] == "intact"
            and out["manifest_matches_record"] is not False
            and out["signature"] in ("valid", "unsigned")
        )
        out["result"] = "verified" if verified else "tampered"
        return _finish(out)
