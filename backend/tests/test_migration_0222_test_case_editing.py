"""Migration 0222 (W28 test-case editing): five columns, no index, a real downgrade.

Shape only (no database): the chain after 0221 with one head; the five
columns with their types, nullability and the project column's ``false``
server default; the ORM declares the same names, types and nullability; the
downgrade drops exactly the five, in reverse order. The PostgreSQL behaviour
runs in ``tests/integration/test_migration_0222_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "0222_test_case_editing.py"

#: (table, column) in upgrade order.
COLUMNS = [
    ("projects", "require_case_review"),
    ("managed_test_cases", "edited_since_approval_version"),
    ("test_case_versions", "source"),
    ("test_case_versions", "assignee_id"),
    ("test_plan_items", "executed_case_version"),
]


def _module():
    spec = importlib.util.spec_from_file_location("migration_0222", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _added_columns():
    migration = _module()
    migration.op = MagicMock()
    migration.upgrade()
    return migration.op, [call.args for call in migration.op.add_column.call_args_list]


def test_it_follows_0221():
    migration = _module()
    assert (migration.revision, migration.down_revision) == ("0222", "0221")
    assert "Revision ID: 0222" in migration.__doc__ and "Revises: 0221" in migration.__doc__


def test_it_imports_no_application_code():
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith("app") for name in imported), imported


def test_upgrade_adds_exactly_the_five_columns():
    op, added = _added_columns()
    assert [(table, column.name) for table, column in added] == COLUMNS
    assert not op.create_index.called
    assert not op.execute.called
    assert not op.create_check_constraint.called
    assert not op.get_bind.called  # no backfill


def test_the_column_types_and_nullability():
    _, added = _added_columns()
    by_name = {column.name: column for _, column in added}

    review = by_name["require_case_review"]
    assert isinstance(review.type, sa.Boolean)
    assert review.nullable is False
    assert str(review.server_default.arg) == "false"

    marker = by_name["edited_since_approval_version"]
    assert isinstance(marker.type, sa.Integer) and marker.nullable is True

    source = by_name["source"]
    assert isinstance(source.type, sa.String) and source.type.length == 16
    assert source.nullable is True and source.server_default is None

    assignee = by_name["assignee_id"]
    assert isinstance(assignee.type, postgresql.UUID) and assignee.nullable is True
    (fk,) = assignee.foreign_keys
    assert fk.target_fullname == "users.id"
    assert fk.ondelete == "SET NULL"
    assert fk.name == "fk_tcv_assignee_id_users"

    executed = by_name["executed_case_version"]
    assert isinstance(executed.type, sa.Integer) and executed.nullable is True


def test_downgrade_drops_exactly_the_five_in_reverse_order():
    migration = _module()
    migration.op = MagicMock()
    migration.downgrade()
    assert [call.args for call in migration.op.drop_column.call_args_list] == list(
        reversed(COLUMNS)
    )
    assert not migration.op.execute.called
    assert not migration.op.drop_constraint.called  # the FK goes with its column


def test_the_orm_declares_the_same_columns():
    from app.models.postgres import (
        ManagedTestCase,
        Project,
        TestCaseVersion,
        TestPlanItem,
    )

    _, added = _added_columns()
    tables = {
        "projects": Project.__table__,
        "managed_test_cases": ManagedTestCase.__table__,
        "test_case_versions": TestCaseVersion.__table__,
        "test_plan_items": TestPlanItem.__table__,
    }
    for table, migrated in added:
        declared = tables[table].c[migrated.name]
        assert declared.nullable is migrated.nullable, (table, migrated.name)
        assert type(declared.type)._type_affinity is type(migrated.type)._type_affinity, (
            table,
            migrated.name,
        )
        assert not declared.index and not declared.unique, (table, migrated.name)
    assert str(Project.__table__.c["require_case_review"].server_default.arg) == "false"
    assert TestCaseVersion.__table__.c["source"].type.length == 16
    (fk,) = TestCaseVersion.__table__.c["assignee_id"].foreign_keys
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"


def _revisions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"revision", "down_revision"}
                and isinstance(node.value, ast.Constant)
            ):
                values[node.targets[0].id] = node.value.value
        if "revision" in values:
            out[str(values["revision"])] = values.get("down_revision")  # type: ignore[assignment]
    return out


def test_the_chain_is_linear_with_one_head():
    revisions = _revisions()
    assert revisions["0222"] == "0221"
    parents = {p for p in revisions.values() if p}
    heads = sorted(r for r in revisions if r not in parents)
    assert len(heads) == 1, heads
    assert heads[0] >= "0222"
