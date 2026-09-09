from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ops_support.py"
RESTORE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "restore.sh"
BACKUP_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "backup.sh"

spec = importlib.util.spec_from_file_location("ops_support_restore", SCRIPT_PATH)
ops_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops_support)

PAYLOADS = {
    "postgres": b"postgres dump",
    "mongo": b"mongo archive",
    "minio": b"minio volume",
}


def manifest() -> dict:
    return {
        "schema_version": ops_support.MANIFEST_SCHEMA_VERSION,
        "created_at": "2026-09-09T00:00:00Z",
        "app_version": "1.0.0",
        "git_sha": "abc",
        "alembic_head": "0157",
        "components": [
            {
                "name": name,
                "file": ops_support.COMPONENT_FILES[name],
                "method": "test",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in PAYLOADS.items()
        ],
    }


def write_archive(
    path: Path,
    *,
    data: dict[str, bytes] | None = None,
    extra: list[tuple[tarfile.TarInfo, bytes | None]] | None = None,
) -> None:
    payloads = data or PAYLOADS
    with tarfile.open(path, "w:gz") as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        entries = {"manifest.json": json.dumps(manifest()).encode()}
        entries.update(
            {ops_support.COMPONENT_FILES[name]: value for name, value in payloads.items()}
        )
        for name, value in entries.items():
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
        for info, value in extra or []:
            if value is not None:
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
            else:
                archive.addfile(info)


def test_valid_backup_archive_passes(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"
    write_archive(archive)
    assert ops_support.verify_archive(str(archive)) == []


def test_tampered_payload_fails_checksum_before_restore(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"
    changed = dict(PAYLOADS)
    changed["postgres"] = b"tampered"
    write_archive(archive, data=changed)
    problems = ops_support.verify_archive(str(archive))
    assert any("sha256 mismatch for component 'postgres'" in p for p in problems)


def test_traversal_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"
    escape = tarfile.TarInfo("../../sentinel")
    write_archive(archive, extra=[(escape, b"escape")])
    assert any(
        "unexpected archive member" in p
        for p in ops_support.verify_archive(str(archive))
    )


def test_symlink_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"
    link = tarfile.TarInfo("unexpected-link")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../sentinel"
    write_archive(archive, extra=[(link, None)])
    problems = ops_support.verify_archive(str(archive))
    assert any("unexpected archive member" in p for p in problems)


def test_duplicate_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"
    duplicate = tarfile.TarInfo("postgres.dump")
    write_archive(archive, extra=[(duplicate, PAYLOADS["postgres"])])
    assert any(
        "duplicate archive member" in p
        for p in ops_support.verify_archive(str(archive))
    )


def test_manifest_rejects_missing_digest_and_filename_substitution() -> None:
    value = manifest()
    value["components"][0].pop("sha256")
    value["components"][1]["file"] = "../../mongo.archive.gz"
    problems = ops_support.validate_manifest(value)
    assert any("invalid sha256" in p for p in problems)
    assert any("must use file" in p for p in problems)


def test_restore_verifies_before_extraction_and_destructive_steps() -> None:
    source = RESTORE_PATH.read_text(encoding="utf-8")
    stage = source.index('cp -- "$FILE" "$ARCHIVE_COPY"')
    verify = source.index("verify-archive", stage)
    extract = source.index('tar xzf "$ARCHIVE_COPY"')
    stop = source.index("stop_app_services")
    assert stage < verify < extract < stop
    assert "backup integrity verification failed; no data was changed" in source


def test_failed_readiness_and_smoke_return_nonzero() -> None:
    source = RESTORE_PATH.read_text(encoding="utf-8")
    degraded = source.index("RESTORE VERDICT: DEGRADED")
    failed = source.index("RESTORE VERDICT: FAIL")
    assert "exit 1" in source[degraded : degraded + 240]
    assert "exit 1" in source[failed : failed + 220]


def test_backup_fails_closed_without_sha256sum() -> None:
    source = BACKUP_PATH.read_text(encoding="utf-8")
    assert "sha256sum is required to create a verifiable backup" in source
    assert 'echo "unavailable"' not in source

def test_legacy_v1_unavailable_hash_requires_explicit_override(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.tar.gz"
    legacy = manifest()
    legacy["schema_version"] = 1
    for component in legacy["components"]:
        component["sha256"] = "unavailable"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        entries = {"manifest.json": json.dumps(legacy).encode()}
        entries.update(
            {ops_support.COMPONENT_FILES[name]: data for name, data in PAYLOADS.items()}
        )
        for name, data in entries.items():
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    assert any("invalid sha256" in p for p in ops_support.verify_archive(str(archive)))
    assert ops_support.verify_archive(
        str(archive), allow_unverified_v1=True
    ) == []


def test_legacy_override_never_allows_unsafe_members(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe-legacy.tar.gz"
    traversal = tarfile.TarInfo("../../sentinel")
    write_archive(archive, extra=[(traversal, b"escape")])
    assert any(
        "unexpected archive member" in p
        for p in ops_support.verify_archive(
            str(archive), allow_unverified_v1=True
        )
    )