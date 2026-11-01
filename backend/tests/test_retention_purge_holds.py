"""W22a ACT-05 — holds inside the policy purge (contract §2.1 P-1 … P-22, item 5-6).

A hold check missed on ONE purge class is silent evidence loss, so the source
of truth here is the compiled SQL the purge actually sends: ``retention_holds``
appears in the WHERE of every statement of a HELD class (the 11 clauses: both
run selects, live sessions, evidence artifacts, event archive, access audit,
test-case audit, activity, deletion jobs, share links, packs) and
in none of the not-held ones; every clause is ``NOT EXISTS``, never ``NOT IN``.
The hold READS are stubbed through the hold service's own names.

W25d HYG-02 (deliberate): P-12 (the provenance class) left with the
``ai_provenance_records`` table (migration 0250) -- its plan entries are gone.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.services import ai_history_retention
from app.services import retention_hold_service as holds
from app.services import retention_service as svc
from tests.test_retention_purge import PROJECT_ID, _FakeDB, _FakeMongo, _FakeStorage, _policy_row


class _CaptureDB(_FakeDB):
    """``_FakeDB`` that also keeps every statement as (kind, table, postgres SQL)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured: list[tuple[str, str, str]] = []

    async def execute(self, stmt):
        kind = type(stmt).__name__.lower()
        if kind in ("delete", "update"):
            table = stmt.table.name
        else:
            froms = stmt.get_final_froms()
            table = getattr(froms[0], "name", "") if froms else ""
        self.captured.append((kind, table, str(stmt.compile(dialect=postgresql.dialect()))))
        return await super().execute(stmt)

    def where_of(self, kind: str, table: str, *, contains: str = "") -> list[str]:
        # The statement's own WHERE: the first one (every FROM here is one table).
        return [
            sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
            for k, t, sql in self.captured
            if k == kind and t == table and contains in sql
        ]


def _world(now, **kwargs):
    run_id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    tc_id = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
    events: list = []
    db = _CaptureDB(
        policy=_policy_row(),
        run_rows=[(run_id, f"uploads/{PROJECT_ID}/{run_id}/", now - timedelta(days=400))],
        tc_rows=[(tc_id, run_id)],
        live_slugs=["slug-held", "slug-free"],
        events=events,
        **kwargs,
    )
    return db, _FakeMongo(events), _FakeStorage(events), events


#: (kind, table, a substring that picks the statement) — the HELD classes.
_HELD_PREVIEW = [
    ("select", "test_runs", "minio_prefix"),          # P-1 superset AND the runs-clock select (2 statements)
    ("select", "live_sessions", ""),                  # P-3
    ("select", "evidence_artifacts", ""),             # P-5
    ("select", "test_runs", "event_archive_at"),      # P-8 (the preview count)
    ("select", "access_audit_logs", ""),              # P-9
    ("select", "test_case_audit_logs", ""),           # P-10
    ("select", "project_activity_events", ""),        # P-11
    ("select", "deletion_jobs", ""),                  # P-13
    ("select", "report_share_links", ""),             # P-14
    ("select", "compliance_packs", ""),               # P-16
]
_NOT_HELD_PREVIEW = [
    ("select", "agent_memory_entries", ""),           # P-15
    ("select", "search_query_events", ""),            # P-21
    ("select", "ai_eval_shadow_pairs", ""),           # P-22
]


async def test_every_held_class_carries_its_hold_clause_and_no_other_does():
    now = datetime.now(timezone.utc)
    db, mongo, storage, _ = _world(now)
    await svc.run_purge(db, project_id=PROJECT_ID, mode="preview", now=now, mongo=mongo, storage=storage)
    for kind, table, contains in _HELD_PREVIEW:
        wheres = db.where_of(kind, table, contains=contains)
        assert wheres, f"{table} ({contains or 'any'}) was never selected — the test proved nothing"
        for where in wheres:
            assert "retention_holds" in where, f"{table}: a held class without its hold clause loses evidence"
    assert len(db.where_of("select", "test_runs", contains="minio_prefix")) == 2, "both run selects"
    for kind, table, contains in _NOT_HELD_PREVIEW:
        wheres = db.where_of(kind, table, contains=contains)
        assert wheres, f"{table} was never selected"
        assert all("retention_holds" not in w for w in wheres), f"{table} is NOT held (D-7)"
    for _kind, _table, sql in db.captured:
        assert "NOT IN" not in sql, "a NULL entity_id makes NOT IN match nothing (R-3)"


_HELD_DML = [
    ("update", "test_runs"),             # P-8 the event_archive strip
    ("delete", "access_audit_logs"),
    ("delete", "test_case_audit_logs"),
    ("delete", "project_activity_events"),
    ("delete", "deletion_jobs"),
    ("delete", "report_share_links"),
]


async def test_the_executed_deletes_carry_the_same_clauses():
    now = datetime.now(timezone.utc)
    db, mongo, storage, _ = _world(now)
    await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    for kind, table in _HELD_DML:
        wheres = db.where_of(kind, table)
        assert wheres and all("retention_holds" in w for w in wheres), f"{kind} {table}"
    for table in ("search_query_events", "ai_eval_shadow_pairs"):
        wheres = db.where_of("delete", table)
        assert wheres and all("retention_holds" not in w for w in wheres), f"{table} is not held"
    run_deletes = db.where_of("delete", "test_runs")
    assert run_deletes and all("retention_holds" not in w for w in run_deletes), (
        "the run delete is BY ID — the hold already acted on the select that produced the ids"
    )


async def test_execute_locks_the_project_row_first_and_preview_takes_no_lock():
    now = datetime.now(timezone.utc)
    db, mongo, storage, _ = _world(now)
    await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    first_kind, first_table, first_sql = db.captured[0]
    assert (first_kind, first_table) == ("select", "projects")
    assert first_sql.rstrip().endswith("FOR NO KEY UPDATE"), first_sql

    db, mongo, storage, _ = _world(now)
    await svc.run_purge(db, project_id=PROJECT_ID, mode="preview", now=now, mongo=mongo, storage=storage)
    assert not [sql for _k, _t, sql in db.captured if " FOR " in sql], "a preview must not lock"


# ── The hold reads: holds / holds_honoured assembly, AI sweep, slugs, fail closed ──


def _ref(entity_type, reason, entity_id=None):
    return holds.HoldRef(
        id=uuid.uuid4(), entity_type=entity_type, entity_id=entity_id,
        entity_label=f"{entity_type} label", reason=reason, expires_at=None,
    )


@pytest.fixture
def stub_reads(monkeypatch):
    """Stubs the hold service's READS by name; records how they were called."""
    state = {"live": [], "pinned_runs": {}, "pinned_packs": {}, "held_ids": set(), "calls": []}

    async def live_holds(db, project_id, *, now):
        state["calls"].append("live_holds")
        return list(state["live"])

    async def pinned_runs_before(db, *, project_id, created_before, now):
        state["calls"].append("pinned_runs_before")
        state["created_before"] = created_before
        return state["pinned_runs"]

    async def pinned_expired_packs(db, *, project_id, now):
        state["calls"].append("pinned_expired_packs")
        return state["pinned_packs"]

    async def held_run_ids(db, *, project_id, now):
        state["calls"].append("held_run_ids")
        return set(state["held_ids"])

    for name, fn in (("live_holds", live_holds), ("pinned_runs_before", pinned_runs_before),
                     ("pinned_expired_packs", pinned_expired_packs), ("held_run_ids", held_run_ids)):
        monkeypatch.setattr(holds, name, fn)
    return state


async def test_no_live_hold_reads_nothing_more_and_records_nothing(stub_reads):
    now = datetime.now(timezone.utc)
    db, mongo, storage, _ = _world(now)
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    assert stub_reads["calls"] == ["live_holds"], "live_holds is the ONLY always-on new read"
    assert out["holds"] == {"project_held": False, "runs_kept": 0, "packs_kept": 0, "active": []}
    assert out["holds_honoured"] is None


async def test_the_holds_block_and_the_job_record_come_from_the_pinned_reads(stub_reads):
    now = datetime.now(timezone.utc)
    r1, r2, p1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    release, run, idle = _ref("release", "Audit Q4 sentinel"), _ref("run", "Run matter"), _ref("run", "Idle matter")
    stub_reads.update(live=[release, run, idle], pinned_runs={release.id: {r1, r2}, run.id: {r2}},
                      pinned_packs={release.id: {p1}}, held_ids={r1, r2})
    db, mongo, storage, _ = _world(now)
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    assert stub_reads["created_before"] == now - timedelta(days=365), "pinned past the RUNS cutoff"
    block = out["holds"]
    assert (block["project_held"], block["runs_kept"], block["packs_kept"]) == (False, 2, 1)
    assert [(a["reason"], a["runs_kept"], a["packs_kept"]) for a in block["active"]] == [
        ("Audit Q4 sentinel", 2, 1), ("Run matter", 1, 0), ("Idle matter", 0, 0),
    ], "EVERY live hold is listed; a run pinned twice counts under both"
    assert block["active"][0]["hold_id"] == str(release.id)
    honoured = out["holds_honoured"]
    assert honoured == {
        "project_held": False,
        "holds": [release.as_json(), run.as_json()],
        "runs_kept": 2,
        "packs_kept": 1,
        "run_ids_kept": sorted([str(r1), str(r2)]),
    }


async def test_a_hold_that_keeps_nothing_records_no_job_value(stub_reads):
    now = datetime.now(timezone.utc)
    stub_reads.update(live=[_ref("run", "Idle matter")])
    db, mongo, storage, _ = _world(now)
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    assert out["holds"]["active"][0]["runs_kept"] == 0
    assert out["holds_honoured"] is None, "absence = no hold applied"


async def test_a_project_hold_empties_the_ai_plan_and_is_always_honoured(stub_reads, monkeypatch):
    now = datetime.now(timezone.utc)
    project = _ref("project", "Litigation sentinel")
    stub_reads.update(live=[project])

    async def _never(*args, **kwargs):
        raise AssertionError("a live project hold must skip the over-cap sweep")

    monkeypatch.setattr(ai_history_retention, "plan_over_cap", _never)
    db, mongo, storage, _ = _world(now)
    out = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    assert out["counts"]["ai_history"]["analysis_versions_over_cap"] == 0
    assert out["holds"]["project_held"] is True
    assert out["holds_honoured"]["project_held"] is True and out["holds_honoured"]["holds"] == [project.as_json()]
    assert "held_run_ids" not in stub_reads["calls"], "the project arm already excludes everything"


async def test_held_runs_join_the_ai_exclusions_and_their_slugs_are_dropped(stub_reads, monkeypatch):
    from app.services.stream_service import canonical_test_run_uuid

    now = datetime.now(timezone.utc)
    held_slug_run = canonical_test_run_uuid("slug-held")
    other = uuid.uuid4()
    stub_reads.update(live=[_ref("run", "Run matter", held_slug_run)], held_ids={held_slug_run, other})
    seen: dict = {}

    async def _plan(db, mongo, *, project_id, caps, exclude_run_ids=()):
        seen["exclude"] = list(exclude_run_ids)
        return ai_history_retention.OverCapPlan()

    monkeypatch.setattr(ai_history_retention, "plan_over_cap", _plan)
    db, mongo, storage, _ = _world(now)
    await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=now, mongo=mongo, storage=storage)
    assert {held_slug_run, other} <= set(seen["exclude"])
    assert uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001") in seen["exclude"], "the purged runs stay excluded"
    (live_filter,) = mongo.collections["live_execution_events"].delete_filters
    assert "slug-held" not in live_filter["run_id"]["$in"] and "slug-free" in live_filter["run_id"]["$in"]


@pytest.mark.parametrize("mode", ["preview", "execute"])
async def test_a_failed_hold_read_fails_the_purge_closed(monkeypatch, mode):
    async def _boom(db, project_id, *, now):
        raise RuntimeError("relation retention_holds is unreadable")

    monkeypatch.setattr(holds, "live_holds", _boom)
    now = datetime.now(timezone.utc)
    db, mongo, storage, events = _world(now)
    with pytest.raises(RuntimeError, match="unreadable"):
        await svc.run_purge(db, project_id=PROJECT_ID, mode=mode, now=now, mongo=mongo, storage=storage)
    assert not [e for e in events if e[0] in ("pg_delete", "pg_update", "mongo_delete", "minio_delete_prefix")]
    assert not [t for k, t, _ in db.captured if k == "select" and t == "test_runs"], "no candidate was read"
