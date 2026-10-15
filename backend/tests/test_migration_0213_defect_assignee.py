"""Migration 0213 (DEF-01): ``defects.assignee_user_id``.

Shape only (no database): the chain, the column / FK / partial index the ORM
declares too, the CONCURRENT index build in the autocommit block (an INVALID
leftover dropped first), and a downgrade that drops exactly what the upgrade
added. The PostgreSQL behaviour runs in
``tests/integration/test_migration_0213_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0213_defect_assignee.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0213", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0212():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0213", "0212")
    assert "Revision ID: 0213" in migration.__doc__ and "Revises: 0212" in migration.__doc__


def test_the_constants_name_the_column_and_the_partial_index():
    migration = _module()
    assert migration.TABLE == "defects"
    assert migration.COLUMN == "assignee_user_id"
    assert migration.INDEX == "ix_defects_project_assignee"
    assert migration.INDEX_WHERE == "assignee_user_id IS NOT NULL"


def _fake_op(invalid=False):
    """A MagicMock ``op`` that records each statement and whether it ran in the autocommit block."""
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
    op.get_bind.return_value.execute.return_value.first.return_value = (1,) if invalid else None
    return op, log


CREATE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_defects_project_assignee "
    "ON defects (project_id, assignee_user_id) WHERE assignee_user_id IS NOT NULL"
)
DROP = "DROP INDEX CONCURRENTLY IF EXISTS ix_defects_project_assignee"


def test_upgrade_adds_the_column_then_builds_the_index_concurrently():
    migration = _module()
    migration.op, log = _fake_op()
    migration.upgrade()
    (table, column), _ = migration.op.add_column.call_args
    assert table == "defects"
    assert column.name == "assignee_user_id" and column.nullable is True
    assert column.server_default is None and column.default is None  # no rewrite, no backfill
    (fk,) = list(column.foreign_keys)
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    assert not migration.op.create_index.called
    assert log == [(CREATE, True)]


def test_an_invalid_leftover_index_is_dropped_before_the_rebuild():
    migration = _module()
    migration.op, log = _fake_op(invalid=True)
    migration.upgrade()
    assert log == [(DROP, True), (CREATE, True)]


def test_offline_sql_mode_does_not_probe_the_database():
    migration = _module()
    migration.op, log = _fake_op()
    migration.op.get_context.return_value.as_sql = True
    migration.upgrade()
    assert not migration.op.get_bind.called
    assert log == [(CREATE, True)]


def test_downgrade_drops_the_index_concurrently_then_the_column():
    migration = _module()
    migration.op, log = _fake_op()
    migration.downgrade()
    assert log == [(DROP, True)]
    migration.op.drop_column.assert_called_once_with("defects", "assignee_user_id")
    assert not migration.op.drop_index.called


def test_the_orm_declares_the_same_column_and_index():
    from app.models.postgres import Defect

    column = Defect.__table__.c.assignee_user_id
    assert column.nullable is True
    (fk,) = list(column.foreign_keys)
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    (index,) = [i for i in Defect.__table__.indexes if i.name == "ix_defects_project_assignee"]
    assert [c.name for c in index.columns] == ["project_id", "assignee_user_id"]
    assert index.unique is not True
    assert str(index.dialect_options["postgresql"]["where"]) == "assignee_user_id IS NOT NULL"


def test_a_second_fk_to_users_leaves_the_approver_relationship_unambiguous():
    from sqlalchemy.orm import configure_mappers

    from app.models.postgres import Defect

    configure_mappers()
    approver = Defect.__mapper__.relationships["approver"]
    assert [c.name for c in approver.local_columns] == ["approved_by"]
