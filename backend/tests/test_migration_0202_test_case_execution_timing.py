"""Migration 0202: test results keep when they ran (EXEC-01).

Pins the shape without a database: the revision chain, the seven nullable
columns and their types, the index built CONCURRENTLY in an autocommit block,
a downgrade that undoes exactly the upgrade, and an ORM that declares the same
columns and index. The real DDL (up, down, up) runs against Postgres in
``tests/integration/test_test_case_execution_timing_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

from app.models.postgres import TestCase as _TestCase  # aliased so pytest doesn't collect it

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0202_test_case_execution_timing.py"
)

EXPECTED = {
    "started_at": sa.DateTime,
    "finished_at": sa.DateTime,
    "setup_ms": sa.Integer,
    "call_ms": sa.Integer,
    "teardown_ms": sa.Integer,
    "worker_id": sa.String,
    "timing_source": sa.String,
}


def _module():
    spec = importlib.util.spec_from_file_location("migration_0202", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ran(migration, step: str) -> MagicMock:
    migration.op = MagicMock()
    migration.op.get_context.return_value.as_sql = True  # offline: no catalog probe
    getattr(migration, step)()
    return migration.op


def test_it_follows_0201():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0202", "0201")


def test_upgrade_adds_seven_nullable_columns_of_the_right_types():
    op = _ran(_module(), "upgrade")
    added = {call.args[1].name: call.args[1] for call in op.add_column.call_args_list}
    assert {call.args[0] for call in op.add_column.call_args_list} == {"test_cases"}
    assert set(added) == set(EXPECTED)
    for name, kind in EXPECTED.items():
        assert isinstance(added[name].type, kind), name
        assert added[name].nullable is True and added[name].server_default is None, name
    assert added["started_at"].type.timezone is True
    assert added["finished_at"].type.timezone is True
    assert added["worker_id"].type.length == 64
    assert added["timing_source"].type.length == 16


def test_upgrade_builds_the_index_concurrently_in_an_autocommit_block():
    op = _ran(_module(), "upgrade")
    op.get_context.return_value.autocommit_block.assert_called_once()
    executed = [str(call.args[0]) for call in op.execute.call_args_list]
    assert executed == [
        "DROP INDEX CONCURRENTLY IF EXISTS ix_test_cases_run_duration",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_cases_run_duration "
        "ON test_cases (test_run_id, duration_ms DESC NULLS LAST)",
    ]


def test_downgrade_undoes_exactly_the_upgrade():
    """Including the index: 0004 built ``(test_run_id, duration_ms)`` under the
    same name, and the upgrade replaced it, so the downgrade puts it back."""
    migration = _module()
    op = _ran(migration, "downgrade")
    op.get_context.return_value.autocommit_block.assert_called_once()
    assert [str(call.args[0]) for call in op.execute.call_args_list] == [
        "DROP INDEX CONCURRENTLY IF EXISTS ix_test_cases_run_duration",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_cases_run_duration "
        "ON test_cases (test_run_id, duration_ms)",
    ]
    previous = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0004_performance_indexes.py")
    assert '"ix_test_cases_run_duration", "test_cases", ["test_run_id", "duration_ms"]' in previous.read_text("utf-8")
    dropped = [call.args for call in op.drop_column.call_args_list]
    assert dropped == [("test_cases", name) for name in reversed(migration.COLUMNS)]
    assert set(migration.COLUMNS) == set(EXPECTED)


def test_the_orm_declares_the_same_columns_and_index():
    table = _TestCase.__table__
    for name, kind in EXPECTED.items():
        assert isinstance(table.c[name].type, kind), name
        assert table.c[name].nullable is True, name
    assert table.c.worker_id.type.length == 64
    assert table.c.timing_source.type.length == 16
    (index,) = [ix for ix in table.indexes if ix.name == "ix_test_cases_run_duration"]
    rendered = str(sa.schema.CreateIndex(index).compile(dialect=sa.dialects.postgresql.dialect()))
    assert rendered.endswith("ON test_cases (test_run_id, duration_ms DESC NULLS LAST)")
