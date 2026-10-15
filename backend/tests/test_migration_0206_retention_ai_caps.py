"""Migration 0206: per-project caps on kept AI history versions (AIH-06, D41).

Two nullable INTEGER columns on ``project_retention_policies`` (NULL = the code
defaults 20 / 10), each bounded 1-100 by a named CHECK; the downgrade drops the
constraints then the columns; the ORM declares the same. The DDL runs against
Postgres in ``tests/integration/test_ai_analysis_versions_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call

import sqlalchemy as sa

from app.models.postgres import ProjectRetentionPolicy

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0206_retention_ai_history_caps.py"
EXPECTED = {
    "ai_versions_per_run": "ck_retention_ai_versions_per_run",
    "ai_versions_per_test": "ck_retention_ai_versions_per_test",
}


def _module():
    spec = importlib.util.spec_from_file_location("migration_0206", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ran(migration, step: str) -> MagicMock:
    migration.op = MagicMock()
    getattr(migration, step)()
    return migration.op


def test_it_follows_0205():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0206", "0205")


def test_upgrade_adds_two_nullable_integers_with_their_bounds():
    op = _ran(_module(), "upgrade")
    added = {c.args[1].name: c.args[1] for c in op.add_column.call_args_list}
    assert {c.args[0] for c in op.add_column.call_args_list} == {"project_retention_policies"}
    assert set(added) == set(EXPECTED)
    for column in added.values():
        assert isinstance(column.type, sa.Integer) and column.nullable is True and column.server_default is None
    assert op.create_check_constraint.call_args_list == [
        call(name, "project_retention_policies", f"{col} IS NULL OR {col} BETWEEN 1 AND 100")
        for col, name in EXPECTED.items()
    ]


def test_downgrade_drops_the_constraints_then_the_columns():
    op = _ran(_module(), "downgrade")
    assert op.drop_constraint.call_args_list == [
        call(name, "project_retention_policies", type_="check") for name in reversed(list(EXPECTED.values()))
    ]
    assert op.drop_column.call_args_list == [
        call("project_retention_policies", col) for col in reversed(list(EXPECTED))
    ]
    op.add_column.assert_not_called()


def test_the_orm_declares_the_same():
    table = ProjectRetentionPolicy.__table__
    for col, name in EXPECTED.items():
        assert isinstance(table.c[col].type, sa.Integer) and table.c[col].nullable is True, col
        (check,) = [c for c in table.constraints if isinstance(c, sa.CheckConstraint) and c.name == name]
        assert str(check.sqltext) == f"{col} IS NULL OR {col} BETWEEN 1 AND 100"
