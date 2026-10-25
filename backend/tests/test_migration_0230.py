"""Migration 0230 (W14 AGT-08): shape only, no database.

``project_workflow_defaults``: one row per ``(project_id, base)`` naming the
published fork a trigger that names no workflow runs; its primary key and three
checks; NO index and NO foreign key to ``workflow_definitions``.

Pinned here: the revision and docstring; the chain 0229 -> 0230, linear, 0230
the only head (every earlier migration test asserts ``heads[0] >= …`` and keeps
passing); no application import; the concurrent-index guard has nothing to
judge (no index is built); the upgrade's one operation; the downgrade drops
exactly the table; the ORM declares the same names, types, nullability,
defaults, key and checks. The PostgreSQL behaviour runs in
``tests/integration/test_migration_0230_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

from tests.regression.test_migration_index_builds_are_concurrent import (
    _blocking,
    _concurrent_outside_autocommit,
    _operations,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0230 = VERSIONS / "0230_project_workflow_defaults.py"
TABLE = "project_workflow_defaults"
COLUMNS = ["project_id", "base", "workflow_id", "workflow_version", "updated_by", "updated_at"]
CHECKS = {
    "ck_pwd_base": "base IN ('offline','deep')",
    "ck_pwd_workflow_is_fork": "workflow_id LIKE 'wf.%'",
    "ck_pwd_version_positive": "workflow_version IS NULL OR workflow_version >= 1",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(step: str) -> MagicMock:
    module = _load(M0230, f"m0230_{step}")
    module.op = MagicMock()
    getattr(module, step)()
    return module.op


def _revisions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"revision", "down_revision"} and isinstance(node.value, ast.Constant):
                    values[node.targets[0].id] = node.value.value
        if "revision" in values:
            out[str(values["revision"])] = values.get("down_revision")  # type: ignore[assignment]
    return out


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


def _sql(clause) -> str:
    return " ".join(str(clause).split())


def _created(op: MagicMock):
    call = op.create_table.call_args
    items = call.args[1:]
    columns = {c.name: c for c in items if isinstance(c, sa.Column)}
    checks = {c.name: c for c in items if isinstance(c, sa.CheckConstraint)}
    keys = [c for c in items if isinstance(c, sa.PrimaryKeyConstraint)]
    return call.args[0], columns, checks, keys


def test_the_revision_and_its_docstring():
    module = _load(M0230, "m0230")
    assert (module.revision, module.down_revision) == ("0230", "0229")
    assert "Revision ID: 0230" in module.__doc__ and "Revises: 0229" in module.__doc__
    # What each object is for is written where an operator reads it.
    for name in ("pk_project_workflow_defaults", *CHECKS):
        assert name in module.__doc__, name
    assert "NO" in module.__doc__ and "workflow_definitions" in module.__doc__


def test_the_chain_is_linear_with_0230_the_only_head():
    revisions = _revisions()
    assert revisions["0230"] == "0229"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0229") == 1, "nothing else forks off 0229"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0230"


def test_it_imports_no_application_code():
    imported = _imports(M0230)
    assert not any(name.startswith("app") for name in imported), imported


def test_it_builds_no_index():
    source = M0230.read_text(encoding="utf-8")
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    assert list(_operations(source)) == []


def test_the_upgrade_creates_the_table_its_key_and_three_checks():
    op = _run("upgrade")
    assert [c[0] for c in op.method_calls] == ["create_table"]
    name, columns, checks, keys = _created(op)
    assert name == TABLE
    assert list(columns) == COLUMNS
    fks = {n: {(fk.target_fullname, fk.ondelete) for fk in c.foreign_keys} for n, c in columns.items()}
    assert fks == {
        "project_id": {("projects.id", "CASCADE")},
        "base": set(),
        "workflow_id": set(),
        "workflow_version": set(),
        "updated_by": {("users.id", "SET NULL")},
        "updated_at": set(),
    }, "no foreign key to workflow_definitions (a NULL version has no single target row)"
    assert isinstance(columns["project_id"].type, sa.Uuid) and isinstance(columns["updated_by"].type, sa.Uuid)
    lengths = {n: c.type.length for n, c in columns.items() if isinstance(c.type, sa.String)}
    assert lengths == {"base": 20, "workflow_id": 80}
    assert isinstance(columns["workflow_version"].type, sa.Integer)
    assert {n for n, c in columns.items() if c.nullable} == {"workflow_version", "updated_by"}
    assert str(columns["updated_at"].server_default.arg) == "now()" and columns["updated_at"].type.timezone
    assert not any(c.index or c.unique for c in columns.values())
    (key,) = keys
    assert key.name == "pk_project_workflow_defaults"
    assert [str(c) for c in key._pending_colargs] == ["project_id", "base"]
    assert {n: _sql(c.sqltext) for n, c in checks.items()} == CHECKS


def test_the_downgrade_drops_exactly_the_table():
    op = _run("downgrade")
    assert [(c[0], c.args, c.kwargs) for c in op.method_calls] == [("drop_table", (TABLE,), {})]


def test_the_orm_declares_the_same_names_types_key_and_checks():
    from app.models.postgres import ProjectWorkflowDefault, WorkflowDefinition

    _, migrated, checks, _ = _created(_run("upgrade"))
    table = ProjectWorkflowDefault.__table__
    assert table.name == TABLE
    assert [c.name for c in table.columns] == COLUMNS
    for name, column in migrated.items():
        declared = table.c[name]
        assert declared.nullable is column.nullable, name
        assert type(declared.type)._type_affinity is type(column.type)._type_affinity, name
        if getattr(column.type, "length", None):
            assert declared.type.length == column.type.length, name
        assert {(fk.target_fullname, fk.ondelete) for fk in declared.foreign_keys} == {
            (fk.target_fullname, fk.ondelete) for fk in column.foreign_keys
        }, name
        assert (declared.server_default is None) is (column.server_default is None), name
    assert table.primary_key.name == "pk_project_workflow_defaults"
    assert [c.name for c in table.primary_key.columns] == ["project_id", "base"]
    declared_checks = {c.name: _sql(c.sqltext) for c in table.constraints if isinstance(c, sa.CheckConstraint)}
    assert declared_checks == {n: _sql(c.sqltext) for n, c in checks.items()}
    assert not table.indexes
    # A plain row (read by services/workflow_default_service.py only): no relationship attributes.
    assert not ProjectWorkflowDefault.__mapper__.relationships
    # Declared right after WorkflowDefinition.
    from app.models import postgres as models

    names = [n for n, v in vars(models).items() if isinstance(v, type) and hasattr(v, "__tablename__")]
    assert names.index("ProjectWorkflowDefault") == names.index(WorkflowDefinition.__name__) + 1
