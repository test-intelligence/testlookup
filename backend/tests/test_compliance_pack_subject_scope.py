"""W31 STRAT-12 — the subject scope of the ONE archive builder (contract §0.15, §1.3, §1.4).

* The release / runs / project manifests stay BYTE-IDENTICAL: their SHA-256 over fixed inputs is
  pinned below (computed on the file BEFORE the subject branch was added).
* The subject branch: ``project_id`` null, ``scope`` "subject", the iterator's keys merged.
* The subject key prefix: ``subject-exports/YYYY/MM/DD/<job_id>.zip`` — no user id in a key.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import compliance_pack_service as packs
from app.services.compliance_pack_verify import verify_archive_file

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
RELEASE = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
POLICY = uuid.UUID("44444444-4444-4444-8444-444444444444")
PACK = uuid.UUID("55555555-5555-4555-8555-555555555555")

#: SHA-256 of ``_build_manifest`` over ``_files()`` at ``_at()``, taken on the unedited W30 file.
RELEASE_DIGEST = "65fa14084a600d6d5c00ba741224eceeefa0af7befee879fa2a8424f5d840205"
RELEASE_NO_RUN_DIGEST = "0e70525005a123e9c1c723472ff70e16d6430b51fd4e0dd7997541a6d5fa09d2"
RUNS_DIGEST = "88db7a3b2f8eec1529c4130ec561eb55b42b8bdaac08b08dabd334ebb0d03afb"
PROJECT_DIGEST = "cb5e1c444fac82a062ee88a1497028fc67df151772bf1466f57e682dcb4716bb"


def _at() -> datetime:
    return datetime(2026, 11, 4, 12, 30, 0, tzinfo=timezone.utc)


def _files() -> dict:
    return {"README.txt": b"readme", "data/a.json": (12, "ab" * 32)}


def _digest(scope: packs.ExportScope, run=None, decision=None) -> str:
    return hashlib.sha256(packs._build_manifest(_files(), _at(), scope, run, decision)).hexdigest()


def test_release_manifest_is_byte_identical() -> None:
    scope = packs.ExportScope.for_release(release_id=RELEASE, project_id=PROJECT, run_ids=[RUN])
    decision = SimpleNamespace(recommendation="go", risk_score=0.25, policy_id=POLICY)
    assert _digest(scope, SimpleNamespace(id=RUN), decision) == RELEASE_DIGEST
    assert _digest(scope) == RELEASE_NO_RUN_DIGEST


def test_runs_manifest_is_byte_identical() -> None:
    scope = packs.ExportScope.for_runs(project_id=PROJECT, run_ids=[RUN], tier="evidence")
    assert _digest(scope) == RUNS_DIGEST


def test_project_manifest_is_byte_identical() -> None:
    scope = packs.ExportScope.for_project(
        project_id=PROJECT, manifest_extra={"format": "testlookup.project-export", "schema_version": 1},
    )
    assert _digest(scope) == PROJECT_DIGEST


def test_for_subject_has_no_project_and_no_runs() -> None:
    extra = {"format": "testlookup.subject-export"}
    scope = packs.ExportScope.for_subject(manifest_extra=extra)
    assert scope.kind == "subject"
    assert scope.project_id == uuid.UUID(int=0)
    assert scope.run_ids == ()
    assert scope.tier == "full"
    assert scope.manifest_extra is extra          # the iterator fills the SAME dict later


def test_subject_manifest_branch_keys() -> None:
    subject_id = uuid.uuid4()
    extra = {
        "format": "testlookup.subject-export", "schema_version": 1,
        "source": {"app_version": "1.2.3", "host": None},
        "subject": {"id": str(subject_id), "username": "zqxwsubj"},
        "categories": {"comments": {"rows": 2, "parts": 1}},
        "requested": {"source": "self", "at": _at().isoformat()},
    }
    scope = packs.ExportScope.for_subject(manifest_extra=extra)
    manifest = json.loads(packs._build_manifest(_files(), _at(), scope, None, None))
    assert manifest["project_id"] is None
    assert manifest["scope"] == "subject"
    assert manifest["format_version"] == 1
    assert manifest["generated_at"] == _at().isoformat()
    assert [entry["name"] for entry in manifest["files"]] == ["README.txt", "data/a.json"]
    for key, value in extra.items():
        assert manifest[key] == value
    assert set(manifest) == {"format_version", "generated_at", "project_id", "files", "scope", *extra}
    # none of the other kinds' keys leak into the subject branch
    assert not {"release_id", "tier", "run_ids", "project", "entities"} & set(manifest)


def test_subject_key_prefix_carries_the_job_id_only() -> None:
    scope = packs.ExportScope.for_subject(manifest_extra={})
    key = packs.build_export_key(scope, _at(), PACK)
    assert key == f"subject-exports/2026/11/04/{PACK}.zip"
    assert str(uuid.UUID(int=0)) not in key


def test_other_kinds_keep_their_prefixes() -> None:
    release = packs.ExportScope.for_release(release_id=RELEASE, project_id=PROJECT, run_ids=[RUN])
    runs = packs.ExportScope.for_runs(project_id=PROJECT, run_ids=[RUN], tier="evidence")
    project = packs.ExportScope.for_project(project_id=PROJECT, manifest_extra={})
    assert packs.build_export_key(release, _at(), PACK) == f"compliance/2026/11/04/{RELEASE}/{PACK}.zip"
    assert packs.build_export_key(runs, _at(), PACK) == f"exports/2026/11/04/{PROJECT}/{PACK}.zip"
    assert packs.build_export_key(project, _at(), PACK) == f"project-exports/2026/11/04/{PROJECT}/{PACK}.zip"


async def test_a_streamed_subject_archive_verifies_with_the_manifest_last() -> None:
    extra: dict = {}
    scope = packs.ExportScope.for_subject(manifest_extra=extra)

    async def members():
        yield "README.txt", b"what this is\n"
        yield "profile.json", b'{"id":"x"}'
        extra.update({"format": "testlookup.subject-export", "categories": {}})   # filled before exhaustion

    sink = io.BytesIO()
    result = await packs.build_archive(scope, members=members(), sink=sink, generated_at=_at())
    with zipfile.ZipFile(io.BytesIO(sink.getvalue())) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert names[-1] == "manifest.json"
    assert manifest["scope"] == "subject" and manifest["project_id"] is None
    assert manifest["format"] == "testlookup.subject-export"
    verified = verify_archive_file(sink, recorded_manifest_sha256=result.manifest_sha256, public_keys={})
    assert verified["chain"] == "intact"
