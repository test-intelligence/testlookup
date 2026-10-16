"""Migration 0217 (W08 TRND-07): ``coverage_snapshots.snapshot_day`` and its unique index.

Read from the migration itself and from its offline (``--sql``) script, with
no database: the chain, the steps in order (the column, the UTC-day backfill,
the same-day collapse, NOT NULL, the old constraint dropped, the unique index
built CONCURRENTLY with the INVALID-leftover drop first), the downgrade's
order (the shared-timestamp re-stamp, the new index dropped CONCURRENTLY, the
old index built CONCURRENTLY and attached ``USING INDEX``, the column
dropped), and the ORM declaring the same column and index.

The same against real Postgres: ``tests/integration/test_migration_0217_0218_postgres.py``.
"""
from __future__ import annotations

import importlib.util
import io
import pathlib
import re

from sqlalchemy import Date
from sqlalchemy.dialects.postgresql import JSONB

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
FILE = "0217_coverage_snapshot_day.py"


def _module():
    spec = importlib.util.spec_from_file_location("m0217_unit", VERSIONS / FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline(step: str) -> str:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _module()
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer})
    with Operations.context(context):
        getattr(module, step)()
    return " ".join(buffer.getvalue().split())


def test_the_chain_is_0216_then_0217():
    module = _module()
    assert (module.revision, module.down_revision) == ("0217", "0216")
    text = (VERSIONS / FILE).read_text(encoding="utf-8")
    assert "Revision ID: 0217" in text and "Revises: 0216" in text
    assert (module.TABLE, module.COLUMN, module.INDEX, module.OLD_CONSTRAINT) == (
        "coverage_snapshots", "snapshot_day", "uq_coverage_snapshots_project_day", "uq_coverage_project_date",
    )


def _positions(script: str, needles: list[str]) -> list[int]:
    found = [script.find(n) for n in needles]
    assert all(i >= 0 for i in found), [n for n, i in zip(needles, found) if i < 0]
    return found


def test_the_upgrade_runs_its_steps_in_order():
    script = _offline("upgrade")
    order = _positions(script, [
        "ALTER TABLE coverage_snapshots ADD COLUMN snapshot_day DATE",
        "UPDATE coverage_snapshots SET snapshot_day = (COALESCE(snapshot_date, now()) AT TIME ZONE 'UTC')::date "
        "WHERE snapshot_day IS NULL",
        "DELETE FROM coverage_snapshots a USING coverage_snapshots b WHERE a.project_id = b.project_id "
        "AND a.snapshot_day = b.snapshot_day AND a.id < b.id",
        "ALTER TABLE coverage_snapshots ALTER COLUMN snapshot_day SET NOT NULL",
        "ALTER TABLE coverage_snapshots DROP CONSTRAINT uq_coverage_project_date",
        "COMMIT",  # autocommit_block() ends the migration's transaction first
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_coverage_snapshots_project_day "
        "ON coverage_snapshots (project_id, snapshot_day)",
    ])
    assert order == sorted(order), script
    assert re.search(r"\bDROP\s+INDEX\s+(?!CONCURRENTLY)", script) is None, script


def test_the_invalid_leftover_is_dropped_concurrently_before_the_build():
    """Offline there is no catalog to read, so the drop is decided from Python
    online only; its statement sits under ``if invalid:`` before the build."""
    source = (VERSIONS / FILE).read_text(encoding="utf-8")
    body = source[source.index("def _build_unique_index"):source.index("def downgrade")]
    drop = body.index('op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")')
    build = body.index("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX}")
    assert body.index("if invalid:") < drop < build
    assert body.index("autocommit_block()") < body.index("if invalid:")
    assert "_index_is_invalid(op.get_bind())" in body


def test_the_downgrade_undoes_it_in_order():
    script = _offline("downgrade")
    order = _positions(script, [
        "UPDATE coverage_snapshots s SET snapshot_date = (s.snapshot_day::timestamp AT TIME ZONE 'UTC')",
        "DROP INDEX CONCURRENTLY IF EXISTS uq_coverage_snapshots_project_day",
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_coverage_project_date_ix "
        "ON coverage_snapshots (project_id, snapshot_date)",
        "ALTER TABLE coverage_snapshots ADD CONSTRAINT uq_coverage_project_date UNIQUE USING INDEX "
        "uq_coverage_project_date_ix",
        "ALTER TABLE coverage_snapshots DROP COLUMN snapshot_day",
    ])
    assert order == sorted(order), script
    assert re.search(r"\bDROP\s+INDEX\s+(?!CONCURRENTLY)", script) is None, script


def test_the_orm_declares_the_same_column_and_index():
    from app.models.postgres import CoverageSnapshot

    table = CoverageSnapshot.__table__
    column = table.c.snapshot_day
    assert isinstance(column.type, Date) and column.nullable is False
    assert isinstance(table.c.suite_coverage.type, JSONB), "the table holds JSONB (0001)"
    [index] = [i for i in table.indexes if i.name == "uq_coverage_snapshots_project_day"]
    assert index.unique and [c.name for c in index.columns] == ["project_id", "snapshot_day"]
    assert not [c for c in table.constraints if getattr(c, "name", None) == "uq_coverage_project_date"]
