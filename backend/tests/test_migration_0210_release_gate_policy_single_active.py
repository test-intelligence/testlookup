"""Migration 0210 (GATE-03): at most one active release-gate policy per scope.

Pins the migration's shape without a database: the chain, the statement
constants, that the ORM declares the SAME three indexes (names and predicates),
and that the downgrade drops exactly those indexes and touches no data. The
PostgreSQL behaviour (data fix, index enforcement, idempotent re-run) runs in
``tests/integration/test_release_gate_policy_single_active_postgres.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0210_release_gate_policy_single_active.py"

EXPECTED_INDEXES = {
    "uq_rgp_one_active_project": ("project_id", "is_active IS TRUE AND project_id IS NOT NULL"),
    "uq_rgp_one_active_system": ("is_active", "is_active IS TRUE AND project_id IS NULL"),
    "uq_rgp_system_version": ("version", "project_id IS NULL"),
}


def _module():
    spec = importlib.util.spec_from_file_location("migration_0210", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_follows_0209():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0210", "0209")
    assert "Revision ID: 0210" in migration.__doc__ and "Revises: 0209" in migration.__doc__


def test_the_three_indexes_are_the_contracts():
    migration = _module()
    assert {name: (col, pred) for name, col, pred in migration.INDEXES} == EXPECTED_INDEXES


def test_the_orm_declares_the_same_indexes():
    from app.models.postgres import ReleaseGatePolicy

    declared = {
        index.name: index
        for index in ReleaseGatePolicy.__table__.indexes
        if index.name in EXPECTED_INDEXES
    }
    assert set(declared) == set(EXPECTED_INDEXES), "model and migration must name the same indexes"
    for name, (column, predicate) in EXPECTED_INDEXES.items():
        index = declared[name]
        assert index.unique is True, name
        assert [c.name for c in index.columns] == [column], name
        where = index.dialect_options["postgresql"]["where"]
        assert str(where) == predicate, name
    # The pre-existing guards are kept.
    names = {i.name for i in ReleaseGatePolicy.__table__.indexes}
    assert "ix_rgp_project_active" in names
    constraints = {c.name for c in ReleaseGatePolicy.__table__.constraints}
    assert "uq_rgp_project_version" in constraints


def test_the_data_fix_statements():
    migration = _module()
    assert migration.LOCK_SQL == "LOCK TABLE release_gate_policies IN SHARE ROW EXCLUSIVE MODE"
    # Only the system scope is renumbered: project scopes already had a guard.
    assert "WHERE project_id IS NULL" in migration.RENUMBER_SQL
    assert "ORDER BY created_at ASC NULLS LAST, id ASC" in migration.RENUMBER_SQL
    # The row resolve_effective_policy preferred stays active.
    assert (
        "PARTITION BY project_id ORDER BY version DESC, activated_at DESC NULLS LAST, "
        "created_at DESC, id DESC"
    ) in migration.DEMOTE_SQL
    assert "r.rn > 1" in migration.DEMOTE_SQL and "RETURNING" in migration.DEMOTE_SQL


def test_upgrade_runs_lock_fix_then_indexes_in_order():
    migration = _module()
    migration.op = MagicMock()
    bind = migration.op.get_bind.return_value
    bind.execute.return_value.fetchall.return_value = []
    migration.upgrade()
    executed = [str(c.args[0]) for c in migration.op.execute.call_args_list]
    assert executed == [migration.LOCK_SQL]
    fixes = [str(c.args[0]) for c in bind.execute.call_args_list]
    # Demote BEFORE renumbering: renumbering first could lift a duplicate above
    # the row the evaluator was using, and keep a different policy active.
    assert fixes == [migration.DEMOTE_SQL, migration.RENUMBER_SQL]
    created = [c.args[0] for c in migration.op.create_index.call_args_list]
    assert created == list(EXPECTED_INDEXES)
    for call in migration.op.create_index.call_args_list:
        assert call.kwargs["unique"] is True
        assert call.kwargs["if_not_exists"] is True
        assert "postgresql_concurrently" not in call.kwargs, "plain builds, atomic with the data fix"


def test_each_demotion_is_logged(caplog):
    import logging
    import uuid

    migration = _module()
    migration.op = MagicMock()
    bind = migration.op.get_bind.return_value
    demoted = uuid.uuid4()
    renumbered = uuid.uuid4()
    bind.execute.return_value.fetchall.side_effect = [[(demoted, None, 3)], [(renumbered, 7)]]
    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        migration.upgrade()
    assert f"0210 demoted duplicate active policy {demoted} (scope system, v3)" in caplog.text
    assert f"{renumbered} is now v7" in caplog.text


def test_downgrade_drops_exactly_the_indexes_and_no_data():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    dropped = [c.args[0] for c in migration.op.drop_index.call_args_list]
    assert sorted(dropped) == sorted(EXPECTED_INDEXES)
    for call in migration.op.drop_index.call_args_list:
        assert call.kwargs["if_exists"] is True
    assert not migration.op.execute.called, "the data fix is one-way"


def test_0210_is_the_parent_of_0211():
    spec = importlib.util.spec_from_file_location("migration_0211", VERSIONS / "0211_release_owner.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0210"
