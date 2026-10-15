"""Migration 0204 (EXEC-05): ``test_runs.duration_findings`` and
``perf_regression_detection`` ON for a new install only.

Pins the migration's shape without a database: the column is a nullable
JSONB (NULL = "not evaluated", never "no regressions"), the flag flip uses the
fresh-install guard of 0201 and only moves 0069's seeded value, and the
downgrade undoes exactly what the upgrade did, in reverse.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0204_duration_findings.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0204", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_the_suite_executions_migration():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0204", "0203")


def test_the_flag_flips_only_the_seeded_value_on_an_unused_database():
    migration = _module()
    sql = migration.UPGRADE_SQL
    assert "key = 'perf_regression_detection'" in sql
    assert "SET enabled_global = true" in sql
    assert "AND enabled_global = false" in sql
    assert "NOT EXISTS (SELECT 1 FROM users)" in sql
    assert "NOT EXISTS (SELECT 1 FROM projects)" in sql
    assert migration.FRESH_INSTALL in migration.DOWNGRADE_SQL
    assert "SET enabled_global = false" in migration.DOWNGRADE_SQL
    assert "AND enabled_global = true" in migration.DOWNGRADE_SQL


def test_upgrade_adds_a_nullable_jsonb_column_then_flips_the_flag():
    from sqlalchemy.dialects import postgresql

    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    [add] = migration.op.add_column.call_args_list
    table, column = add.args
    assert table == "test_runs"
    assert column.name == "duration_findings"
    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is True
    assert column.server_default is None, "no default: existing runs stay NULL (not evaluated)"
    [flip] = migration.op.execute.call_args_list
    assert str(flip.args[0]) == migration.UPGRADE_SQL
    migration.op.drop_column.assert_not_called()


def test_downgrade_undoes_exactly_the_upgrade_in_reverse():
    migration = _module()
    calls = MagicMock()
    migration.op = calls
    migration.downgrade()
    names = [c[0] for c in calls.mock_calls]
    assert names == ["execute", "drop_column"], names
    assert str(calls.execute.call_args.args[0]) == migration.DOWNGRADE_SQL
    assert calls.drop_column.call_args.args == ("test_runs", "duration_findings")


def test_the_orm_column_matches_the_migration():
    from sqlalchemy.dialects import postgresql

    from app.models.postgres import TestRun

    column = TestRun.__table__.c.duration_findings
    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is True
