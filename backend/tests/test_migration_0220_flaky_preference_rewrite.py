"""Migration 0220 (W09 NTF-01): stored "flaky_test_detected" preferences become "test.newly_flaky".

Shape only (no database): the chain after 0219; the alias pair equals the
API's ``schemas.LEGACY_EVENT_ALIASES`` (the migration repeats the literal
because migrations import no app code); the bookkeeping table is created with
the preference id as its only key and dropped by the downgrade; the
statements touch only array rows holding the alias. The PostgreSQL behaviour
(order kept, duplicates dropped, NULL / non-array rows untouched, the exact
membership restore) runs in ``tests/integration/test_migration_0219_0221_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0220_flaky_preference_rewrite.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0220", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0219():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0220", "0219")
    assert "Revision ID: 0220" in migration.__doc__ and "Revises: 0219" in migration.__doc__


def test_the_alias_is_the_apis():
    from app.models.schemas import LEGACY_EVENT_ALIASES

    migration = _module()
    assert {migration.ALIAS_FROM: migration.ALIAS_TO} == LEGACY_EVENT_ALIASES
    assert (migration.ALIAS_FROM, migration.ALIAS_TO) == ("flaky_test_detected", "test.newly_flaky")
    assert migration.MARKER_TABLE == "notification_pref_rewrites_0220"


def test_every_statement_is_limited_to_array_rows_holding_the_alias_or_the_marker_table():
    migration = _module()
    holds = "jsonb_typeof(events) = 'array' AND events @> '[\"flaky_test_detected\"]'::jsonb"
    assert migration.HOLDS_ALIAS == holds
    assert migration.RECORD_SQL == (
        "INSERT INTO notification_pref_rewrites_0220 (preference_id, had_alias_target) "
        "SELECT id, events @> '[\"test.newly_flaky\"]'::jsonb FROM notification_preferences "
        f"WHERE {holds} ON CONFLICT DO NOTHING"
    )
    assert migration.REWRITE_SQL.startswith("UPDATE notification_preferences p SET events = (")
    assert migration.REWRITE_SQL.endswith(
        ") WHERE jsonb_typeof(p.events) = 'array' AND p.events @> '[\"flaky_test_detected\"]'::jsonb"
    )
    # Order kept (by the first position of each value), duplicates dropped (GROUP BY the mapped value).
    assert "jsonb_agg(v ORDER BY first_pos)" in migration.REWRITE_SQL
    assert "MIN(pos) AS first_pos" in migration.REWRITE_SQL and "GROUP BY v" in migration.REWRITE_SQL
    assert "WITH ORDINALITY" in migration.REWRITE_SQL
    for restore in (migration.RESTORE_BOTH_SQL, migration.RESTORE_ALIAS_SQL):
        assert "FROM notification_pref_rewrites_0220 m WHERE m.preference_id = p.id" in restore
        assert "AND NOT p.events @> '[\"flaky_test_detected\"]'::jsonb" in restore
    assert "AND m.had_alias_target " in migration.RESTORE_BOTH_SQL
    assert "AND NOT m.had_alias_target " in migration.RESTORE_ALIAS_SQL


def test_upgrade_creates_the_bookkeeping_table_then_records_then_rewrites():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    (table, *columns), _ = migration.op.create_table.call_args
    assert table == "notification_pref_rewrites_0220"
    by_name = {c.name: c for c in columns}
    assert set(by_name) == {"preference_id", "had_alias_target"}
    pref = by_name["preference_id"]
    assert pref.primary_key is True
    (fk,) = list(pref.foreign_keys)
    assert fk.target_fullname == "notification_preferences.id" and fk.ondelete == "CASCADE"
    assert by_name["had_alias_target"].nullable is False
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [
        migration.RECORD_SQL,
        migration.REWRITE_SQL,
    ]
    assert not migration.op.create_index.called


def test_downgrade_restores_then_drops_the_table():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    assert [str(c.args[0]) for c in migration.op.execute.call_args_list] == [
        migration.RESTORE_BOTH_SQL,
        migration.RESTORE_ALIAS_SQL,
    ]
    migration.op.drop_table.assert_called_once_with("notification_pref_rewrites_0220")


def test_the_sql_binds_no_parameter():
    """``::jsonb`` casts must not be read as ``:name`` bind parameters."""
    import sqlalchemy as sa

    migration = _module()
    for statement in (
        migration.RECORD_SQL,
        migration.REWRITE_SQL,
        migration.RESTORE_BOTH_SQL,
        migration.RESTORE_ALIAS_SQL,
    ):
        assert sa.text(statement).compile().params == {}
