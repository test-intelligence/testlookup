"""Migration 0214 (W07 FAIL-05): ``test_runs.ai_recommendation``.

Shape only (no database): the chain; the column, then the batched backfill in
an autocommit block, then the CONCURRENT index build (an INVALID leftover
dropped first); the downgrade order; the keyset paging of ``backfill`` (bound
ids, never spliced); and the ORM declaring the same column and index. The
PostgreSQL behaviour -- the frozen SQL against ``run_verdict.ai_recommendation_of``
-- runs in ``tests/integration/test_migration_0214_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0214_test_run_ai_recommendation.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0214", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0213():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0214", "0213")
    assert "Revision ID: 0214" in migration.__doc__ and "Revises: 0213" in migration.__doc__


def test_the_constants():
    migration = _module()
    assert migration.TABLE == "test_runs"
    assert migration.COLUMN == "ai_recommendation"
    assert migration.INDEX == "ix_test_runs_project_recommendation_created"
    assert migration._BATCH == 1000


def test_the_frozen_schema_version_is_the_current_one():
    # Frozen at this revision on purpose; a later bump does not rewrite history,
    # but at the time of writing the two must agree or the backfill skips servable rows.
    from app.services.intelligence_snapshot_service import CURRENT_SCHEMA_VERSION

    assert _module()._SCHEMA_VERSION == CURRENT_SCHEMA_VERSION == 5


def _fake_op(invalid=False):
    """A MagicMock ``op`` recording each statement and whether it ran in the autocommit block."""
    op = MagicMock()
    op.get_context.return_value.as_sql = False
    state = {"autocommit": False}
    log: list[tuple[str, bool]] = []

    class _Block:
        def __enter__(self):
            state["autocommit"] = True

        def __exit__(self, *exc):
            state["autocommit"] = False
            return False

    op.get_context.return_value.autocommit_block.side_effect = lambda: _Block()
    op.execute.side_effect = lambda sql, *a, **k: log.append((str(sql), state["autocommit"]))
    op.add_column.side_effect = lambda *a, **k: log.append(("ADD COLUMN", state["autocommit"]))
    op.drop_column.side_effect = lambda *a, **k: log.append(("DROP COLUMN", state["autocommit"]))
    op.get_bind.return_value.execute.return_value.first.return_value = (1,) if invalid else None
    return op, log, state


CREATE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_runs_project_recommendation_created "
    "ON test_runs (project_id, ai_recommendation, created_at)"
)
DROP = "DROP INDEX CONCURRENTLY IF EXISTS ix_test_runs_project_recommendation_created"


def _with_recorded_backfill(migration, log, state):
    def _backfill(bind, batch=1000):
        log.append(("BACKFILL", state["autocommit"]))
        return 3, 4

    migration.backfill = _backfill


def test_upgrade_adds_the_column_backfills_in_autocommit_then_builds_the_index_concurrently():
    migration = _module()
    migration.op, log, state = _fake_op()
    _with_recorded_backfill(migration, log, state)
    migration.upgrade()
    (table, column), _ = migration.op.add_column.call_args
    assert table == "test_runs"
    assert column.name == "ai_recommendation" and column.nullable is True
    assert column.type.length == 20
    assert column.server_default is None and column.default is None  # no table rewrite
    assert not migration.op.create_index.called
    assert log == [("ADD COLUMN", False), ("BACKFILL", True), (CREATE, True)]


def test_an_invalid_leftover_index_is_dropped_before_the_rebuild():
    migration = _module()
    migration.op, log, state = _fake_op(invalid=True)
    _with_recorded_backfill(migration, log, state)
    migration.upgrade()
    assert log == [("ADD COLUMN", False), ("BACKFILL", True), (DROP, True), (CREATE, True)]


def test_offline_sql_mode_runs_one_update_and_does_not_probe_the_database():
    migration = _module()
    migration.op, log, state = _fake_op()
    migration.op.get_context.return_value.as_sql = True
    migration.upgrade()
    assert not migration.op.get_bind.called
    assert [entry[0][:6] for entry in log] == ["ADD CO", "UPDATE", "CREATE"]
    update_sql = log[1][0]
    assert "updated_at = tr.updated_at" in update_sql
    assert "s.schema_version >= 5" in update_sql
    assert migration._PAYLOAD in update_sql
    assert log[2] == (CREATE, True)


def test_downgrade_drops_the_index_concurrently_then_the_column():
    migration = _module()
    migration.op, log, _ = _fake_op()
    migration.downgrade()
    assert log == [(DROP, True), ("DROP COLUMN", False)]
    migration.op.drop_column.assert_called_once_with("test_runs", "ai_recommendation")
    assert not migration.op.drop_index.called


class _Bind:
    """Answers the keyset query with consecutive slices of ``keys``; records every call."""

    def __init__(self, keys: list[str], updated_per_batch: int):
        self.keys = keys
        self.updated_per_batch = updated_per_batch
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params):
        sql = str(stmt)
        self.calls.append((sql, dict(params)))
        result = MagicMock()
        if sql.startswith("SELECT run_id"):
            after = params["after"]
            start = 0 if after is None else self.keys.index(after) + 1
            result.scalars.return_value = self.keys[start:start + params["batch"]]
        else:
            result.rowcount = min(self.updated_per_batch, len(params["ids"]))
        return result


def test_backfill_pages_by_keyset_and_binds_the_ids():
    migration = _module()
    keys = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, 6)]
    bind = _Bind(keys, updated_per_batch=2)
    assert migration.backfill(bind, batch=2) == (5, 5)
    selects = [params for sql, params in bind.calls if sql.startswith("SELECT run_id")]
    updates = [(sql, params) for sql, params in bind.calls if sql.startswith("UPDATE")]
    assert [p["after"] for p in selects] == [None, keys[1], keys[3], keys[4]]
    assert all(p["v"] == 5 and p["batch"] == 2 for p in selects)
    assert [p["ids"] for _, p in updates] == [keys[0:2], keys[2:4], keys[4:5]]
    for sql, params in updates:
        assert "ANY(CAST(:ids AS uuid[]))" in sql
        assert "updated_at = tr.updated_at" in sql
        assert not any(key in sql for key in keys), "ids are bound, never spliced"


def test_the_frozen_sql_reads_the_payload_as_jsonb_once():
    migration = _module()
    sql = str(migration._FILL)
    assert sql.count("AS jsonb)") == 1
    assert f"CROSS JOIN LATERAL (SELECT {migration._PAYLOAD} AS p) AS j" in sql


def test_the_frozen_payload_cast_is_the_batch_reads_at_this_revision():
    # Frozen on purpose (a later change to the read must not rewrite history), but
    # written as the same expression: \u0000 escapes neutralised before the jsonb cast.
    from app.services.run_verdict_service import JSONB_PAYLOAD_SQL

    assert _module()._PAYLOAD == JSONB_PAYLOAD_SQL
    assert "E'\\\\u0000'" in JSONB_PAYLOAD_SQL and "E'\\\\u0020'" in JSONB_PAYLOAD_SQL


def test_the_orm_declares_the_same_column_and_index():
    from app.models.postgres import TestRun

    column = TestRun.__table__.c.ai_recommendation
    assert column.nullable is True and column.type.length == 20
    assert column.server_default is None
    (index,) = [i for i in TestRun.__table__.indexes if i.name == "ix_test_runs_project_recommendation_created"]
    assert [c.name for c in index.columns] == ["project_id", "ai_recommendation", "created_at"]
    assert index.unique is not True
