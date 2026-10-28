"""W22a ACT-04 F-3 — the SET NULL reference resolver (``services/retention_references.py``).

The FK list is DERIVED from metadata, so every expectation below is computed
by the test's OWN metadata walk, never from a literal list; the counts fail
SOFT (a warning must never fail or block a deletion).
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, ForeignKey, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.db.postgres import Base
from app.services import retention_references as rr
from app.services.retention_vocab import REFERENCE_FALLBACK_LABEL, REFERENCE_LABELS, REFERENCE_LABELS_ONE


def _throwaway() -> MetaData:
    md = MetaData()
    Table("test_runs", md, Column("id", PG_UUID(as_uuid=True), primary_key=True))
    Table(
        "widgets", md,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("run_id", PG_UUID(as_uuid=True), ForeignKey("test_runs.id", ondelete="CASCADE")),
        Column("baseline_id", PG_UUID(as_uuid=True), ForeignKey("test_runs.id", ondelete="SET NULL")),
        Column("plain_id", PG_UUID(as_uuid=True), ForeignKey("test_runs.id")),  # NO ACTION
    )
    Table(
        "cases", md,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("run_id", PG_UUID(as_uuid=True), ForeignKey("test_runs.id", ondelete="CASCADE")),
    )
    Table(
        "steps", md,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("case_id", PG_UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE")),
        Column("source_run_id", PG_UUID(as_uuid=True), ForeignKey("test_runs.id", ondelete="set null")),
    )
    return md


def test_a_set_null_fk_is_picked_up_with_its_cascade_chains():
    refs = rr.set_null_refs(_throwaway())
    assert [(r.table, r.column) for r in refs] == [("steps", "source_run_id"), ("widgets", "baseline_id")]
    by_key = {r.key: r for r in refs}
    assert by_key["widgets.baseline_id"].survive_chains == ((("widgets.run_id", "test_runs.id"),),)
    assert by_key["steps.source_run_id"].survive_chains == (
        (("steps.case_id", "cases.id"), ("cases.run_id", "test_runs.id")),
    ), "depth 2: a CASCADE to a table that itself CASCADEs to test_runs"


def test_cascade_and_no_action_fks_are_not_references():
    keys = {r.key for r in rr.set_null_refs(_throwaway())}
    assert "widgets.run_id" not in keys and "widgets.plain_id" not in keys and "cases.run_id" not in keys


def test_the_walk_is_cached_per_metadata_object():
    md = _throwaway()
    assert rr.set_null_refs(md) is rr.set_null_refs(md)


def _own_walk(md: MetaData) -> set[tuple[str, str]]:
    found = set()
    for table in md.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "test_runs" and (fk.ondelete or "").upper() == "SET NULL":
                found.add((table.name, fk.parent.name))
    return found


def test_on_the_real_metadata_the_set_equals_an_independent_walk():
    derived = rr.set_null_refs()
    pairs = [(r.table, r.column) for r in derived]
    assert set(pairs) == _own_walk(Base.metadata)
    assert pairs == sorted(pairs), "sorted (table, column)"
    assert len(pairs) >= 20
    assert ("run_baselines", "baseline_run_id") in pairs
    baseline = next(r for r in derived if r.key == "run_baselines.baseline_run_id")
    assert (("run_baselines.run_id", "test_runs.id"),) in baseline.survive_chains, (
        "a baseline row whose OWN run is purged cascades away and must not be counted"
    )


def test_labels_use_the_vocab_copy_and_the_fallback():
    assert rr.label_for("run_baselines", "baseline_run_id", 3) == "3 runs lose their baseline link"
    assert rr.label_for("run_baselines", "baseline_run_id", 3) == REFERENCE_LABELS[
        "run_baselines.baseline_run_id"].format(count=3)
    assert rr.label_for("notification_logs", "run_id", 2) == REFERENCE_FALLBACK_LABEL.format(
        count=2, table="notification_logs", column="run_id")


def test_a_count_of_one_reads_in_the_singular():
    # The local W22a demo read "1 compliance packs lose their run link".
    assert rr.label_for("compliance_packs", "test_run_id", 1) == "1 compliance pack loses its run link"
    assert rr.label_for("run_baselines", "baseline_run_id", 1) == "1 run loses its baseline link"
    assert rr.label_for("notification_logs", "run_id", 1) == "1 notification_logs row loses its run_id link"
    assert rr.label_for("compliance_packs", "test_run_id", 2) == "2 compliance packs lose their run link"
    # Every specific label has its singular twin, so no FK falls back to the plural for a count of 1.
    assert set(REFERENCE_LABELS_ONE) == set(REFERENCE_LABELS)


def _compiled(ref) -> str:
    stmt = rr.count_statement(ref, rr._ids_param([uuid.uuid4(), uuid.uuid4()]))
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_the_count_binds_one_array_and_is_null_safe():
    by_key = {r.key: r for r in rr.set_null_refs(_throwaway())}
    sql = _compiled(by_key["widgets.baseline_id"])
    assert "widgets.baseline_id = ANY (%(purged_run_ids)s::UUID[])" in sql
    assert "widgets.run_id IS NULL OR NOT (widgets.run_id = ANY" in sql, (
        "a bare NOT (k = ANY) is NULL for a NULL k and would drop a surviving row"
    )
    assert " IN (" not in sql and "NOT IN" not in sql, "one uuid[] bind, never an expanding IN"
    deep = _compiled(by_key["steps.source_run_id"])
    assert "NOT (EXISTS (SELECT" in deep and "cascade_0.run_id = ANY" in deep
    assert "cascade_0.id = steps.case_id" in deep


# ── references_broken: counts, ordering, fail-soft ───────────────────────────


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Savepoint:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.savepoints += 1
        return self

    async def __aexit__(self, *exc):
        return False


class _CountingDB:
    """Answers each COUNT by the referencing table named in its FROM."""

    def __init__(self, counts=None, *, raise_on=None):
        self.counts = dict(counts or {})
        self.raise_on = raise_on
        self.statements: list[str] = []
        self.savepoints = 0

    def begin_nested(self):
        return _Savepoint(self)

    async def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.statements.append(sql)
        table = sql.split("FROM ", 1)[1].split()[0]
        if self.raise_on and table == self.raise_on:
            raise RuntimeError("relation does not exist")
        return _Scalar(self.counts.get(table, 0))


class _Recorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def warning(self, event, **kwargs):
        self.events.append((event, kwargs))


async def test_counts_only_positive_refs_highest_first_inside_a_savepoint():
    db = _CountingDB({"widgets": 2, "steps": 5})
    report = await rr.references_broken(db, [uuid.uuid4()], refs=rr.set_null_refs(_throwaway()))
    assert report.measured is True and db.savepoints == 1
    assert [(i["table"], i["count"]) for i in report.items] == [("steps", 5), ("widgets", 2)]
    assert report.items[1]["label"] == "2 widgets rows lose their baseline_id link"
    assert report.job_record() == {"measured": True, "total": 7, "steps.source_run_id": 5, "widgets.baseline_id": 2}


async def test_nothing_broken_is_measured_and_records_no_job_value():
    report = await rr.references_broken(_CountingDB(), [uuid.uuid4()], refs=rr.set_null_refs(_throwaway()))
    assert report.measured is True and report.items == []
    assert report.job_record() is None, "NULL on the job = measured, nothing broke"


async def test_no_run_ids_issue_no_statement():
    db = _CountingDB({"widgets": 9})
    report = await rr.references_broken(db, [], refs=rr.set_null_refs(_throwaway()))
    assert report.measured is True and report.items == [] and db.statements == [] and db.savepoints == 0


async def test_a_failing_count_fails_soft_and_logs_one_warning(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(rr, "_slog", recorder)
    db = _CountingDB({"steps": 5}, raise_on="widgets")
    report = await rr.references_broken(db, [uuid.uuid4()], refs=rr.set_null_refs(_throwaway()))
    assert report.measured is False and report.items == []
    assert report.job_record() == {"measured": False}
    assert recorder.events == [(
        "retention_references_unmeasured", {"error_type": "RuntimeError", "refs": 2, "run_ids": 1},
    )]


async def test_a_session_without_savepoints_lands_in_the_fail_soft_branch(monkeypatch):
    monkeypatch.setattr(rr, "_slog", _Recorder())

    class _NoSavepoint:
        async def execute(self, stmt):  # pragma: no cover - never reached
            raise AssertionError("counted outside a savepoint")

    report = await rr.references_broken(_NoSavepoint(), [uuid.uuid4()])
    assert report.measured is False
