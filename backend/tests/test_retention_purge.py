"""Regression tests for the retention purge (PMF US-11.4).

The AC's heart is cascade correctness across Postgres/Mongo/MinIO, so the
suite pins:

* policy CRUD — defaults without a row, partial PUT merge, bounds and the
  audit≥runs cross-check, source flip, service-never-commits;
* the ORDERING TRAP — execute mode deletes Mongo docs and MinIO objects
  BEFORE the Postgres run delete (the CASCADE destroys the only mapping);
* preview writes NOTHING and returns every contract count key;
* cutoff math — runs clock vs raw-events clock vs audit clock;
* re-entrancy — a second run with the same cutoffs finds zero candidates
  (and the beat sweep still writes a zero-count audit row);
* ai_provenance_records survives the run purge (0113 flips run_id to
  SET NULL) and dies only on the audit clock;
* migration 0113 chain + downgrade contract;
* StorageProvider deletes — Local traversal guard + prefix counting;
* purge gating — confirmation mismatch, disabled policy, missing project;
* the beat sweep isolates a failing project.

Hermetic: Mongo, MinIO, and the DB session are all faked; the fakes share
one ``events`` journal so cross-store ordering is assertable.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import retention_service as svc  # noqa: E402
from app.services.retention_service import (  # noqa: E402
    ConfirmationMismatch,
    EffectiveRetentionPolicy,
    PolicyDisabled,
    ProjectNotFound,
    RetentionValidationError,
)

PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def _age(days: int) -> datetime:
    return NOW - timedelta(days=days)


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Rows:
    """Mimics an SQLAlchemy result for the query shapes the service uses."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.rowcount = 0

    def all(self):
        return self._rows

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows))

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0] if self._rows else 0


class _Dml:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeDB:
    """Statement-inspecting async-session fake.

    Routes each SELECT by the table names in its compiled string; records
    DELETE/UPDATE statements plus a shared cross-store ``events`` journal
    for ordering assertions.
    """

    def __init__(
        self,
        *,
        policy=None,
        project=None,
        run_rows=(),
        tc_rows=(),
        pipeline_rows=(),
        evidence_rows=(),
        live_slugs=(),
        packs=(),
        counts=None,
        audit_rows=(),
        delete_rowcounts=None,
        events=None,
    ):
        self.policy = policy
        self.project = project
        self.run_rows = list(run_rows)
        self.tc_rows = list(tc_rows)
        self.pipeline_rows = list(pipeline_rows)
        self.evidence_rows = list(evidence_rows)
        self.live_slugs = list(live_slugs)
        self.packs = list(packs)
        self.counts = dict(counts or {})
        self.audit_rows = list(audit_rows)
        self.delete_rowcounts = dict(delete_rowcounts or {})
        self.events = events if events is not None else []

        self.added: list = []
        self.commits = 0
        self.flushes = 0
        self.delete_stmts: list = []
        self.update_stmts: list = []

    def add(self, obj):
        self.added.append(obj)
        # Emulate a subsequent SELECT seeing the staged policy row.
        if type(obj).__name__ == "ProjectRetentionPolicy":
            self.policy = obj

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt):
        kind = type(stmt).__name__
        if kind == "Delete":
            table = stmt.table.name
            self.events.append(("pg_delete", table))
            self.delete_stmts.append(stmt)
            return _Dml(self.delete_rowcounts.get(table, 0))
        if kind == "Update":
            self.events.append(("pg_update", stmt.table.name))
            self.update_stmts.append(stmt)
            return _Dml(self.counts.get("event_archive_update", 0))

        s = str(stmt)
        if "project_retention_policies" in s:
            return _Rows([self.policy] if self.policy is not None else [])
        if "count(" in s:
            if "access_audit_logs" in s:
                return _Rows([self.counts.get("access_audit", 0)])
            if "test_case_audit_logs" in s:
                return _Rows([self.counts.get("tc_audit", 0)])
            if "ai_provenance_records" in s:
                return _Rows([self.counts.get("provenance", 0)])
            if "test_runs" in s:
                return _Rows([self.counts.get("event_archive", 0)])
            return _Rows([0])
        if "compliance_packs" in s:
            return _Rows(self.packs)
        if "live_sessions" in s:
            return _Rows(self.live_slugs)
        if "agent_pipeline_runs" in s:
            return _Rows(self.pipeline_rows)
        if "evidence_artifacts" in s:
            return _Rows(self.evidence_rows)
        if "agent_memory_entries" in s:
            return _Rows([])
        if "test_cases" in s:
            return _Rows(self.tc_rows)
        if "minio_prefix" in s:
            return _Rows(self.run_rows)
        if "settings_audit_log" in s:
            return _Rows(self.audit_rows)
        if "FROM projects" in s:
            return _Rows([self.project] if self.project is not None else [])
        raise AssertionError(f"Unrouted statement in _FakeDB: {s[:200]}")


class _FakeCollection:
    def __init__(self, name, events, count=0, delete_count=0):
        self.name = name
        self.events = events
        self.count = count
        self.delete_count = delete_count
        self.count_filters: list[dict] = []
        self.delete_filters: list[dict] = []

    async def count_documents(self, filt):
        self.events.append(("mongo_count", self.name))
        self.count_filters.append(filt)
        return self.count

    async def delete_many(self, filt):
        self.events.append(("mongo_delete", self.name))
        self.delete_filters.append(filt)
        return SimpleNamespace(deleted_count=self.delete_count)


class _FakeMongo:
    def __init__(self, events, counts=None, delete_counts=None):
        self.events = events
        self.counts = dict(counts or {})
        self.delete_counts = dict(delete_counts or {})
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeCollection(
                name,
                self.events,
                count=self.counts.get(name, 0),
                delete_count=self.delete_counts.get(name, 0),
            )
        return self.collections[name]


class _FakeStorage:
    def __init__(self, events, list_map=None, prefix_counts=None):
        self.events = events
        self.list_map = dict(list_map or {})
        self.prefix_counts = dict(prefix_counts or {})
        self.deleted_objects: list[str] = []
        self.deleted_prefixes: list[str] = []

    async def list_objects(self, prefix, bucket=None):
        self.events.append(("minio_list", prefix))
        return list(self.list_map.get(prefix, []))

    async def delete_prefix(self, prefix, bucket=None):
        self.events.append(("minio_delete_prefix", prefix))
        self.deleted_prefixes.append(prefix)
        return self.prefix_counts.get(prefix, 0)

    async def delete_object(self, key, bucket=None):
        self.events.append(("minio_delete_object", key))
        self.deleted_objects.append(key)


def _policy_row(**overrides):
    base = dict(
        enabled=True,
        raw_events_days=90,
        runs_days=365,
        artifacts_days=180,
        audit_days=2555,
        updated_by_user_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Policy CRUD ──────────────────────────────────────────────────────────────


async def test_effective_policy_defaults_without_row():
    db = _FakeDB(policy=None)
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert eff == EffectiveRetentionPolicy()
    assert eff.enabled is False
    assert (eff.raw_events_days, eff.runs_days, eff.artifacts_days, eff.audit_days) == (
        90, 365, 180, 2555,
    )
    assert eff.source == "default"


async def test_effective_policy_custom_row():
    db = _FakeDB(policy=_policy_row(enabled=True, runs_days=400))
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert eff.enabled is True
    assert eff.runs_days == 400
    assert eff.source == "custom"


async def test_upsert_policy_creates_row_stages_only_and_flips_source():
    db = _FakeDB(policy=None)
    eff = await svc.upsert_policy(
        db, project_id=PROJECT_ID, actor_name="admin", runs_days=200,
    )
    # Partial body merged over defaults.
    assert eff.runs_days == 200
    assert eff.raw_events_days == 90
    assert eff.source == "custom"
    # Stage-only: the router owns the commit (transaction ratchet).
    assert db.commits == 0
    # Policy row + settings_audit_log row staged.
    added_types = [type(o).__name__ for o in db.added]
    assert "ProjectRetentionPolicy" in added_types
    assert "SettingsAuditLog" in added_types
    audit = next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")
    assert audit.setting_key == f"retention_policy:{PROJECT_ID}"
    assert isinstance(audit.changed_fields, dict)


async def test_upsert_policy_partial_update_merges_with_existing_row():
    row = _policy_row(enabled=False, runs_days=400)
    db = _FakeDB(policy=row)
    eff = await svc.upsert_policy(
        db, project_id=PROJECT_ID, enabled=True, audit_days=500,
    )
    assert row.enabled is True
    assert row.audit_days == 500
    assert row.runs_days == 400  # untouched
    assert eff.audit_days == 500
    assert db.commits == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_events_days", 5),
        ("raw_events_days", 4000),
        ("runs_days", 29),
        ("artifacts_days", 6),
        ("audit_days", 364),
    ],
)
async def test_upsert_policy_bounds_rejected(field, value):
    db = _FakeDB(policy=None)
    with pytest.raises(RetentionValidationError):
        await svc.upsert_policy(db, project_id=PROJECT_ID, **{field: value})
    assert db.added == []


async def test_upsert_policy_audit_must_cover_runs_on_merged_values():
    # Existing row already has runs_days=400; a PUT touching ONLY audit_days
    # must still be validated against the merged runs value.
    db = _FakeDB(policy=_policy_row(runs_days=400))
    with pytest.raises(RetentionValidationError, match="audit_days must be >= runs_days"):
        await svc.upsert_policy(db, project_id=PROJECT_ID, audit_days=380)


def test_write_schema_field_bounds_422():
    from pydantic import ValidationError

    from app.models.schemas import RetentionPolicyWrite

    for bad in (
        {"raw_events_days": 5},
        {"runs_days": 10},
        {"artifacts_days": 3651},
        {"audit_days": 100},
    ):
        with pytest.raises(ValidationError):
            RetentionPolicyWrite(**bad)
    # Bounds mirror the service table.
    ok = RetentionPolicyWrite(
        raw_events_days=7, runs_days=30, artifacts_days=7, audit_days=365,
    )
    assert ok.raw_events_days == 7


# ── Purge: preview ───────────────────────────────────────────────────────────


def _purge_fixture(*, run_ages_days=(400,), policy=None):
    """A project with one (or more) runs, each with a test case, a pipeline
    run, a live-session slug, and an uploads prefix."""
    events: list = []
    run_rows = []
    tc_rows = []
    pipeline_rows = []
    for i, age in enumerate(run_ages_days):
        run_id = uuid.UUID(f"aaaaaaaa-0000-0000-0000-{i:012d}")
        tc_id = uuid.UUID(f"bbbbbbbb-0000-0000-0000-{i:012d}")
        pipe_id = uuid.UUID(f"cccccccc-0000-0000-0000-{i:012d}")
        run_rows.append((run_id, f"uploads/{PROJECT_ID}/{run_id}/", _age(age)))
        tc_rows.append((tc_id, run_id))
        pipeline_rows.append((pipe_id, run_id))
    db = _FakeDB(
        policy=policy if policy is not None else _policy_row(),
        run_rows=run_rows,
        tc_rows=tc_rows,
        pipeline_rows=pipeline_rows,
        live_slugs=["slug-1"],
        counts={"event_archive": 3, "access_audit": 2, "tc_audit": 1, "provenance": 4},
        delete_rowcounts={
            "test_runs": len(run_ages_days),
            "access_audit_logs": 2,
            "test_case_audit_logs": 1,
            "ai_provenance_records": 4,
        },
        events=events,
    )
    mongo = _FakeMongo(
        events,
        counts={"raw_allure_json": 7, "decision_evidence_snapshots": 1},
        delete_counts={"execution_logs": 5, "decision_evidence_snapshots": 1},
    )
    storage = _FakeStorage(events, prefix_counts={}, list_map={})
    return db, mongo, storage, events


async def test_preview_writes_nothing_and_returns_all_count_keys():
    db, mongo, storage, events = _purge_fixture(run_ages_days=(400,))
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="preview", now=NOW,
        mongo=mongo, storage=storage,
    )
    assert out["mode"] == "preview"
    # No writes anywhere: no Postgres DML, no Mongo deletes, no MinIO
    # deletes, no staged rows, no commits.
    kinds = {kind for kind, _ in events}
    assert "pg_delete" not in kinds
    assert "pg_update" not in kinds
    assert "mongo_delete" not in kinds
    assert "minio_delete_prefix" not in kinds
    assert "minio_delete_object" not in kinds
    assert db.added == []
    assert db.commits == 0
    # Contract keys, all present.
    assert set(out["candidates"]) == {
        "runs", "test_cases", "mongo_docs", "minio_objects",
        "event_archive_rows", "audit_rows", "revoked_share_links",
        "provenance_rows",
        "compliance_packs_expired", "evidence_artifact_rows",
        "analysis_cache_entries", "memory_entries_expired",
        # SEARCH-009. Reported separately from analysis_cache_entries on
        # purpose: that counter is the AI ANALYSIS cache
        # (``ai_analysis_cache_*``), this one is the test-case SEARCH index
        # (``test_case_search``). Folding them together is how an executed
        # purge read as complete while never visiting the second store.
        "search_index_documents",
    }
    assert set(out["cutoffs"]) == {"raw_events", "runs", "artifacts", "audit"}
    assert out["candidates"]["runs"] == 1
    assert out["candidates"]["test_cases"] == 1
    assert out["candidates"]["event_archive_rows"] == 3
    assert out["candidates"]["audit_rows"] == 3  # access(2) + test-case(1)
    assert out["candidates"]["provenance_rows"] == 4
    assert out["candidates"]["mongo_docs"]["raw_allure_json"] == 7
    assert out["candidates"]["mongo_docs"]["decision_evidence_snapshots"] == 1
    snapshot_collection = mongo.collections["decision_evidence_snapshots"]
    assert snapshot_collection.count_filters == [
        {"test_run_id": {"$in": ["aaaaaaaa-0000-0000-0000-000000000000"]}}
    ]


async def test_preview_works_when_policy_disabled_or_missing():
    db, mongo, storage, _ = _purge_fixture(policy=None)
    db.policy = None  # no row at all → defaults, disabled
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="preview", now=NOW,
        mongo=mongo, storage=storage,
    )
    assert out["mode"] == "preview"
    assert out["candidates"]["runs"] == 1  # 400d > default 365d runs window


async def test_artifacts_clock_previews_and_deletes_evidence_rows():
    artifact_id = uuid.uuid4()
    preview_db, preview_mongo, preview_storage, _ = _purge_fixture()
    preview_db.evidence_rows = [artifact_id]
    preview = await svc.run_purge(
        preview_db, project_id=PROJECT_ID, mode="preview", now=NOW,
        mongo=preview_mongo, storage=preview_storage,
    )
    assert preview["candidates"]["evidence_artifact_rows"] == 1

    execute_db, execute_mongo, execute_storage, _ = _purge_fixture()
    execute_db.evidence_rows = [artifact_id]
    execute_db.delete_rowcounts["evidence_artifacts"] = 1
    executed = await svc.run_purge(
        execute_db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=execute_mongo, storage=execute_storage,
    )
    assert executed["counts"]["postgres"]["evidence_artifact_rows"] == 1


# ── Purge: execute ordering (the ordering trap) ─────────────────────────────


async def test_execute_order_mongo_then_minio_then_postgres_run_delete():
    db, mongo, storage, events = _purge_fixture(run_ages_days=(3000,))
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    assert out["mode"] == "execute"
    snapshot_collection = mongo.collections["decision_evidence_snapshots"]
    assert snapshot_collection.delete_filters == [
        {"test_run_id": {"$in": ["aaaaaaaa-0000-0000-0000-000000000000"]}}
    ]

    mongo_deletes = [i for i, (k, _) in enumerate(events) if k == "mongo_delete"]
    minio_deletes = [
        i for i, (k, _) in enumerate(events)
        if k in ("minio_delete_prefix", "minio_delete_object")
    ]
    run_deletes = [
        i for i, (k, d) in enumerate(events)
        if k == "pg_delete" and d == "test_runs"
    ]
    assert mongo_deletes, "execute must delete Mongo docs"
    assert minio_deletes, "execute must delete MinIO objects"
    assert run_deletes, "execute must delete the test_runs rows"
    # Pinned order: ALL Mongo deletes precede ALL MinIO deletes precede the
    # Postgres run delete — the CASCADE destroys the only run→doc mapping.
    assert max(mongo_deletes) < min(minio_deletes) < min(run_deletes)
    # The service never commits — the caller owns the transaction.
    assert db.commits == 0


async def test_execute_event_archive_strip_after_run_delete_and_audit_last():
    db, mongo, storage, events = _purge_fixture(run_ages_days=(3000,))
    await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    run_delete = min(
        i for i, (k, d) in enumerate(events) if k == "pg_delete" and d == "test_runs"
    )
    strip = min(
        i for i, (k, d) in enumerate(events) if k == "pg_update" and d == "test_runs"
    )
    audit_deletes = [
        i for i, (k, d) in enumerate(events)
        if k == "pg_delete" and d in ("access_audit_logs", "test_case_audit_logs")
    ]
    assert run_delete < strip
    assert audit_deletes and strip < min(audit_deletes)


# ── Cutoff math ──────────────────────────────────────────────────────────────


async def test_cutoff_math_run_clock_vs_raw_events_clock():
    # 100d-old run: inside the runs window (365) but past raw-events (90).
    # 400d-old run: past both.
    db, mongo, storage, _ = _purge_fixture(run_ages_days=(100, 400))
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    run_100 = "aaaaaaaa-0000-0000-0000-000000000000"
    run_400 = "aaaaaaaa-0000-0000-0000-000000000001"

    # Runs-class collection sees ONLY the 400d run.
    exec_logs = mongo.collections["execution_logs"]
    (filt,) = exec_logs.delete_filters
    assert filt["test_run_id"]["$in"] == [run_400]

    # Raw-events collection sees BOTH (both are past the 90d raw cutoff).
    allure = mongo.collections["raw_allure_json"]
    (filt,) = allure.delete_filters
    assert set(filt["test_run_id"]["$in"]) == {run_100, run_400}

    # live_execution_events covers uuid keys AND the LiveSession slug.
    live = mongo.collections["live_execution_events"]
    (filt,) = live.delete_filters
    assert set(filt["run_id"]["$in"]) == {run_100, run_400, "slug-1"}

    # Only the 400d run row is deleted from Postgres.
    run_delete = next(
        s for s in db.delete_stmts if s.table.name == "test_runs"
    )
    compiled = run_delete.compile()
    in_values = [
        v for v in compiled.params.values()
        if isinstance(v, (list, tuple, uuid.UUID))
    ]
    flat = []
    for v in in_values:
        flat.extend(v if isinstance(v, (list, tuple)) else [v])
    assert uuid.UUID(run_400) in flat
    assert uuid.UUID(run_100) not in flat
    assert out["counts"]["postgres"]["runs"] == 2  # canned rowcount


async def test_event_archive_strip_uses_raw_cutoff_and_nulls_both_columns():
    db, mongo, storage, _ = _purge_fixture(run_ages_days=(3000,))
    await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    stmt = next(s for s in db.update_stmts if s.table.name == "test_runs")
    text = str(stmt)
    assert "event_archive" in text and "event_archive_at" in text
    compiled = stmt.compile()
    # Both columns set to NULL (keys present with explicit None values).
    assert "event_archive" in compiled.params
    assert compiled.params["event_archive"] is None
    assert compiled.params["event_archive_at"] is None
    # The WHERE cutoff is the raw-events clock, not the runs clock.
    raw_cutoff = NOW - timedelta(days=90)
    assert raw_cutoff in compiled.params.values()


async def test_audit_clock_separate_from_run_clock():
    db, mongo, storage, _ = _purge_fixture(run_ages_days=(3000,))
    await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    audit_cutoff = NOW - timedelta(days=2555)
    runs_cutoff = NOW - timedelta(days=365)
    for table in ("access_audit_logs", "test_case_audit_logs", "ai_provenance_records"):
        stmt = next(s for s in db.delete_stmts if s.table.name == table)
        params = stmt.compile().params.values()
        assert audit_cutoff in params, f"{table} must use the audit clock"
        assert runs_cutoff not in params, f"{table} must not use the runs clock"


# ── Re-entrancy ──────────────────────────────────────────────────────────────


async def test_reentrant_second_run_finds_zero_candidates_and_deletes_nothing():
    # Same cutoffs, but the first sweep already emptied everything.
    events: list = []
    db = _FakeDB(policy=_policy_row(), events=events)
    mongo = _FakeMongo(events)
    storage = _FakeStorage(events)
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    assert out["counts"]["postgres"]["runs"] == 0
    assert out["counts"]["minio"]["objects_deleted"] == 0
    assert all(n == 0 for n in out["counts"]["mongo"].values())
    # No run delete was even issued (empty candidate chunks), and nothing
    # hit Mongo/MinIO.
    kinds = [k for k, _ in events]
    assert ("pg_delete", "test_runs") not in events
    assert "mongo_delete" not in kinds
    assert "minio_delete_prefix" not in kinds


# ── Provenance survives the run purge ────────────────────────────────────────


def test_provenance_fk_set_null_and_project_scope_cascade():
    from app.models.postgres import AIProvenanceRecord

    table = AIProvenanceRecord.__table__
    run_fks = list(table.c.run_id.foreign_keys)
    assert run_fks and run_fks[0].ondelete == "SET NULL", (
        "ai_provenance_records.run_id must be SET NULL so provenance "
        "survives the run purge (audit-class data, migration 0113)"
    )
    project_fks = list(table.c.project_id.foreign_keys)
    assert project_fks and project_fks[0].ondelete == "CASCADE"
    assert table.c.run_id.nullable and table.c.project_id.nullable


async def test_provenance_deleted_only_by_audit_clock_and_project_scope():
    db, mongo, storage, _ = _purge_fixture(run_ages_days=(3000,))
    out = await svc.run_purge(
        db, project_id=PROJECT_ID, mode="execute", now=NOW,
        mongo=mongo, storage=storage,
    )
    stmt = next(s for s in db.delete_stmts if s.table.name == "ai_provenance_records")
    text = str(stmt)
    assert "project_id" in text and "created_at" in text
    assert out["counts"]["postgres"]["provenance_rows"] == 4


# ── Migration 0113 chain + downgrade contract ────────────────────────────────


def _versions_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_migration_0113_is_chained_and_the_tree_has_one_head():
    """0113 must chain onto 0112 and the tree must keep exactly ONE head.

    Deliberately does not pin *which* revision is the head — later
    migrations legitimately move it (0114 already did). Pinning the head
    here made an unrelated, correctly-chained migration fail this test.
    """
    revisions: dict[str, str | None] = {}
    for path in _versions_dir().glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        rev = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:["\']([^"\']+)["\']|None)', text, re.M)
        if rev:
            revisions[rev.group(1)] = down.group(1) if (down and down.group(1)) else None
    assert "0113" in revisions
    assert revisions["0113"] == "0112"
    referenced = {d for d in revisions.values() if d}
    heads = set(revisions) - referenced
    assert len(heads) == 1, f"expected exactly one Alembic head, got {sorted(heads)}"
    # 0113 is either the head itself or has been built on by a later revision.
    assert "0113" in referenced or heads == {"0113"}


def test_migration_0113_downgrade_contract():
    text = (_versions_dir() / "0113_retention_policies.py").read_text(encoding="utf-8")
    upgrade_src = text.split("def upgrade()")[1].split("def downgrade()")[0]
    downgrade_src = text.split("def downgrade()")[1]
    # Upgrade: table + SET NULL flip + project_id backfill.
    assert "project_retention_policies" in upgrade_src
    assert 'ondelete="SET NULL"' in upgrade_src
    assert "UPDATE ai_provenance_records" in upgrade_src
    # Downgrade: restores the CASCADE FK, drops the column and the table.
    assert 'ondelete="CASCADE"' in downgrade_src
    assert 'op.drop_column("ai_provenance_records", "project_id")' in downgrade_src
    assert 'op.drop_table("project_retention_policies")' in downgrade_src


# ── StorageProvider delete methods ───────────────────────────────────────────


@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    from app.db.storage import LocalStorageProvider

    return LocalStorageProvider()


async def test_local_delete_object_rejects_traversal(local_storage, tmp_path):
    (tmp_path / "outside.txt").write_bytes(b"keep me")
    with pytest.raises(ValueError):
        await local_storage.delete_object("../outside.txt", bucket="bucket")
    assert (tmp_path / "outside.txt").exists()


async def test_local_delete_object_removes_file_and_is_idempotent(local_storage):
    await local_storage.put_object("a/b.txt", b"x", bucket="bucket")
    await local_storage.delete_object("a/b.txt", bucket="bucket")
    objects = await local_storage.list_objects("a/", bucket="bucket")
    assert objects == []
    # Deleting a missing object is a no-op (re-entrant purge).
    await local_storage.delete_object("a/b.txt", bucket="bucket")


async def test_local_delete_prefix_counts_and_scopes(local_storage):
    await local_storage.put_object("runs/r1/a.xml", b"1", bucket="bucket")
    await local_storage.put_object("runs/r1/b.xml", b"2", bucket="bucket")
    await local_storage.put_object("runs/r2/c.xml", b"3", bucket="bucket")
    deleted = await local_storage.delete_prefix("runs/r1/", bucket="bucket")
    assert deleted == 2
    remaining = await local_storage.list_objects("runs/", bucket="bucket")
    assert [o["Key"] for o in remaining] == ["runs/r2/c.xml"]


async def test_delete_prefix_refuses_bucket_wipe(local_storage):
    for bad in ("", "/", "//"):
        with pytest.raises(ValueError):
            await local_storage.delete_prefix(bad, bucket="bucket")


# ── Manual purge gating ──────────────────────────────────────────────────────


def _project(name="Acme", active=True):
    return SimpleNamespace(id=PROJECT_ID, name=name, is_active=active)


async def test_validate_purge_request_confirmation_mismatch():
    db = _FakeDB(project=_project("Acme"), policy=_policy_row(enabled=True))
    with pytest.raises(ConfirmationMismatch):
        await svc.validate_purge_request(
            db, project_id=PROJECT_ID, confirmation_name="acme",  # case-sensitive
        )


async def test_validate_purge_request_disabled_policy_conflict():
    db = _FakeDB(project=_project("Acme"), policy=_policy_row(enabled=False))
    with pytest.raises(PolicyDisabled):
        await svc.validate_purge_request(
            db, project_id=PROJECT_ID, confirmation_name="Acme",
        )
    # Missing row entirely → also disabled.
    db = _FakeDB(project=_project("Acme"), policy=None)
    with pytest.raises(PolicyDisabled):
        await svc.validate_purge_request(
            db, project_id=PROJECT_ID, confirmation_name="Acme",
        )


async def test_validate_purge_request_unknown_or_inactive_project():
    db = _FakeDB(project=None)
    with pytest.raises(ProjectNotFound):
        await svc.validate_purge_request(
            db, project_id=PROJECT_ID, confirmation_name="whatever",
        )
    db = _FakeDB(project=_project(active=False))
    with pytest.raises(ProjectNotFound):
        await svc.validate_purge_request(
            db, project_id=PROJECT_ID, confirmation_name="Acme",
        )


async def test_validate_purge_request_happy_path():
    db = _FakeDB(project=_project("Acme"), policy=_policy_row(enabled=True))
    project = await svc.validate_purge_request(
        db, project_id=PROJECT_ID, confirmation_name="Acme",
    )
    assert project.name == "Acme"


# ── last_purge parsing ───────────────────────────────────────────────────────


async def test_get_last_purge_returns_latest_execute_row():
    rows = [
        SimpleNamespace(  # newest first (the query orders DESC)
            created_at=NOW,
            changed_fields={"mode": "execute", "counts": {"postgres": {"runs": 3}}},
        ),
        SimpleNamespace(
            created_at=NOW - timedelta(days=1),
            changed_fields={"mode": "execute", "counts": {"postgres": {"runs": 9}}},
        ),
    ]
    db = _FakeDB(audit_rows=rows)
    out = await svc.get_last_purge(db, PROJECT_ID)
    assert out == {
        "at": NOW,
        "mode": "execute",
        "counts": {"postgres": {"runs": 3}},
    }


async def test_get_last_purge_none_when_no_execute_rows():
    db = _FakeDB(audit_rows=[
        SimpleNamespace(created_at=NOW, changed_fields=["legacy", "list", "shape"]),
    ])
    assert await svc.get_last_purge(db, PROJECT_ID) is None
    assert await svc.get_last_purge(_FakeDB(audit_rows=[]), PROJECT_ID) is None


# ── Beat sweep isolation + zero-count audit rows ─────────────────────────────


class _SweepSession:
    """Fake AsyncSessionLocal() context: serves the enabled-policy id query
    and records adds/commits, bucketed BY TYPE.

    The sweep opens this factory for several unrelated purposes — the audit
    row, and (S2b) the deletion-job status writes, which deliberately use
    their own sessions. A collector that lumped them together made
    ``audit_rows`` mean "everything anything wrote", so adding any new
    own-session writer broke an assertion about audit rows.
    """

    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return _Rows(self.store["targets"])

    def add(self, obj):
        from app.models.postgres import SettingsAuditLog

        if isinstance(obj, SettingsAuditLog):
            self.store["audit_rows"].append(obj)
        else:
            self.store.setdefault("other_rows", []).append(obj)

    async def commit(self):
        self.store["commits"] += 1


async def test_sweep_isolates_failing_project_and_writes_zero_count_audit(monkeypatch):
    from app.worker.tasks import _retention_purge_sweep

    pid_fail = uuid.UUID("dddddddd-0000-0000-0000-000000000000")
    pid_ok = uuid.UUID("eeeeeeee-0000-0000-0000-000000000000")
    store = {"targets": [pid_fail, pid_ok], "audit_rows": [], "commits": 0}

    import app.db.postgres as pg_mod

    monkeypatch.setattr(pg_mod, "AsyncSessionLocal", lambda: _SweepSession(store))

    zero_counts = {
        "postgres": {"runs": 0},
        "mongo": {},
        "minio": {"objects_deleted": 0},
    }

    async def fake_run_purge(db, *, project_id, mode, **kwargs):
        assert mode == "execute"
        if project_id == pid_fail:
            raise RuntimeError("mongo unreachable")
        return {"mode": "execute", "cutoffs": {"runs": "x"}, "counts": zero_counts}

    monkeypatch.setattr(
        "app.services.retention_service.run_purge", fake_run_purge,
    )

    summary = await _retention_purge_sweep(None)

    # One failure did NOT stop the sweep — both projects were attempted.
    assert summary["projects"] == 2
    assert summary["errors"] == 1
    assert set(summary["results"]) == {str(pid_fail), str(pid_ok)}

    # BOTH projects got an audit row — the zero-candidate/ok project keeps
    # its zeroed counts, the failed one records the error.
    assert len(store["audit_rows"]) == 2
    by_key = {row.setting_key: row for row in store["audit_rows"]}
    fail_row = by_key[f"retention_purge:{pid_fail}"]
    ok_row = by_key[f"retention_purge:{pid_ok}"]
    assert fail_row.changed_fields["errors"] and fail_row.changed_fields["counts"] is None
    assert ok_row.changed_fields["errors"] == []
    assert ok_row.changed_fields["counts"] == zero_counts
    assert ok_row.changed_fields["mode"] == "execute"

    # S2b: both projects also opened a deletion_jobs row, BEFORE the purge ran.
    # This is the half settings_audit_log structurally cannot record — the
    # failed project's audit row exists only because the sweep caught the
    # error, whereas the job row exists from the moment work started.
    from app.models.postgres import DeletionJob

    jobs = [o for o in store.get("other_rows", []) if isinstance(o, DeletionJob)]
    assert {j.project_id for j in jobs} == {pid_fail, pid_ok}
    assert all(j.status == "running" for j in jobs), (
        "a job must be opened as running; opening it in a terminal state "
        "would make a crash mid-purge indistinguishable from a clean finish"
    )


async def test_sweep_explicit_project_skips_enabled_query(monkeypatch):
    from app.worker.tasks import _retention_purge_sweep

    pid = uuid.UUID("ffffffff-0000-0000-0000-000000000000")
    store = {"targets": [], "audit_rows": [], "commits": 0}

    import app.db.postgres as pg_mod

    monkeypatch.setattr(pg_mod, "AsyncSessionLocal", lambda: _SweepSession(store))

    seen: list[uuid.UUID] = []

    async def fake_run_purge(db, *, project_id, mode, **kwargs):
        seen.append(project_id)
        return {"mode": "execute", "cutoffs": {}, "counts": {}}

    monkeypatch.setattr(
        "app.services.retention_service.run_purge", fake_run_purge,
    )

    summary = await _retention_purge_sweep(str(pid))
    assert seen == [pid]
    assert summary["projects"] == 1
    assert summary["errors"] == 0
