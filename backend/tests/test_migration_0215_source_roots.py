"""Migration 0215 (W07 FAIL-08): ``projects.source_roots``.

Shape only: the chain, a nullable JSON column with no default (no rewrite, no
backfill, no index), a downgrade that drops exactly it, and the ORM declaring
the same column. PostgreSQL: ``tests/integration/test_migration_0215_0216_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0215_project_source_roots.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0215", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0214():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0215", "0214")
    assert "Revision ID: 0215" in migration.__doc__ and "Revises: 0214" in migration.__doc__


def test_upgrade_adds_one_nullable_json_column_and_nothing_else():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    (table, column), _ = migration.op.add_column.call_args
    assert table == "projects"
    assert column.name == "source_roots" and column.nullable is True
    assert isinstance(column.type, sa.JSON)
    assert column.server_default is None and column.default is None
    assert not column.index
    assert not migration.op.execute.called
    assert not migration.op.create_index.called


def test_downgrade_drops_the_column():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    migration.op.drop_column.assert_called_once_with("projects", "source_roots")


def test_the_orm_declares_the_same_column():
    from app.models.postgres import Project

    column = Project.__table__.c.source_roots
    assert column.nullable is True
    assert isinstance(column.type, sa.JSON)
    assert column.server_default is None
