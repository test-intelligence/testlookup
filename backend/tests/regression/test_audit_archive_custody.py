"""Independent custody contracts; every S3 operation is stubbed, never live."""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile
import uuid

import boto3
from botocore.stub import Stubber
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from testlookup_cli import audit_archive as custody

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)
PROJECT = "11111111-1111-4111-8111-111111111111"
OWNER = "123456789012"
BUCKET = "independent-audit-fixture"
KMS = f"arn:aws:kms:us-east-1:{OWNER}:key/11111111-2222-3333-4444-555555555555"
PRIVATE = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
PUBLIC = PRIVATE.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
SCOPE = {"kind": "project", "project_id": PROJECT, "window_start": (NOW - timedelta(days=1)).isoformat(), "window_end": NOW.isoformat()}
ROW = f"access,role_changed,FAKE_PAYLOAD_MARKER,{PROJECT},{NOW.isoformat()}\n"
CSV = ("source,action,actor_name,project_id,created_at\n" + ROW).encode()


def sealed(**overrides):
    args = dict(source_kind="audit_csv", scope=SCOPE, retention_until=(NOW + timedelta(days=367)).isoformat(), private_key=PRIVATE, now=NOW)
    args.update(overrides)
    return custody.seal(CSV, **args)


def rewrite(blob, change):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members = change(members)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return output.getvalue()


def test_capture_and_restored_copy_verify_without_application_credentials(tmp_path):
    blob = sealed()
    restored = tmp_path / "restored.zip"
    restored.write_bytes(blob)
    doc = custody.verify(restored.read_bytes(), public_key_pem=PUBLIC, expected_sha256=hashlib.sha256(blob).hexdigest())
    assert doc["scope"] == SCOPE
    assert doc["source_check"] == {"authenticity": "operator_attested_projection", "rows": 1}
    assert doc["completeness"] == "captured_projection_only"
    assert doc["legal_hold"] == "ON"
    assert "FAKE_PAYLOAD_MARKER" not in json.dumps(doc)


@pytest.mark.parametrize("member", ["payload", "manifest.json", "signature.json"])
def test_payload_scope_and_signature_tampering_are_refused(member):
    def altered(members):
        members[member] = members[member] + b"x"
        return members
    with pytest.raises(custody.CustodyError):
        custody.verify(rewrite(sealed(), altered), public_key_pem=PUBLIC)


def test_untrusted_key_substitution_and_wrong_object_receipt_are_refused():
    other = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32).public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(custody.CustodyError):
        custody.verify(sealed(), public_key_pem=other)
    with pytest.raises(custody.CustodyError, match="recorded object checksum"):
        custody.verify(sealed(), public_key_pem=PUBLIC, expected_sha256="0" * 64)


@pytest.mark.parametrize("extra", ["../payload", "foreign-note", "payload/"])
def test_no_unlisted_or_path_like_member_can_enter_custody(extra):
    with pytest.raises(custody.CustodyError):
        custody.verify(rewrite(sealed(), lambda members: {**members, extra: b"foreign"}), public_key_pem=PUBLIC)


def test_truncated_source_or_incorrect_scope_is_not_attested_as_complete():
    for bad in [dict(SCOPE, project_id="22222222-2222-4222-8222-222222222222"), dict(SCOPE, window_start=NOW.isoformat(), window_end=(NOW + timedelta(hours=1)).isoformat())]:
        with pytest.raises(custody.CustodyError):
            sealed(scope=bad)
    capped = CSV.splitlines(keepends=True)[0] + ROW.encode() * 5000
    with pytest.raises(custody.CustodyError, match="source export cap"):
        custody.seal(capped, source_kind="audit_csv", scope=SCOPE, retention_until=(NOW + timedelta(days=367)).isoformat(), private_key=PRIVATE, now=NOW)


async def test_existing_real_signed_pack_uses_existing_verifier_and_independent_key(monkeypatch):
    from tests.test_compliance_pack_verify import _generate, install_world
    world = install_world(monkeypatch)
    row, source, db = await _generate(world)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    generated = datetime.fromisoformat(manifest["generated_at"])
    scope = {"kind": "project", "project_id": manifest["project_id"], "window_start": (generated - timedelta(seconds=1)).isoformat(), "window_end": generated.isoformat()}
    args = dict(source_kind="compliance_pack", scope=scope, retention_until=(generated + timedelta(days=367)).isoformat(), private_key=PRIVATE, source_public_keys={world.key.key_id: world.key.public_key_pem}, source_manifest_sha256=row.manifest_sha256, now=generated)
    archived = custody.seal(source, **args)
    assert custody.verify(archived, public_key_pem=PUBLIC)["source_check"]["authenticity"] == "application_signature_verified"
    assert db.commits == 0  # no change to the product's stage-only transaction semantics
    for changed in [{"source_public_keys": {}}, {"source_manifest_sha256": "0" * 64}, {"private_key": world.key.private_key}, {"source_kind": "retention_archive"}, {"scope": dict(scope, project_id=PROJECT)}]:
        with pytest.raises(custody.CustodyError):
            custody.seal(source, **(args | changed))


def test_signed_runs_archive_uses_existing_manifest_and_signature_format():
    from app.services import compliance_pack_service as packs, compliance_signing as signing
    source_private = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)
    source_public = source_private.public_key()
    source_key = signing.SigningKey(signing.key_id_for(source_public), signing.public_key_pem(source_public), source_private)
    scope = packs.ExportScope.for_runs(project_id=uuid.UUID(PROJECT), run_ids=[uuid.UUID(PROJECT)], tier="summary")
    manifest = packs._build_manifest(files={"audit_events.json": b"[]"}, generated_at=NOW, scope=scope, run=None, decision=None)
    digest = hashlib.sha256(manifest).hexdigest()
    signature = signing.signature_document(source_key, pack_id=uuid.UUID(PROJECT), manifest_sha256=digest, signed_at=NOW)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in {"audit_events.json": b"[]", "manifest.json": manifest, "signature.json": json.dumps(signature).encode()}.items():
            archive.writestr(name, value)
    result = custody.seal(buffer.getvalue(), source_kind="retention_archive", scope=SCOPE, retention_until=(NOW + timedelta(days=367)).isoformat(), private_key=PRIVATE, source_public_keys={source_key.key_id: source_key.public_key_pem}, source_manifest_sha256=digest, now=NOW)
    assert custody.verify(result, public_key_pem=PUBLIC)["source_kind"] == "retention_archive"


def test_cli_seal_verify_and_exclusive_output_preserve_local_custody(tmp_path, monkeypatch, capsys):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(custody, "datetime", Clock)
    paths = {name: tmp_path / name for name in ("private.pem", "public.pem", "source.csv", "scope.json", "archive.zip")}
    paths["private.pem"].write_bytes(PRIVATE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    paths["private.pem"].chmod(0o600)
    paths["public.pem"].write_bytes(PUBLIC)
    paths["source.csv"].write_bytes(CSV)
    paths["scope.json"].write_text(json.dumps(SCOPE))
    args = ["seal", "--input", str(paths["source.csv"]), "--source-kind", "audit_csv", "--scope", str(paths["scope.json"]), "--retention-until", (NOW + timedelta(days=367)).isoformat(), "--private-key", str(paths["private.pem"]), "--output", str(paths["archive.zip"])]
    assert custody.main(args) == 0
    original = paths["archive.zip"].read_bytes()
    assert custody.main(args) == 2
    assert paths["archive.zip"].read_bytes() == original
    assert custody.main(["verify", "--input", str(paths["archive.zip"]), "--public-key", str(paths["public.pem"]), "--expected-sha256", hashlib.sha256(original).hexdigest()]) == 0
    recovered = tmp_path / "recovered-export"
    recover_args = ["recover", "--input", str(paths["archive.zip"]), "--public-key", str(paths["public.pem"]), "--expected-sha256", hashlib.sha256(original).hexdigest(), "--output", str(recovered)]
    assert custody.main(recover_args) == 0
    assert recovered.read_bytes() == CSV
    assert custody.main(recover_args) == 2
    output = capsys.readouterr()
    assert "FAKE_PAYLOAD_MARKER" not in output.out + output.err


def client():
    # Static test credentials + Stubber: neither credential discovery nor an
    # externally reachable endpoint is used by any test operation.
    return boto3.Session(aws_access_key_id="fixture", aws_secret_access_key="fixture").client("s3", region_name="us-east-1")


def queue_publish(stub, blob, *, mode="COMPLIANCE", hold="ON", version="version-fixture", kms=KMS, put_error=False):
    doc = custody.verify(blob, public_key_pem=PUBLIC)
    until = datetime.fromisoformat(doc["retention_until"])
    common = {"Bucket": BUCKET, "ExpectedBucketOwner": OWNER}
    key = "audit-custody/" + doc["archive_id"] + ".zip"
    checksum = base64.b64encode(hashlib.sha256(blob).digest()).decode()
    stub.add_response("get_bucket_versioning", {"Status": "Enabled"}, common)
    stub.add_response("get_object_lock_configuration", {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}, common)
    expected = {
        **common, "Key": key, "Body": blob, "ContentType": "application/zip", "IfNoneMatch": "*",
        "ChecksumAlgorithm": "SHA256", "ChecksumSHA256": checksum, "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": KMS,
        "ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": until, "ObjectLockLegalHoldStatus": "ON",
    }
    if put_error:
        stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412, expected_params=expected)
        return
    stub.add_response("put_object", {"VersionId": version, "ChecksumSHA256": checksum, "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms}, expected)
    if version != "null" and kms == KMS:
        object_args = {**common, "Key": key, "VersionId": version}
        stub.add_response("get_object_retention", {"Retention": {"Mode": mode, "RetainUntilDate": until}}, object_args)
        stub.add_response("get_object_legal_hold", {"LegalHold": {"Status": hold}}, object_args)


def test_locked_publish_uses_versioned_create_only_encrypted_checksum_contract():
    api, blob = client(), sealed()
    with Stubber(api) as stub:
        queue_publish(stub, blob)
        receipt = custody.publish(api, blob, public_key_pem=PUBLIC, bucket=BUCKET, owner=OWNER, kms_key=KMS, now=NOW)
        stub.assert_no_pending_responses()
    assert receipt["version_id"] == "version-fixture"
    assert receipt["sha256"] == hashlib.sha256(blob).hexdigest()
    assert receipt["key"].endswith(receipt["archive_id"] + ".zip")


def test_existing_object_refusal_never_retries_with_an_unprotected_overwrite():
    api, blob = client(), sealed()
    with Stubber(api) as stub:
        queue_publish(stub, blob, put_error=True)
        with pytest.raises(ClientError):
            custody.publish(api, blob, public_key_pem=PUBLIC, bucket=BUCKET, owner=OWNER, kms_key=KMS, now=NOW)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("failure", [{"mode": "GOVERNANCE"}, {"hold": "OFF"}, {"version": "null"}, {"kms": KMS + "-different"}])
def test_missing_hold_compliance_version_or_encryption_is_not_a_success(failure):
    api, blob = client(), sealed()
    with Stubber(api) as stub:
        queue_publish(stub, blob, **failure)
        with pytest.raises(custody.CustodyError):
            custody.publish(api, blob, public_key_pem=PUBLIC, bucket=BUCKET, owner=OWNER, kms_key=KMS, now=NOW)
        stub.assert_no_pending_responses()


def test_expired_retention_and_unversioned_bucket_refuse_before_writing():
    api, blob = client(), sealed()
    with Stubber(api):
        with pytest.raises(custody.CustodyError, match="retention has expired"):
            custody.publish(api, blob, public_key_pem=PUBLIC, bucket=BUCKET, owner=OWNER, kms_key=KMS, now=NOW + timedelta(days=400))
    with Stubber(api) as stub:
        stub.add_response("get_bucket_versioning", {"Status": "Suspended"}, {"Bucket": BUCKET, "ExpectedBucketOwner": OWNER})
        with pytest.raises(custody.CustodyError, match="versioning"):
            custody.publish(api, blob, public_key_pem=PUBLIC, bucket=BUCKET, owner=OWNER, kms_key=KMS, now=NOW)
        stub.assert_no_pending_responses()


def test_application_caller_is_refused_before_any_archive_client_write(tmp_path, monkeypatch, capsys):
    blob = tmp_path / "capture.zip"
    public = tmp_path / "trusted.pem"
    blob.write_bytes(sealed())
    public.write_bytes(PUBLIC)

    class Session:
        def __init__(self, **kwargs):
            assert kwargs["profile_name"] == "fixture-app"

        def client(self, name, **_kwargs):
            assert name == "sts", "wrong role must not construct the S3 client"
            assert _kwargs["config"].ignore_configured_endpoint_urls is True
            return SimpleNamespace(get_caller_identity=lambda: {"Account": OWNER, "Arn": f"arn:aws:sts::{OWNER}:assumed-role/Application/fixture"})

    monkeypatch.setattr(boto3, "Session", Session)
    result = custody.main(["publish", "--input", str(blob), "--public-key", str(public), "--archive-profile", "fixture-app", "--writer-role-arn", f"arn:aws:iam::{OWNER}:role/ArchiveWriter", "--bucket", BUCKET, "--owner-account", OWNER, "--kms-key-arn", KMS, "--region", "us-east-1"])
    assert result == 2
    output = capsys.readouterr()
    assert "configured archive writer role" in output.err
    assert "FAKE_PAYLOAD_MARKER" not in output.err


def test_role_templates_separate_write_read_retention_and_app_capabilities():
    path = Path(__file__).resolve().parents[3] / "infra/audit-archive/iam-policies.example.json"
    policies = json.loads(path.read_text())

    def actions(name):
        return {action for statement in policies[name]["Statement"] for action in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])}

    assert "s3:PutObject" in actions("archive_writer")
    assert not actions("archive_writer") & {"s3:GetObject", "s3:GetObjectVersion", "s3:DeleteObject", "s3:DeleteObjectVersion", "kms:Decrypt"}
    assert not actions("audit_reader") & {"s3:PutObject", "s3:PutObjectRetention", "s3:PutObjectLegalHold", "s3:DeleteObjectVersion"}
    assert actions("retention_operator") == {"s3:GetObjectRetention", "s3:GetObjectLegalHold", "s3:PutObjectLegalHold", "s3:DeleteObjectVersion"}
    assert policies["application_explicit_deny"]["Statement"][0]["Effect"] == "Deny"
    guards = {statement["Sid"]: statement for statement in policies["bucket_guard"]["Statement"]}
    assert guards["DenyMissingCreateOnlyCondition"]["Condition"] == {"Null": {"s3:if-none-match": "true"}}
    assert guards["DenyHoldRemovalExceptRetentionRole"]["Condition"]["ArnNotEquals"]["aws:PrincipalArn"] == "__RETENTION_OPERATOR_ROLE_ARN__"
    assert guards["DenyDeleteMarkers"]["Action"] == "s3:DeleteObject"


def test_optional_operator_sdk_pins_and_hashes_match_the_existing_backend_lock():
    root = Path(__file__).resolve().parents[3]

    def pins(path):
        blocks = re.split(r"(?m)(?=^[a-zA-Z0-9][a-zA-Z0-9_.-]*==)", path.read_text())
        return {
            block.split("==")[0]: (block.splitlines()[0].rstrip(" \\"), set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block)))
            for block in blocks if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*==", block)
        }

    backend = pins(root / "backend/requirements-lock.txt")
    operator = pins(root / "cli/requirements-audit-archive.txt")
    assert set(operator) == {"boto3", "botocore", "jmespath", "s3transfer", "python-dateutil", "six", "urllib3"}
    assert all(value == backend[name] for name, value in operator.items())
