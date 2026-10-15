"""Migration 0211 (REL-02): ``releases.owner_user_id``.

Shape only (no database): the chain, the column / FK / index the ORM declares
too, the backfill that copies ``created_by_id`` for real people only, and a
downgrade that drops exactly what the upgrade added. The PostgreSQL behaviour
runs in ``tests/integration/test_migration_0211_0212_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0211_release_owner.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0211", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0210():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0211", "0210")
    assert "Revision ID: 0211" in migration.__doc__ and "Revises: 0210" in migration.__doc__


def test_the_backfill_copies_the_creator_only_for_a_real_person():
    migration = _module()
    assert migration.BACKFILL_SQL == (
        "UPDATE releases r SET owner_user_id = r.created_by_id FROM users u "
        "WHERE u.id = r.created_by_id AND r.owner_user_id IS NULL "
        "AND u.is_service_account IS NOT TRUE AND u.is_synthetic IS NOT TRUE "
        "AND u.is_active IS NOT FALSE"
    )
    # An owner backfill is not an edit.
    assert "updated_at" not in migration.BACKFILL_SQL


def test_upgrade_adds_the_column_index_and_backfill():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    (table, column), _ = migration.op.add_column.call_args
    assert table == "releases"
    assert column.name == "owner_user_id" and column.nullable is True
    (fk,) = list(column.foreign_keys)
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    index_call = migration.op.create_index.call_args
    assert index_call.args == ("ix_releases_owner", "releases", ["owner_user_id"])
    assert str(index_call.kwargs["postgresql_where"]) == "owner_user_id IS NOT NULL"
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [migration.BACKFILL_SQL]


def test_downgrade_drops_the_index_then_the_column():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    migration.op.drop_index.assert_called_once_with("ix_releases_owner", table_name="releases")
    migration.op.drop_column.assert_called_once_with("releases", "owner_user_id")
    assert not migration.op.execute.called


def test_the_orm_declares_the_same_column_and_index():
    from app.models.postgres import Release

    column = Release.__table__.c.owner_user_id
    assert column.nullable is True
    (fk,) = list(column.foreign_keys)
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    (index,) = [i for i in Release.__table__.indexes if i.name == "ix_releases_owner"]
    assert [c.name for c in index.columns] == ["owner_user_id"]
    assert index.unique is not True
    assert str(index.dialect_options["postgresql"]["where"]) == "owner_user_id IS NOT NULL"
