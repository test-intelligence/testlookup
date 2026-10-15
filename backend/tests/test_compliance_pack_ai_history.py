"""AIH-06: the release compliance pack carries the decided run's AI history.

``ai_history.json`` is the ``AIHistoryExportBundle`` of the run the release was
decided on (versions, consecutive diffs, per-test versions, review status). It
is listed in the pack's README (the pack's index: a file absent from the table
is undiscoverable to the reviewer), hashed into ``manifest.json.files[]`` like
every other file -- and the hash verifies -- and counted in the pack metadata.

It is best-effort surrounding context, like the decision trail: the versions
live in Mongo, and a Mongo outage writes ``{"available": false}`` into the file
instead of failing the pack or leaving the file out. The service still never
commits (``compliance_pack_service.py`` is a STAGE_ONLY service).
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import compliance_pack_service as pack

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


def _bundle(n_versions: int = 2) -> dict:
    versions = [{"version_id": f"v{i}", "version": i, "kind": "analysis_digest", "payload": {"kind": "analysis_digest"}}
                for i in range(1, n_versions + 1)]
    return {
        "schema_version": 1, "report_type": "ai_history", "exported_at": "2026-10-14T10:00:00+00:00",
        "run": {"run_id": str(RUN_ID), "project_id": str(PROJECT_ID), "build_number": "1234"},
        "versions": versions, "run_summaries": [],
        "diffs": [{"from_version": i, "to_version": i + 1, "diff": {"identical": False}}
                  for i in range(1, n_versions)],
        "per_test_versions": {"total": 0, "truncated": False, "items": []},
        "limits": {"max_versions": 20, "max_test_rows": 500}, "partial": [],
        "requires_human_review": True, "review": {"state": "pending_review", "message": "Not reviewed."},
        "ai_disclaimer": "AI-generated.", "ai_disclaimer_version": "1",
    }


class _DB:
    """A session double: savepoints work, commits are counted (there must be none)."""

    def __init__(self):
        self.commits = 0
        self.savepoints = 0

    def begin_nested(self):
        db = self

        @asynccontextmanager
        async def _savepoint():
            db.savepoints += 1
            yield

        return _savepoint()

    async def commit(self):  # pragma: no cover - asserted never called
        self.commits += 1


@pytest.fixture
def gathered(monkeypatch):
    """Every other section stubbed: these tests are about ai_history.json."""
    run = SimpleNamespace(id=RUN_ID, build_number="1234", project_id=PROJECT_ID)
    decision = SimpleNamespace(recommendation="GO", risk_score=12, original_recommendation=None, policy_id=None)

    async def _decision_and_run(db, release):
        return decision, run

    async def _empty(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(pack, "_gather_decision_and_run", _decision_and_run)
    for name in ("_gather_release_snapshot", "_gather_policy_snapshot", "_gather_release_gate",
                 "_gather_decision_trail", "_gather_clusters", "_gather_defects", "_gather_audit_events",
                 "_gather_agent_activity"):
        monkeypatch.setattr(pack, name, _empty)
    import app.db.mongo as mongo_module

    monkeypatch.setattr(mongo_module, "get_mongo_db", lambda: "mongo-handle")
    return SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT_ID, name="Checkout", version="2.4.0")


def _export_bundle(monkeypatch, *, result=None, error=None, seen=None):
    from app.services import ai_history_read_service

    async def _export(db, mongo, *, run, max_versions=100, max_test_rows=1000):
        if seen is not None:
            seen.update(mongo=mongo, run=run, max_versions=max_versions, max_test_rows=max_test_rows)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(ai_history_read_service, "export_bundle", _export)


def _unzip(zip_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


async def test_the_pack_carries_the_runs_ai_history(gathered, monkeypatch):
    seen: dict = {}
    _export_bundle(monkeypatch, result=_bundle(3), seen=seen)
    db = _DB()
    zip_bytes, metadata = await pack._build_pack_payload(db, gathered)
    files = _unzip(zip_bytes)

    assert "ai_history.json" in files
    body = json.loads(files["ai_history.json"])
    assert body["available"] is True
    assert [v["version"] for v in body["versions"]] == [1, 2, 3]
    assert len(body["diffs"]) == 2, "N versions -> N-1 consecutive diffs"
    assert body["review"]["state"] == "pending_review", "the review status travels with the AI content"
    assert seen == {"mongo": "mongo-handle", "run": seen["run"], "max_versions": 20, "max_test_rows": 500}
    assert seen["run"].id == RUN_ID
    assert metadata["ai_history_versions"] == 3
    assert metadata["file_count"] == len(files)
    assert db.commits == 0, "compliance_pack_service is stage-only"
    assert db.savepoints == 1, "a failed read must not abort the pack's own transaction"


async def test_the_manifest_hashes_the_file_and_the_hash_verifies(gathered, monkeypatch):
    _export_bundle(monkeypatch, result=_bundle(2))
    zip_bytes, metadata = await pack._build_pack_payload(_DB(), gathered)
    files = _unzip(zip_bytes)
    manifest = json.loads(files["manifest.json"])

    entry = next(e for e in manifest["files"] if e["name"] == "ai_history.json")
    assert entry["sha256"] == hashlib.sha256(files["ai_history.json"]).hexdigest()
    assert entry["bytes"] == len(files["ai_history.json"])
    for item in manifest["files"]:
        assert hashlib.sha256(files[item["name"]]).hexdigest() == item["sha256"], item["name"]
    assert hashlib.sha256(files["manifest.json"]).hexdigest() == metadata["manifest_sha256"]
    assert set(manifest) == {"format_version", "generated_at", "project_id", "files", "release_id",
                             "test_run_id", "recommendation", "risk_score", "policy_id"}, (
        "the manifest's top-level key set is unchanged: only files[] grew"
    )


async def test_the_readme_lists_the_file_and_what_unavailable_means(gathered, monkeypatch):
    _export_bundle(monkeypatch, result=_bundle(1))
    zip_bytes, _ = await pack._build_pack_payload(_DB(), gathered)
    readme = _unzip(zip_bytes)["README.md"].decode("utf-8")
    row = next(line for line in readme.splitlines() if line.startswith("| `ai_history.json` |"))
    assert "`available: false`" in row
    assert "not that the run has no history" in row


@pytest.mark.parametrize("error", [RuntimeError("mongo down"), ConnectionError("refused")])
async def test_a_mongo_error_writes_available_false_and_the_pack_still_builds(gathered, monkeypatch, error):
    _export_bundle(monkeypatch, error=error)
    db = _DB()
    zip_bytes, metadata = await pack._build_pack_payload(db, gathered)
    files = _unzip(zip_bytes)
    assert json.loads(files["ai_history.json"]) == {"available": False, "error": "ai_history_unavailable"}
    assert metadata["ai_history_versions"] == 0
    manifest = json.loads(files["manifest.json"])
    assert "ai_history.json" in {e["name"] for e in manifest["files"]}, "stated in the pack, never left out"
    assert db.commits == 0


async def test_the_unavailable_error_of_the_read_service_is_absorbed(gathered, monkeypatch):
    from app.services import ai_history_read_service

    unavailable = getattr(ai_history_read_service, "AIHistoryUnavailable", RuntimeError)
    _export_bundle(monkeypatch, error=unavailable("ai_history_unavailable"))
    out = await pack._gather_ai_history(_DB(), SimpleNamespace(id=RUN_ID))
    assert out == {"available": False, "error": "ai_history_unavailable"}


def test_the_service_still_has_no_commit():
    import inspect
    import re

    source = inspect.getsource(pack)
    assert not re.search(r"await\s+\w*db\w*\.commit\(", source)
