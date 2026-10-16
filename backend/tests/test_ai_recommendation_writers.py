"""The writers of ``test_runs.ai_recommendation`` (W07 FAIL-05), statement by statement.

The column is the projection of the run's servable snapshot, so it is written
exactly where the snapshot is: ``save_snapshot`` sets it to
``run_verdict.ai_recommendation_of`` of the payload it STORES, in the
snapshot's own transaction (before the commit); ``invalidate`` (commits) and
``stage_invalidate`` (stage-only: the caller commits) clear it with the delete;
``mark_stale`` leaves it (a stale snapshot is still served); the
``defect_candidates_pending`` return writes nothing at all. Every write sets
``updated_at`` to itself, so ``onupdate=func.now()`` never fires.

The real-database round trip (values read back, ``updated_at`` unchanged) is
``tests/integration/test_runs_list_filters_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Delete, Update

from app.services import intelligence_snapshot_service as snapshots
from tests.test_run_verdict import CASES

RUN = uuid.uuid4()


class _Result:
    rowcount = 1

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return SimpleNamespace(all=lambda: [], first=lambda: None)


class _Db:
    def __init__(self):
        self.journal: list = []
        self.added: list = []

    async def execute(self, statement, *args, **kwargs):
        self.journal.append(statement)
        return _Result()

    def add(self, row):
        self.added.append(row)
        self.journal.append("add")

    async def commit(self):
        self.journal.append("commit")


def _updates(db: _Db) -> list[Update]:
    return [s for s in db.journal if isinstance(s, Update)]


def _compiled(statement: Update) -> tuple[str, dict]:
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


def _assert_projection(statement: Update, value):
    sql, params = _compiled(statement)
    assert sql.startswith("UPDATE test_runs SET ai_recommendation=")
    assert "updated_at=test_runs.updated_at" in sql, "updated_at is set to itself: onupdate never fires"
    assert "WHERE test_runs.id = " in sql
    assert params["ai_recommendation"] == value
    assert RUN in params.values()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_save_snapshot_projects_the_stored_payload_before_its_commit(case):
    db = _Db()
    await snapshots.save_snapshot(db, RUN, case["full"])
    (update,) = _updates(db)
    _assert_projection(update, case["recommendation"])
    assert db.journal.index(update) < db.journal.index("commit"), "one transaction: written before the commit"
    assert db.journal.count("commit") == 1


async def test_save_snapshot_projects_what_it_stores_not_what_it_was_handed():
    # The memory-manifest enrichment and the JSON encoding happen first; the
    # projection reads the payload that lands in the column.
    db = _Db()
    await snapshots.save_snapshot(db, RUN, {"release_decision": {"recommendation": "GO"}, "provenance": {}})
    (row,) = db.added
    assert "memory_reference_manifest" in row.payload
    _assert_projection(_updates(db)[0], "GO")


async def test_a_pending_payload_writes_nothing():
    db = _Db()
    await snapshots.save_snapshot(db, RUN, {**CASES[0]["full"], "defect_candidates_pending": True})
    assert db.journal == [] and db.added == []


async def test_invalidate_clears_it_with_the_delete_then_commits():
    # The run's row first, then the snapshot's: the lock order of a run deletion's cascade.
    db = _Db()
    assert await snapshots.invalidate(db, RUN) is True
    update, delete, commit = db.journal
    _assert_projection(update, None)
    assert isinstance(delete, Delete)
    assert commit == "commit"


async def test_stage_invalidate_clears_it_and_leaves_the_commit_to_the_caller():
    db = _Db()
    assert await snapshots.stage_invalidate(db, RUN) is True
    update, delete = db.journal
    _assert_projection(update, None)
    assert isinstance(delete, Delete)
    assert "commit" not in db.journal


async def test_save_snapshot_locks_the_run_before_writing_the_snapshot_row():
    db = _Db()
    await snapshots.save_snapshot(db, RUN, CASES[0]["full"])
    (update,) = _updates(db)
    assert db.journal.index(update) < db.journal.index("add")


async def test_mark_stale_leaves_it():
    db = _Db()
    assert await snapshots.mark_stale(db, RUN) is False  # no row in this fake; nothing is written either way
    assert _updates(db) == []
