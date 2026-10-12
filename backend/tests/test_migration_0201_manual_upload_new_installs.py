"""Migration 0201: ``manual_upload`` starts ON for a new install, only for one.

The Postgres behaviour (a used database keeps its value, an unused one flips)
is exercised against a real database in
``tests/integration/test_manual_upload_default_postgres.py``; this file pins
the migration's shape without one.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0201_manual_upload_on_for_new_installs.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0201", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_the_previous_head():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0201", "0200")


def test_upgrade_flips_only_the_seeded_value_on_an_unused_database():
    migration = _module()
    sql = migration.UPGRADE_SQL
    assert "key = 'manual_upload'" in sql
    assert "SET enabled_global = true" in sql
    # Only while the row still holds 0092's seed ...
    assert "AND enabled_global = false" in sql
    # ... and only on a database nobody has used yet.
    assert "NOT EXISTS (SELECT 1 FROM users)" in sql
    assert "NOT EXISTS (SELECT 1 FROM projects)" in sql


def test_downgrade_touches_the_same_databases_only():
    migration = _module()
    assert migration.FRESH_INSTALL in migration.UPGRADE_SQL
    assert migration.FRESH_INSTALL in migration.DOWNGRADE_SQL
    assert "SET enabled_global = false" in migration.DOWNGRADE_SQL


def test_upgrade_and_downgrade_execute_their_statements():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    migration.downgrade()
    executed = [str(call.args[0]) for call in migration.op.execute.call_args_list]
    assert executed == [migration.UPGRADE_SQL, migration.DOWNGRADE_SQL]
