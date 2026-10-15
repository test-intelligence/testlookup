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


def _fake_op(invalid=()):
    """A MagicMock ``op`` that records whether each statement ran inside the autocommit block."""
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
    bind = op.get_bind.return_value
    fix_result = MagicMock()
    fix_result.fetchall.return_value = []
    probe = MagicMock()
    probe.scalars.return_value.all.return_value = list(invalid)

    def _execute(statement, *args, **kwargs):
        return probe if "indisvalid" in str(statement) else fix_result

    bind.execute.side_effect = _execute
    return op, log


def test_upgrade_runs_lock_fix_then_concurrent_indexes_in_order():
    migration = _module()
    migration.op, log = _fake_op()
    migration.upgrade()
    bind = migration.op.get_bind.return_value
    fixes = [str(c.args[0]) for c in bind.execute.call_args_list if "indisvalid" not in str(c.args[0])]
    # Demote BEFORE renumbering: renumbering first could lift a duplicate above
    # the row the evaluator was using, and keep a different policy active.
    assert fixes == [migration.DEMOTE_SQL, migration.RENUMBER_SQL]
    # The lock and the fix are transactional; every build is CONCURRENT and in the autocommit block.
    assert log[0] == (migration.LOCK_SQL, False)
    builds = log[1:]
    assert [sql.split(" IF NOT EXISTS ")[1].split(" ")[0] for sql, _ in builds] == list(EXPECTED_INDEXES)
    for (sql, in_block), (name, (column, predicate)) in zip(builds, EXPECTED_INDEXES.items()):
        assert in_block, sql
        assert sql == (
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {name} "
            f"ON release_gate_policies ({column}) WHERE {predicate}"
        )


def test_an_invalid_leftover_is_dropped_before_the_rebuild():
    migration = _module()
    migration.op, log = _fake_op(invalid=["uq_rgp_one_active_system"])
    migration.upgrade()
    in_block = [sql for sql, block in log if block]
    assert in_block[0] == "DROP INDEX CONCURRENTLY IF EXISTS uq_rgp_one_active_system"
    assert len(in_block) == 1 + len(EXPECTED_INDEXES)


def test_each_demotion_is_logged(caplog):
    import logging
    import uuid

    migration = _module()
    migration.op, _log = _fake_op()
    bind = migration.op.get_bind.return_value
    demoted = uuid.uuid4()
    renumbered = uuid.uuid4()
    answers = iter([[(demoted, None, 3)], [(renumbered, 7)]])
    probe = MagicMock()
    probe.scalars.return_value.all.return_value = []

    def _execute(statement, *args, **kwargs):
        if "indisvalid" in str(statement):
            return probe
        result = MagicMock()
        result.fetchall.return_value = next(answers)
        return result

    bind.execute.side_effect = _execute
    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        migration.upgrade()
    assert f"0210 demoted duplicate active policy {demoted} (scope system, v3)" in caplog.text
    assert f"{renumbered} is now v7" in caplog.text


def test_downgrade_drops_exactly_the_indexes_concurrently_and_no_data():
    migration = _module()
    migration.op, log = _fake_op()
    migration.downgrade()
    assert all(in_block for _sql, in_block in log)
    assert sorted(log) == sorted((f"DROP INDEX CONCURRENTLY IF EXISTS {name}", True) for name in EXPECTED_INDEXES)
    assert not migration.op.get_bind.return_value.execute.called, "the data fix is one-way"


def test_0210_is_the_parent_of_0211():
    spec = importlib.util.spec_from_file_location("migration_0211", VERSIONS / "0211_release_owner.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0210"
