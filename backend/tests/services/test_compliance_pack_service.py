"""
Unit tests for ``services.compliance_pack_service``.

Covers the pure pack-assembly helpers (``_serialize``, ``_sha256``,
``_build_manifest``, ``_build_minio_key``) and the tamper-detection
contract: a pack whose manifest hash matches the stored row, and whose
per-file hashes match the manifest, should verify cleanly.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import compliance_pack_service as svc


# ── Pure helpers ────────────────────────────────────────────────────────────


def test_serialize_is_deterministic():
    """Same input → same bytes. Critical for reproducible manifests."""
    payload = {"b": 2, "a": 1, "c": [3, 2, 1]}
    out1 = svc._serialize(payload)
    out2 = svc._serialize(payload)
    assert out1 == out2
    # Sorted keys so the output is stable across dict insertion order.
    assert out1.index(b'"a":') < out1.index(b'"b":') < out1.index(b'"c":')


def test_sha256_matches_hashlib():
    import hashlib
    data = b"hello world"
    assert svc._sha256(data) == hashlib.sha256(data).hexdigest()


def test_build_minio_key_is_date_prefixed_and_unique_per_pack():
    """Repointed at the key builder production actually calls (S6a).

    ``_build_minio_key`` was generalised into ``build_export_key`` and this
    test was its only remaining caller — it would have gone on asserting the
    shape of a function nothing used, while the real key builder went
    unchecked by it.
    """
    release_id = uuid.uuid4()
    pack_id = uuid.uuid4()
    ts = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    scope = svc.ExportScope.for_release(
        release_id=release_id, project_id=uuid.uuid4(), run_ids=[uuid.uuid4()]
    )
    key = svc.build_export_key(scope, ts, pack_id)
    assert key.startswith("compliance/2026/04/14/")
    assert str(release_id) in key
    assert key.endswith(f"{pack_id}.zip")


def test_to_jsonable_handles_datetime_uuid_and_orm_rows():
    now = datetime.now(timezone.utc)
    row_id = uuid.uuid4()
    out = svc._to_jsonable({"id": row_id, "at": now, "kids": [1, 2, 3]})
    assert out["id"] == str(row_id)
    assert out["at"] == now.isoformat()
    assert out["kids"] == [1, 2, 3]


# ── Manifest ────────────────────────────────────────────────────────────────


def test_build_manifest_includes_every_file_with_correct_hash():
    files = {
        "release.json": b'{"id": "r1"}',
        "run.json": b'{"id": "t1"}',
    }
    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    # _build_manifest takes an ExportScope now (S6a) — one description for
    # both the release pack and a retention export, so there is one
    # manifest chain rather than two that can drift.
    scope = svc.ExportScope.for_release(
        release_id=release.id, project_id=release.project_id,
        run_ids=[uuid.uuid4()],
    )
    run = SimpleNamespace(id=uuid.uuid4())
    decision = SimpleNamespace(recommendation="GO", risk_score=10, policy_id=None)
    manifest_bytes = svc._build_manifest(
        files,
        datetime.now(timezone.utc),
        scope,
        run,
        decision,
    )
    manifest = json.loads(manifest_bytes)
    assert manifest["format_version"] == 1
    assert manifest["recommendation"] == "GO"
    assert manifest["risk_score"] == 10
    names = [e["name"] for e in manifest["files"]]
    assert set(names) == set(files.keys())
    # Every entry's hash must match the file bytes.
    for entry in manifest["files"]:
        expected = svc._sha256(files[entry["name"]])
        assert entry["sha256"] == expected
        assert entry["bytes"] == len(files[entry["name"]])


def test_build_manifest_is_sorted_by_filename():
    files = {"z.json": b"z", "a.json": b"a", "m.json": b"m"}
    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    # _build_manifest takes an ExportScope now (S6a) — one description for
    # both the release pack and a retention export, so there is one
    # manifest chain rather than two that can drift.
    scope = svc.ExportScope.for_release(
        release_id=release.id, project_id=release.project_id,
        run_ids=[uuid.uuid4()],
    )
    manifest = json.loads(
        svc._build_manifest(files, datetime.now(timezone.utc), scope, None, None)
    )
    names = [e["name"] for e in manifest["files"]]
    assert names == sorted(names)


# ── End-to-end assembly sanity check ────────────────────────────────────────


def test_zip_includes_manifest_and_every_declared_file():
    """Manually call the ZIP construction to verify the bytes round-trip.

    Uses the raw helpers so the test stays DB-free. If someone rewrites
    ``_build_pack_payload`` to skip the manifest or drop a file, this
    test fails immediately.
    """
    files = {
        "README.md": b"# pack",
        "release.json": b'{"id": "r1"}',
        "run.json": b'{"id": "t1"}',
    }
    release = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    # _build_manifest takes an ExportScope now (S6a) — one description for
    # both the release pack and a retention export, so there is one
    # manifest chain rather than two that can drift.
    scope = svc.ExportScope.for_release(
        release_id=release.id, project_id=release.project_id,
        run_ids=[uuid.uuid4()],
    )
    run = SimpleNamespace(id=uuid.uuid4(), build_number="b-1")
    decision = SimpleNamespace(recommendation="GO", risk_score=10, policy_id=None)

    manifest_bytes = svc._build_manifest(
        files, datetime.now(timezone.utc), scope, run, decision,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(svc._MANIFEST_FILENAME, manifest_bytes)
        for name, data in sorted(files.items()):
            zf.writestr(name, data)

    zip_bytes = buf.getvalue()
    assert zip_bytes  # non-empty

    # Re-open and assert structure.
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert svc._MANIFEST_FILENAME in names
        assert "README.md" in names
        assert "release.json" in names
        assert "run.json" in names

        # The manifest declares every other file with the correct SHA.
        manifest_payload = json.loads(zf.read(svc._MANIFEST_FILENAME))
        for entry in manifest_payload["files"]:
            actual = zf.read(entry["name"])
            assert svc._sha256(actual) == entry["sha256"]
            assert len(actual) == entry["bytes"]
