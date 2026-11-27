"""Independent operator custody of authorized exports; no application config/DB.

The independent signature attests captured bytes and declared scope, not source
completeness. No network operation occurs except the explicit publish command.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from testlookup_cli.pack_verify import MAX_MANIFEST_BYTES, verify_pack_bytes

FORMAT = "testlookup-audit-custody-v1"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
SOURCE_KINDS = ("audit_csv", "compliance_pack", "retention_archive")


class CustodyError(ValueError):
    """Safe operator error: never include source contents or credential values."""


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise CustodyError("timestamps must be explicit UTC/offset ISO8601 values") from None


def _key_id(public: Ed25519PublicKey) -> str:
    return "archive-" + _sha(public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def _public(pem: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem)
    except (ValueError, TypeError):
        raise CustodyError("invalid archive public key") from None
    if not isinstance(key, Ed25519PublicKey):
        raise CustodyError("archive public key must be Ed25519")
    return key


def _statement(manifest: bytes, key_id: str) -> bytes:
    return f"{FORMAT}\n{_sha(manifest)}\n{key_id}\n".encode()


def _scope(scope: dict) -> dict:
    if not isinstance(scope, dict) or set(scope) != {"kind", "project_id", "window_start", "window_end"}:
        raise CustodyError("scope requires kind, project_id, window_start and window_end only")
    if scope["kind"] not in {"instance", "project"}:
        raise CustodyError("scope kind must be instance or project")
    try:
        project = str(uuid.UUID(scope["project_id"])) if scope["project_id"] is not None else None
    except (ValueError, TypeError, AttributeError):
        raise CustodyError("scope project_id must be a UUID or null") from None
    if (scope["kind"] == "project") != (project is not None):
        raise CustodyError("project scope requires an ID; instance scope requires null")
    start, end = _time(scope["window_start"]), _time(scope["window_end"])
    if start >= end:
        raise CustodyError("scope window must be increasing")
    return {"kind": scope["kind"], "project_id": project, "window_start": start.isoformat(), "window_end": end.isoformat()}


def _csv_projection(source: bytes, scope: dict) -> int:
    try:
        rows = list(csv.reader(io.StringIO(source.decode("utf-8"), newline=""), strict=True))
    except (ValueError, UnicodeError, csv.Error):
        raise CustodyError("source is not an authorized audit CSV projection") from None
    if not rows or rows[0] != ["source", "action", "actor_name", "project_id", "created_at"]:
        raise CustodyError("audit CSV headers do not match the supported redacted export")
    if len(rows) - 1 >= 5000:
        raise CustodyError("audit CSV reaches the source export cap; narrow scope before archival")
    start, end = _time(scope["window_start"]), _time(scope["window_end"])
    for row in rows[1:]:
        if len(row) != 5 or not row[0] or not row[1]:
            raise CustodyError("audit CSV contains an invalid row")
        if not start <= _time(row[4]) <= end:
            raise CustodyError("audit CSV row falls outside the declared capture window")
        if scope["kind"] == "project" and row[3] != scope["project_id"]:
            raise CustodyError("audit CSV row falls outside the declared project scope")
    return len(rows) - 1


def seal(
    source: bytes, *, source_kind: str, scope: dict, retention_until: str,
    private_key: Ed25519PrivateKey, source_public_keys: dict[str, str] | None = None,
    source_manifest_sha256: str | None = None, now: datetime | None = None,
) -> bytes:
    """Bounded local capture. Source authorization remains an operator duty."""
    if not source or len(source) > MAX_SOURCE_BYTES or source_kind not in SOURCE_KINDS:
        raise CustodyError("unsupported or oversized archive source")
    if not isinstance(private_key, Ed25519PrivateKey):
        raise CustodyError("independent signing key must be Ed25519")
    captured = now or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise CustodyError("capture timestamp must include its timezone")
    captured = captured.astimezone(timezone.utc)
    until = _time(retention_until)
    if not 1 <= (until - captured).total_seconds() / 86400 <= 3650:
        raise CustodyError("archive retention must be between one day and ten years")
    capture_scope = _scope(scope)
    if _time(capture_scope["window_end"]) > captured:
        raise CustodyError("capture scope cannot include future events")
    source_check: dict[str, Any] = {"authenticity": "operator_attested_projection"}
    if source_kind == "audit_csv":
        source_check["rows"] = _csv_projection(source, capture_scope)
    else:
        if not isinstance(source_manifest_sha256, str) or not re.fullmatch("[0-9a-f]{64}", source_manifest_sha256):
            raise CustodyError("signed source requires its independently recorded manifest SHA256")
        try:
            with zipfile.ZipFile(io.BytesIO(source)) as archive:
                members = archive.infolist()
                if len(members) > 1000 or sum(m.file_size for m in members) > 256 * 1024 * 1024:
                    raise CustodyError("signed source exceeds verification bounds")
                if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                    raise CustodyError("signed source manifest exceeds verification bounds")
                source_manifest = json.loads(archive.read("manifest.json"))
        except (zipfile.BadZipFile, ValueError, KeyError):
            raise CustodyError("signed source is not a readable evidence archive") from None
        for pem in (source_public_keys or {}).values():
            if _key_id(_public(pem.encode())) == _key_id(private_key.public_key()):
                raise CustodyError("archive custody key must differ from application signing keys")
        checked = verify_pack_bytes(source, recorded_manifest_sha256=source_manifest_sha256, public_keys=source_public_keys or {})
        if checked["result"] != "verified" or checked["signature"] != "valid":
            raise CustodyError("source evidence must have a valid trusted application signature and digest")
        if not isinstance(source_manifest, dict) or (source_kind == "compliance_pack" and "release_id" not in source_manifest) or (source_kind == "retention_archive" and source_manifest.get("scope") != "runs"):
            raise CustodyError("signed source kind does not match the supported export class")
        if capture_scope["kind"] == "project" and source_manifest.get("project_id") != capture_scope["project_id"]:
            raise CustodyError("signed source project differs from declared custody scope")
        if not _time(capture_scope["window_start"]) <= _time(source_manifest.get("generated_at")) <= _time(capture_scope["window_end"]):
            raise CustodyError("signed source generation is outside the capture window")
        source_check = {"authenticity": "application_signature_verified", "manifest_sha256": source_manifest_sha256, "key_id": checked["signing_key_id"]}
    manifest = _json({
        "format": FORMAT, "archive_id": str(uuid.uuid4()), "captured_at": captured.isoformat(),
        "source_kind": source_kind, "source_sha256": _sha(source), "source_bytes": len(source),
        "scope": capture_scope, "source_check": source_check, "retention_until": until.isoformat(),
        "legal_hold": "ON", "completeness": "captured_projection_only",
    })
    if len(manifest) > MAX_METADATA_BYTES:
        raise CustodyError("capture metadata exceeds bounds")
    key_id = _key_id(private_key.public_key())
    signature = _json({"algorithm": "ed25519", "key_id": key_id, "signature": base64.b64encode(private_key.sign(_statement(manifest, key_id))).decode()})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("payload", source)
        archive.writestr("manifest.json", manifest)
        archive.writestr("signature.json", signature)
    return output.getvalue()


def verify(blob: bytes, *, public_key_pem: bytes, expected_sha256: str | None = None) -> dict:
    """Verify captured payload/scope with an independently pinned public key."""
    if len(blob) > MAX_SOURCE_BYTES + 2 * MAX_METADATA_BYTES + 4096:
        raise CustodyError("custody archive exceeds bounds")
    if expected_sha256 is not None and _sha(blob) != expected_sha256:
        raise CustodyError("archive differs from the independently recorded object checksum")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            members = archive.infolist()
            if sorted(m.filename for m in members) != ["manifest.json", "payload", "signature.json"] or any(m.compress_type != zipfile.ZIP_STORED for m in members):
                raise CustodyError("unexpected custody archive members or compression")
            for member in members:
                if member.file_size > (MAX_SOURCE_BYTES if member.filename == "payload" else MAX_METADATA_BYTES):
                    raise CustodyError("custody archive member exceeds bounds")
            source, manifest, signature = (archive.read(name) for name in ("payload", "manifest.json", "signature.json"))
        document, signed = json.loads(manifest), json.loads(signature)
        public = _public(public_key_pem)
        if set(signed) != {"algorithm", "key_id", "signature"} or signed["algorithm"] != "ed25519" or signed["key_id"] != _key_id(public):
            raise CustodyError("archive signing key is not the independently trusted key")
        public.verify(base64.b64decode(signed["signature"], validate=True), _statement(manifest, signed["key_id"]))
        expected = {"format", "archive_id", "captured_at", "source_kind", "source_sha256", "source_bytes", "scope", "source_check", "retention_until", "legal_hold", "completeness"}
        if set(document) != expected or _json(document) != manifest or document["format"] != FORMAT:
            raise CustodyError("invalid or noncanonical custody manifest")
        uuid.UUID(document["archive_id"])
        _scope(document["scope"])
        if document["source_sha256"] != _sha(source) or document["source_bytes"] != len(source):
            raise CustodyError("captured payload differs from its signed digest")
        if document["source_kind"] not in SOURCE_KINDS or document["legal_hold"] != "ON" or document["completeness"] != "captured_projection_only":
            raise CustodyError("unsupported custody policy")
        captured, until = _time(document["captured_at"]), _time(document["retention_until"])
        if not 1 <= (until - captured).total_seconds() / 86400 <= 3650 or _time(document["scope"]["window_end"]) > captured:
            raise CustodyError("invalid signed custody retention window")
        if document["source_kind"] == "audit_csv":
            if document["source_check"] != {"authenticity": "operator_attested_projection", "rows": _csv_projection(source, document["scope"])}:
                raise CustodyError("invalid signed audit projection attestation")
        elif not isinstance(document["source_check"], dict) or set(document["source_check"]) != {"authenticity", "manifest_sha256", "key_id"} or document["source_check"]["authenticity"] != "application_signature_verified" or not re.fullmatch(r"[0-9a-f]{64}", document["source_check"]["manifest_sha256"]):
            raise CustodyError("invalid signed source verification attestation")
        return document
    except CustodyError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile, InvalidSignature, RuntimeError):
        raise CustodyError("archive integrity verification failed") from None


def publish(client, blob: bytes, *, public_key_pem: bytes, bucket: str, owner: str, kms_key: str, now: datetime | None = None) -> dict:
    """Create-only locked write; injected clients enable fake-only tests."""
    document = verify(blob, public_key_pem=public_key_pem)
    if not re.fullmatch(r"[0-9]{12}", owner) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise CustodyError("invalid independent archive account/bucket")
    if not re.fullmatch(r"arn:aws(?:-us-gov|-cn)?:kms:[a-z0-9-]+:" + owner + r":key/[a-zA-Z0-9-]+", kms_key):
        raise CustodyError("use a full KMS key ARN in the independent archive account")
    until = _time(document["retention_until"])
    current = now or datetime.now(timezone.utc)
    if until <= current:
        raise CustodyError("archive retention has expired; obtain a reviewed new capture policy")
    common = {"Bucket": bucket, "ExpectedBucketOwner": owner}
    if client.get_bucket_versioning(**common).get("Status") != "Enabled":
        raise CustodyError("archive bucket must have versioning enabled")
    if client.get_object_lock_configuration(**common).get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") != "Enabled":
        raise CustodyError("archive bucket must have Object Lock enabled")
    key = "audit-custody/" + document["archive_id"] + ".zip"
    checksum = base64.b64encode(hashlib.sha256(blob).digest()).decode()
    response = client.put_object(
        **common, Key=key, Body=blob, ContentType="application/zip", IfNoneMatch="*",
        ChecksumAlgorithm="SHA256", ChecksumSHA256=checksum,
        ServerSideEncryption="aws:kms", SSEKMSKeyId=kms_key,
        ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=until, ObjectLockLegalHoldStatus="ON",
    )
    version = response.get("VersionId")
    if not version or version == "null" or response.get("ChecksumSHA256") != checksum or response.get("ServerSideEncryption") != "aws:kms" or response.get("SSEKMSKeyId") != kms_key:
        raise CustodyError("publish acknowledgement is incomplete; reconcile the object before retrying")
    object_args = {**common, "Key": key, "VersionId": version}
    retained = client.get_object_retention(**object_args).get("Retention", {})
    held = client.get_object_legal_hold(**object_args).get("LegalHold", {}).get("Status")
    if retained.get("Mode") != "COMPLIANCE" or held != "ON" or retained.get("RetainUntilDate", current) < until:
        raise CustodyError("publish protection readback failed; reconcile before any retention action")
    return {"archive_id": document["archive_id"], "key": key, "version_id": version, "sha256": _sha(blob), "key_id": _key_id(_public(public_key_pem))}


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise CustodyError("input file exceeds bounds")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal_parser = commands.add_parser("seal", help="countersign an already-authorized local export")
    seal_parser.add_argument("--input", type=Path, required=True)
    seal_parser.add_argument("--source-kind", choices=SOURCE_KINDS, required=True)
    seal_parser.add_argument("--scope", type=Path, required=True)
    seal_parser.add_argument("--retention-until", required=True)
    seal_parser.add_argument("--private-key", type=Path, required=True)
    seal_parser.add_argument("--output", type=Path, required=True)
    seal_parser.add_argument("--source-public-key", type=Path)
    seal_parser.add_argument("--source-manifest-sha256")
    for name in ("verify", "recover", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--public-key", type=Path, required=True)
        if name == "verify":
            command.add_argument("--expected-sha256")
        elif name == "recover":
            command.add_argument("--expected-sha256", required=True)
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--archive-profile", required=True)
            command.add_argument("--writer-role-arn", required=True)
            command.add_argument("--bucket", required=True)
            command.add_argument("--owner-account", required=True)
            command.add_argument("--kms-key-arn", required=True)
            command.add_argument("--region", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            if os.name == "posix" and args.private_key.stat().st_mode & 0o077:
                raise CustodyError("custody private key must be readable only by its operator owner")
            private = serialization.load_pem_private_key(_read(args.private_key, MAX_METADATA_BYTES), password=None)
            keys = {}
            if args.source_public_key:
                from testlookup_cli.pack_verify import load_public_key
                key_id, _ = load_public_key(_read(args.source_public_key, MAX_METADATA_BYTES).decode())
                keys[key_id] = args.source_public_key.read_text()
            blob = seal(_read(args.input, MAX_SOURCE_BYTES), source_kind=args.source_kind, scope=json.loads(_read(args.scope, MAX_METADATA_BYTES)), retention_until=args.retention_until, private_key=private, source_public_keys=keys, source_manifest_sha256=args.source_manifest_sha256)
            with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                output.write(blob)
            print(json.dumps({"result": "sealed", "sha256": _sha(blob)}))
        else:
            blob = _read(args.input, MAX_SOURCE_BYTES + 2 * MAX_METADATA_BYTES + 4096)
            public = _read(args.public_key, MAX_METADATA_BYTES)
            document = verify(blob, public_key_pem=public, expected_sha256=getattr(args, "expected_sha256", None))
            if args.command == "verify":
                print(json.dumps({"result": "verified", "archive_id": document["archive_id"], "source_kind": document["source_kind"]}))
            elif args.command == "recover":
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    source = archive.read("payload")
                with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                    output.write(source)
                print(json.dumps({"result": "recovered_local_export", "archive_id": document["archive_id"], "source_sha256": _sha(source)}))
            else:
                import boto3
                from botocore.config import Config
                if args.archive_profile == "default" or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", args.archive_profile):
                    raise CustodyError("use an explicit independently provisioned archive writer profile")
                session = boto3.Session(profile_name=args.archive_profile, region_name=args.region)
                identity = session.client("sts", config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True)).get_caller_identity()
                role = re.fullmatch(r"arn:(aws(?:-us-gov|-cn)?):iam::([0-9]{12}):role/(?:[A-Za-z0-9_+=,.@-]+/)*([A-Za-z0-9_+=,.@-]+)", args.writer_role_arn)
                expected = f"arn:{role[1]}:sts::{role[2]}:assumed-role/{role[3]}/" if role else ""
                if not role or identity.get("Account") != args.owner_account or role[2] != args.owner_account or not str(identity.get("Arn", "")).startswith(expected):
                    raise CustodyError("caller is not the independently configured archive writer role")
                client = session.client("s3", config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True))
                print(json.dumps(publish(client, blob, public_key_pem=public, bucket=args.bucket, owner=args.owner_account, kms_key=args.kms_key_arn)))
        return 0
    except Exception as error:
        # Provider/key parser messages may contain endpoints or credential data.
        print("audit custody operation refused: " + (str(error) if isinstance(error, CustodyError) else type(error).__name__), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
