"""W22b ACT-03 E-3 — what each export tier contains, and what it never contains.

The archive is built end to end through the ONE builder
(``compliance_pack_service.build_archive``) over ``run_members``, with the
doubles of ``test_retention_export_service``. A credential-shaped sentinel is
seeded into an error message, a raw document and two text objects; it must be
absent from EVERY member of EVERY tier.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app.services import compliance_pack_service as packs
from app.services import retention_export_content as content
from app.services.redaction_service import redact_text
from tests.test_retention_export_service import FakeDB, MemMongo, SizedStorage

PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000000bb")
R1 = uuid.UUID("00000000-0000-4000-8000-000000000001")
R2 = uuid.UUID("00000000-0000-4000-8000-000000000002")
C1 = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
C2 = uuid.UUID("00000000-0000-4000-8000-0000000000c2")
C3 = uuid.UUID("00000000-0000-4000-8000-0000000000c3")
SENTINEL = "W22bSentinelValue"
SECRET_SENTENCE = f"login failed with password={SENTINEL} on the gateway"
MIB = 1024 * 1024


class ContentDB(FakeDB):
    """Answers run_members' statements from a small two-run world."""

    def __init__(self, created_at):
        super().__init__()
        self.runs = [
            (R1, "build-a", "FAILED", "main", "c0ffee", "staging", "push", "jenkins", created_at,
             created_at, created_at, 1200, 2, 1, 1, 0, 0, 0, 50.0, "api", ["api"]),
            (R2, "build-b", "FAILED", "main", "c0ffee", "staging", "push", "jenkins", created_at,
             created_at, created_at, 900, 1, 0, 0, 0, 1, 0, 0.0, "ui", ["ui"]),
        ]
        self.cases = {
            R1: [(C1, "test_ok", "pkg.test_ok", "api", "TestApi", "PASSED", 10, None, 0, False, None, None),
                 (C2, "test_login", "pkg.test_login", "api", "TestApi", "FAILED", 20, "PRODUCT_BUG", 1, False,
                  SECRET_SENTENCE, f"Traceback: {SECRET_SENTENCE}")],
            R2: [(C3, "test_page", "pkg.test_page", "ui", "TestUi", "BROKEN", 30, "AUTOMATION_BUG", 0, True,
                  "element not found", None)],
        }
        self.prefixes = [(R1, None), (R2, None)]

    async def execute(self, stmt, *_a, **_kw):
        from sqlalchemy.dialects import postgresql

        from tests.test_retention_export_service import _Rows

        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.sql.append(sql)
        if "test_runs.minio_prefix" in sql:
            return _Rows(self.prefixes)
        if "FROM test_runs" in sql:
            return _Rows(self.runs)
        if "FROM test_cases" in sql:
            run_id = next(v for v in compiled.params.values() if isinstance(v, uuid.UUID))
            wide = "error_message" in sql
            return _Rows([row if wide else row[:10] for row in self.cases.get(run_id, [])])
        if "FROM evidence_artifacts" in sql:
            return _Rows([])
        return _Rows([])


def _world():
    created = datetime.now(timezone.utc) - timedelta(days=40)
    db = ContentDB(created)
    mongo = MemMongo({
        "raw_allure_json": [{"_id": "a1", "test_run_id": str(R1), "message": SECRET_SENTENCE,
                             "steps": [{"name": f"step {i}"} for i in range(150)]}],
        "execution_logs": [{"_id": "l1", "test_run_id": str(R2), "line": "ok"}],
        "raw_testng_xml": [{"_id": "t1", "test_case_id": str(C2), "xml": "<testng/>"}],
        "rest_api_payloads": [{"_id": "p1", "test_case_id": str(C3), "token": SENTINEL}],
        "decision_reports": [{"_id": "d1", "test_run_id": str(R1), "summary": SECRET_SENTENCE}],
        "run_summaries": [{"_id": "s1", "test_run_id": str(R1),
                           "structured_summary": {"executive_summary": SECRET_SENTENCE}}],
    })
    up = f"uploads/{PROJECT}"
    storage = SizedStorage(
        objects={
            (None, f"{up}/{R1}/report.json"): json.dumps({"password": SENTINEL, "ok": True}).encode(),
            (None, f"{up}/{R1}/console.log"): SECRET_SENTENCE.encode(),
            (None, f"{up}/{R1}/shot.png"): b"\x89PNG" + b"\x00" * 100,
        },
        sizes={f"{up}/{R2}/huge.log": 26 * MIB},
    )
    deletion = {"project_name": "Payments sentinel", "job_id": "job-1", "project_id": str(PROJECT),
                "tier": None, "target": "collect", "run_ids_excluded": [], "missing_run_ids": []}
    return db, mongo, storage, deletion


async def _build(tier, *, at=None, world=None):
    db, mongo, storage, deletion = world or _world()
    scope = packs.ExportScope.for_runs(project_id=PROJECT, run_ids=[R1, R2], tier=tier)
    sink, stats = io.BytesIO(), {}
    result = await packs.build_archive(
        scope,
        members=content.run_members(db, scope, mongo=mongo, storage=storage,
                                    deletion={**deletion, "tier": tier}, stats=stats),
        sink=sink, generated_at=at or datetime.now(timezone.utc),
    )
    with zipfile.ZipFile(io.BytesIO(sink.getvalue())) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
        order = zf.namelist()
    return result, members, order, stats, storage


_COMMON = ["README.md", "deletion.json", "runs.json"]


def _raw(run):
    return [f"raw/{run}/{c}.jsonl" for c in ("raw_allure_json", "execution_logs", "raw_testng_xml", "rest_api_payloads")]


EXPECTED = {
    "summary": _COMMON + [f"test_cases/{R1}.json", f"test_cases/{R2}.json", "manifest.json"],
    "evidence": _COMMON + [f"test_cases/{R1}.json", f"evidence/{R1}.json",
                           f"test_cases/{R2}.json", f"evidence/{R2}.json", "manifest.json"],
    "full": _COMMON + [f"test_cases/{R1}.json", f"evidence/{R1}.json", *_raw(R1),
                       f"objects/{R1}/console.log", f"objects/{R1}/report.json",
                       f"test_cases/{R2}.json", f"evidence/{R2}.json", *_raw(R2),
                       "objects_not_archived.json", "manifest.json"],
}


@pytest.mark.parametrize("tier", ["summary", "evidence", "full"])
@pytest.mark.asyncio
async def test_each_tier_writes_exactly_its_members_manifest_last(tier):
    result, members, order, _stats, _storage = await _build(tier)
    assert order == EXPECTED[tier]
    manifest = json.loads(members["manifest.json"])
    assert sorted(e["name"] for e in manifest["files"]) == sorted(EXPECTED[tier][:-1])
    assert manifest["tier"] == tier and manifest["scope"] == "runs"
    assert result.file_count == len(EXPECTED[tier])
    readme = members["README.md"].decode()
    assert "Archive, not a backup — it cannot be re-imported." in readme
    assert "testlookup compliance-pack verify <zip> --manifest-sha256 <digest>" in readme


@pytest.mark.parametrize("tier", ["summary", "evidence", "full"])
@pytest.mark.asyncio
async def test_a_seeded_secret_is_absent_from_every_member(tier):
    assert redact_text(SECRET_SENTENCE) != SECRET_SENTENCE, "the sentinel must be one redaction catches"
    _result, members, _order, _stats, _storage = await _build(tier)
    for name, data in members.items():
        assert SENTINEL.encode() not in data, f"{tier}: {name} carries the secret"
    if tier != "summary":
        evidence = json.loads(members[f"evidence/{R1}.json"])
        assert evidence["failures"][0]["error_message"] == redact_text(SECRET_SENTENCE)
    else:
        cases = json.loads(members[f"test_cases/{R1}.json"])
        assert all("error_message" not in c and "stack_trace" not in c for c in cases)


@pytest.mark.asyncio
async def test_full_copies_text_lists_binaries_and_never_truncates_raw_lists():
    _result, members, _order, stats, storage = await _build("full")

    report = json.loads(members[f"objects/{R1}/report.json"])
    assert report == {"ok": True, "password": "[REDACTED]"}
    listed = json.loads(members["objects_not_archived.json"])
    assert listed == [
        {"run_id": str(R1), "key": f"uploads/{PROJECT}/{R1}/shot.png", "size": 104, "reason": "binary"},
        {"run_id": str(R2), "key": f"uploads/{PROJECT}/{R2}/huge.log", "size": 26 * MIB, "reason": "too_large"},
    ]
    assert stats == {"objects_not_archived": 2}
    raw = json.loads(members[f"raw/{R1}/raw_allure_json.jsonl"].decode().splitlines()[0])
    assert len(raw["steps"]) == 150, "redaction must not cut a raw document's list"
    payload = json.loads(members[f"raw/{R2}/rest_api_payloads.jsonl"].decode().splitlines()[0])
    assert payload["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_full_never_routes_raw_documents_through_the_report_projection(monkeypatch):
    from app.services import report_export_sanitizer

    seen = []
    real = report_export_sanitizer.sanitize_report_export_payload

    def _spy(payload):
        seen.append(payload)
        return real(payload)

    monkeypatch.setattr(report_export_sanitizer, "sanitize_report_export_payload", _spy)
    await _build("full")
    assert [p.get("_id") for p in seen] == ["s1"], "only the AI run report, never a raw document"


@pytest.mark.parametrize("tier", ["summary", "evidence", "full"])
@pytest.mark.asyncio
async def test_two_builds_differ_only_in_the_manifest_timestamp(tier):
    world = _world()
    first_at = datetime.now(timezone.utc)
    _r1, first, _o1, _s1, _st1 = await _build(tier, at=first_at, world=world)
    _r2, second, _o2, _s2, _st2 = await _build(tier, at=first_at + timedelta(hours=3), world=world)
    assert first.keys() == second.keys()
    for name in first:
        if name != "manifest.json":
            assert first[name] == second[name], name
    one, two = json.loads(first["manifest.json"]), json.loads(second["manifest.json"])
    assert one["generated_at"] != two["generated_at"]
    one.pop("generated_at"), two.pop("generated_at")
    assert one == two


@pytest.mark.asyncio
async def test_summary_writes_a_sliver_of_what_it_reclaims():
    from app.services import retention_export_service as svc

    world = _world()
    db, mongo, storage, deletion = world
    for i in range(5):
        storage.objects[(None, f"uploads/{PROJECT}/{R1}/blob{i}.bin")] = bytes(MIB)
    listing = await svc.list_project_objects(storage, project_id=PROJECT)
    reclaim = sum(svc.bytes_under_run(listing, project_id=PROJECT, run_id=r, minio_prefix=None) for r in (R1, R2))
    result, _members, _order, _stats, _storage = await _build("summary", world=world)
    assert reclaim > 5 * MIB
    assert result.bytes < reclaim / 100
