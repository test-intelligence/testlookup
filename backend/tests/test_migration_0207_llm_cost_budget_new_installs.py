"""Migration 0207 (AIC-06): ``llm_cost_budget`` starts ON for a new install, only for one.

Pins the migration's shape without a database: the flip uses the fresh-install
guard of 0201 / 0204 and only moves 0064's seeded OFF, and the downgrade is the
mirror (only an untouched database goes back). The Postgres behaviour runs in
``tests/integration/test_migration_0207_0209_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0207_llm_cost_budget_on_for_new_installs.py"
FRESH_INSTALL = "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0207", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_the_retention_caps_migration():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0207", "0206")
    assert "Revision ID: 0207" in migration.__doc__ and "Revises: 0206" in migration.__doc__


def test_the_fresh_install_predicate_is_0201s_verbatim():
    migration = _module()
    spec = importlib.util.spec_from_file_location("migration_0201", VERSIONS / "0201_manual_upload_on_for_new_installs.py")
    assert spec and spec.loader
    m0201 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m0201)
    assert migration.FRESH_INSTALL == m0201.FRESH_INSTALL == FRESH_INSTALL


def test_upgrade_flips_only_the_seeded_off_on_an_unused_database():
    migration = _module()
    assert migration.UPGRADE_SQL == (
        "UPDATE feature_flags SET enabled_global = true, updated_at = now() "
        "WHERE key = 'llm_cost_budget' AND enabled_global = false AND " + FRESH_INSTALL
    )


def test_downgrade_is_the_mirror_on_the_same_databases():
    migration = _module()
    assert migration.DOWNGRADE_SQL == (
        "UPDATE feature_flags SET enabled_global = false, updated_at = now() "
        "WHERE key = 'llm_cost_budget' AND enabled_global = true AND " + FRESH_INSTALL
    )


def test_upgrade_and_downgrade_execute_exactly_their_statement():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [migration.UPGRADE_SQL]
    migration.op = MagicMock()
    migration.downgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [migration.DOWNGRADE_SQL]
    # Data only: no DDL in either direction.
    assert not migration.op.add_column.called and not migration.op.drop_column.called
