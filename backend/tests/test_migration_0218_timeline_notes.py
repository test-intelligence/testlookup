"""Migration 0218 (W08 TRND-04): the ``timeline_notes`` table.

Read from the migration and its offline (``--sql``) script, with no database:
the chain, the table's columns (types, nullability, the two foreign keys and
their ``ON DELETE``), both CHECK texts equal to the ORM's, the window index
built on the table this migration creates (a plain build is allowed there),
and the downgrade dropping the index and then the table.

The same against real Postgres: ``tests/integration/test_migration_0217_0218_postgres.py``.
"""
from __future__ import annotations

import importlib.util
import io
import pathlib

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
FILE = "0218_timeline_notes.py"


def _module():
    spec = importlib.util.spec_from_file_location("m0218_unit", VERSIONS / FILE)
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


def test_the_chain_is_0217_then_0218():
    module = _module()
    assert (module.revision, module.down_revision) == ("0218", "0217")
    text = (VERSIONS / FILE).read_text(encoding="utf-8")
    assert "Revision ID: 0218" in text and "Revises: 0217" in text


def test_the_table_has_its_columns_checks_and_foreign_keys():
    script = _offline("upgrade")
    for fragment in (
        "CREATE TABLE timeline_notes (",
        "id UUID NOT NULL",
        "project_id UUID NOT NULL",
        "suite_name VARCHAR(500)",
        "starts_on DATE NOT NULL",
        "ends_on DATE NOT NULL",
        "text VARCHAR(280) NOT NULL",
        "created_by_id UUID",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "PRIMARY KEY (id)",
        "FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE",
        "FOREIGN KEY(created_by_id) REFERENCES users (id) ON DELETE SET NULL",
        "CONSTRAINT ck_timeline_notes_range CHECK (ends_on >= starts_on)",
        "CONSTRAINT ck_timeline_notes_text CHECK (char_length(text) BETWEEN 1 AND 280)",
        "CREATE INDEX ix_timeline_notes_project_starts ON timeline_notes (project_id, starts_on)",
    ):
        assert fragment in script, fragment
    assert "created_by_id UUID NOT NULL" not in script and "suite_name VARCHAR(500) NOT NULL" not in script
    assert script.index("CREATE TABLE timeline_notes") < script.index("CREATE INDEX ix_timeline_notes_project_starts")


def test_both_check_texts_and_the_index_equal_the_orms():
    from app.models.postgres import TimelineNote

    module = _module()
    table = TimelineNote.__table__
    checks = {c.name: str(c.sqltext) for c in table.constraints if (c.name or "").startswith("ck_")}
    assert checks == {"ck_timeline_notes_range": module.RANGE_CHECK, "ck_timeline_notes_text": module.TEXT_CHECK}
    assert "category IN (" not in module.RANGE_CHECK + module.TEXT_CHECK
    [index] = [i for i in table.indexes if i.name == module.INDEX]
    assert [c.name for c in index.columns] == ["project_id", "starts_on"]
    assert {c.name: c.nullable for c in table.columns} == {
        "id": False, "project_id": False, "suite_name": True, "starts_on": False, "ends_on": False,
        "created_by_id": True, "created_at": False, "updated_at": False, "text": False,
    }
    assert {fk.parent.name: (fk.column.table.name, fk.ondelete) for fk in table.foreign_keys} == {
        "project_id": ("projects", "CASCADE"), "created_by_id": ("users", "SET NULL"),
    }


def test_the_downgrade_drops_the_index_then_the_table():
    script = _offline("downgrade")
    assert script.index("DROP INDEX ix_timeline_notes_project_starts") < script.index("DROP TABLE timeline_notes")
