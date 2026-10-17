"""Migration 0219 (W09 FLK-01, D11): the quarantine flag ships ON where nobody ever set it.

Shape only (no database): the chain after 0218; one UPDATE that flips only
0065's untouched seed (``enabled_global = false``, no actor, ``updated_at =
created_at``) and writes the marker literally; a downgrade that reverts only
the row the upgrade flipped (still ON, no actor, the marker) back to the
untouched seed exactly; the key visible to the feature-flag catalogue guard.
The PostgreSQL behaviour runs in ``tests/integration/test_migration_0219_0221_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0219_flaky_quarantine_on.py"
MARKER = " Default ON since migration 0219: a test is quarantined only after a QA lead approves it."
UNTOUCHED_SEED = "enabled_global = false AND updated_by_user_id IS NULL AND updated_at = created_at"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0219", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0218():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0219", "0218")
    assert "Revision ID: 0219" in migration.__doc__ and "Revises: 0218" in migration.__doc__


def test_the_constants():
    migration = _module()
    assert migration._FLAGS == ("flaky_auto_quarantine",)
    assert migration.MARKER == MARKER
    assert migration.UNTOUCHED_SEED == UNTOUCHED_SEED
    # Written literally inside SQL text and matched by strpos: none of these may appear.
    for forbidden in ("'", "%", "_"):
        assert forbidden not in migration.MARKER


def test_upgrade_flips_only_the_untouched_seed_and_writes_the_marker():
    migration = _module()
    assert migration.UPGRADE_SQL == (
        "UPDATE feature_flags SET enabled_global = true, updated_at = now(), "
        f"description = COALESCE(description, '') || '{MARKER}' "
        "WHERE key = 'flaky_auto_quarantine' AND " + UNTOUCHED_SEED
    )


def test_downgrade_reverts_only_the_flipped_row_to_the_seed_exactly():
    migration = _module()
    assert migration.DOWNGRADE_SQL == (
        "UPDATE feature_flags SET enabled_global = false, updated_at = created_at, "
        f"description = REPLACE(description, '{MARKER}', '') "
        "WHERE key = 'flaky_auto_quarantine' AND enabled_global = true AND updated_by_user_id IS NULL "
        f"AND strpos(description, '{MARKER}') > 0"
    )


def test_upgrade_and_downgrade_execute_exactly_their_statement():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [migration.UPGRADE_SQL]
    migration.op = MagicMock()
    migration.downgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [migration.DOWNGRADE_SQL]
    assert not migration.op.add_column.called and not migration.op.create_table.called


def test_the_sql_binds_no_parameter():
    """``sa.text`` treats ``:name`` as a bind parameter; the marker's colon is followed by a space."""
    import sqlalchemy as sa

    migration = _module()
    for statement in (migration.UPGRADE_SQL, migration.DOWNGRADE_SQL):
        assert sa.text(statement).compile().params == {}


def test_the_catalogue_guard_sees_the_key():
    from tests.regression.test_feature_flag_catalogue_is_complete import _keys_in_migration

    assert "flaky_auto_quarantine" in _keys_in_migration(MIGRATION.read_text(encoding="utf-8"))
