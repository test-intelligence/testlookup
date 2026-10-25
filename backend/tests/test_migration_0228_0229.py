"""Migrations 0228 and 0229 (W13 TCL-03 / TCL-06): shape only, no database.

* 0228: ``projects.automation_target_pct`` ``SMALLINT NOT NULL DEFAULT 60`` and
  its check ``ck_projects_automation_target_pct`` (0-100).
* 0229: the ``test_case_requirement_links`` table, its two checks, its unique
  constraint and one index (a plain build: the table is created in the same
  file), and the ``test_case_requirement_traceability`` flag row seeded with
  the fresh-install condition (ON for a new install, OFF for an upgraded one).

Pinned here: the revisions and docstrings; the chain 0227 -> 0228 -> 0229,
linear, 0229 the only head (every earlier migration test asserts
``heads[0] >= …`` and keeps passing); no application import; the
concurrent-index guard's own scanner sees only builds on the table 0229
creates; the upgrades' operations in order; each downgrade is its upgrade's
exact reverse; the ORM declares the same names, types, nullability, defaults
and constraint / index names; the feature-flag catalogue guard reads the key.
The PostgreSQL behaviour runs in ``tests/integration/test_migration_0228_0229_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

from tests.regression.test_feature_flag_catalogue_is_complete import _keys_in_migration
from tests.regression.test_migration_index_builds_are_concurrent import (
    _blocking,
    _concurrent_outside_autocommit,
    _operations,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0228 = VERSIONS / "0228_project_automation_target.py"
M0229 = VERSIONS / "0229_test_case_requirement_links.py"
FLAG = "test_case_requirement_traceability"
LINK_COLUMNS = [
    "id", "project_id", "test_case_id", "requirement_key", "requirement_key_norm", "requirement_title",
    "link_type", "created_by_id", "created_at",
]
FRESH_INSTALL = "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(path: Path, step: str) -> MagicMock:
    module = _load(path, f"{path.stem}_{step}")
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


# ── revisions, chain, imports ────────────────────────────────────────────────


def test_the_revisions_and_their_docstrings():
    m28, m29 = _load(M0228, "m0228"), _load(M0229, "m0229")
    assert (m28.revision, m28.down_revision) == ("0228", "0227")
    assert (m29.revision, m29.down_revision) == ("0229", "0228")
    assert "Revision ID: 0228" in m28.__doc__ and "Revises: 0227" in m28.__doc__
    assert "Revision ID: 0229" in m29.__doc__ and "Revises: 0228" in m29.__doc__
    # The deploy step an upgraded install needs is named where an operator reads it.
    assert f"PATCH /api/v1/feature-flags/{FLAG}" in m29.__doc__
    assert "ON for a" in m29.__doc__ and "OFF for an upgraded one" in m29.__doc__


def test_the_chain_is_linear_with_0229_the_only_head():
    revisions = _revisions()
    assert revisions["0228"] == "0227" and revisions["0229"] == "0228"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0227") == 1, "nothing else forks off 0227"
    assert parents.count("0228") == 1, "nothing else forks off 0228"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0229"


def test_they_import_no_application_code():
    for path in (M0228, M0229):
        imported = _imports(path)
        assert not any(name.startswith("app") for name in imported), (path.name, imported)


# ── the concurrent-index guard's view ────────────────────────────────────────


def test_0228_builds_no_index():
    source = M0228.read_text(encoding="utf-8")
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    assert list(_operations(source)) == []


def test_0229_builds_only_on_the_table_it_creates():
    source = M0229.read_text(encoding="utf-8")
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []
    assert {op.table for op in _operations(source)} == {"test_case_requirement_links"}
    assert sorted(op.kind for op in _operations(source)) == ["drop", "index"]


def test_the_catalogue_guard_reads_the_flag_key():
    assert _keys_in_migration(M0229.read_text(encoding="utf-8")) == {FLAG}


# ── 0228 ─────────────────────────────────────────────────────────────────────


def test_0228_upgrade_adds_the_column_then_its_check():
    op = _run(M0228, "upgrade")
    assert [c[0] for c in op.method_calls] == ["add_column", "create_check_constraint"]
    table, column = op.add_column.call_args.args
    assert table == "projects" and column.name == "automation_target_pct"
    assert isinstance(column.type, sa.SmallInteger) and column.nullable is False
    assert str(column.server_default.arg) == "60", "a constant default: existing projects read 60"
    assert not column.index and not column.unique and not column.foreign_keys
    assert op.create_check_constraint.call_args.args == (
        "ck_projects_automation_target_pct", "projects", "automation_target_pct BETWEEN 0 AND 100",
    )


def test_0228_downgrade_is_the_exact_reverse():
    op = _run(M0228, "downgrade")
    assert [(c[0], c.args, c.kwargs) for c in op.method_calls] == [
        ("drop_constraint", ("ck_projects_automation_target_pct", "projects"), {"type_": "check"}),
        ("drop_column", ("projects", "automation_target_pct"), {}),
    ]


def test_0228_the_orm_declares_the_same_column():
    from app.models.postgres import Project

    declared = Project.__table__.c["automation_target_pct"]
    assert isinstance(declared.type, sa.SmallInteger) and declared.nullable is False
    assert declared.default.arg == 60 and str(declared.server_default.arg) == "60"
    names = [c.name for c in Project.__table__.columns]
    assert names.index("automation_target_pct") == names.index("require_case_review") + 1
    # The check lives in the migration only (as for require_case_review).
    assert not [c for c in Project.__table__.constraints if isinstance(c, sa.CheckConstraint)]


# ── 0229 ─────────────────────────────────────────────────────────────────────


def _created(op: MagicMock):
    call = op.create_table.call_args
    items = call.args[1:]
    columns = {c.name: c for c in items if isinstance(c, sa.Column)}
    checks = {c.name: c for c in items if isinstance(c, sa.CheckConstraint)}
    uniques = [c for c in items if isinstance(c, sa.UniqueConstraint)]
    return call.args[0], columns, checks, uniques


def test_0229_upgrade_creates_the_table_its_index_then_the_flag_row():
    op = _run(M0229, "upgrade")
    assert [c[0] for c in op.method_calls] == ["create_table", "create_index", "execute"]
    name, columns, checks, uniques = _created(op)
    assert name == "test_case_requirement_links"
    assert list(columns) == LINK_COLUMNS
    assert columns["id"].primary_key and isinstance(columns["id"].type, sa.Uuid)
    fks = {n: {(fk.target_fullname, fk.ondelete) for fk in c.foreign_keys} for n, c in columns.items()}
    assert fks["project_id"] == {("projects.id", "CASCADE")}
    assert fks["test_case_id"] == {("managed_test_cases.id", "CASCADE")}
    assert fks["created_by_id"] == {("users.id", "SET NULL")}
    lengths = {n: c.type.length for n, c in columns.items() if isinstance(c.type, sa.String)}
    assert lengths == {"requirement_key": 200, "requirement_key_norm": 200, "requirement_title": 500, "link_type": 20}
    nullable = {n for n, c in columns.items() if c.nullable}
    assert nullable == {"requirement_title", "created_by_id"}
    assert str(columns["link_type"].server_default.arg) == "'verifies'"
    assert str(columns["created_at"].server_default.arg) == "now()" and columns["created_at"].type.timezone
    assert {n: _sql(c.sqltext) for n, c in checks.items()} == {
        "ck_tcrl_link_type": "link_type IN ('verifies', 'partially_verifies', 'related')",
        "ck_tcrl_key_norm_nonempty": "requirement_key_norm <> ''",
    }
    (unique,) = uniques
    assert unique.name == "uq_tcrl_case_key"
    assert [str(c) for c in unique._pending_colargs] == ["test_case_id", "requirement_key_norm"]
    index = op.create_index.call_args
    assert index.args == ("ix_tcrl_project_key", "test_case_requirement_links", ["project_id", "requirement_key_norm"])
    assert not index.kwargs.get("unique") and not index.kwargs.get("postgresql_concurrently")

    (clause,) = op.execute.call_args.args
    assert _sql(clause) == (
        "INSERT INTO feature_flags (id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
        f"SELECT gen_random_uuid(), :key, :description, ({FRESH_INSTALL}), 100, now(), now() "
        "ON CONFLICT (key) DO NOTHING"
    )
    params = clause.compile().params
    assert params["key"] == FLAG
    assert params["description"].startswith("Requirement keys on test cases") and "no Jira" in params["description"]


def test_0229_downgrade_is_the_exact_reverse_and_keeps_an_edited_flag():
    op = _run(M0229, "downgrade")
    assert [c[0] for c in op.method_calls] == ["execute", "drop_index", "drop_table"]
    (clause,) = op.execute.call_args.args
    assert _sql(clause) == (
        "DELETE FROM feature_flags WHERE key = :key AND description = :description "
        "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
        "AND rollout_percent = 100"
    ), "the 0209 rule: only the untouched seed is deleted"
    assert clause.compile().params["key"] == FLAG
    assert op.drop_index.call_args.args == ("ix_tcrl_project_key",)
    assert op.drop_index.call_args.kwargs == {"table_name": "test_case_requirement_links"}
    assert op.drop_table.call_args.args == ("test_case_requirement_links",)


def test_0229_the_orm_declares_the_same_names_types_and_defaults():
    from app.models.postgres import TestCaseRequirementLink

    op = _run(M0229, "upgrade")
    _, migrated, checks, _ = _created(op)
    table = TestCaseRequirementLink.__table__
    assert table.name == "test_case_requirement_links"
    assert [c.name for c in table.columns] == LINK_COLUMNS
    for name, column in migrated.items():
        declared = table.c[name]
        assert declared.nullable is column.nullable, name
        assert declared.primary_key is column.primary_key, name
        assert type(declared.type)._type_affinity is type(column.type)._type_affinity, name
        if getattr(column.type, "length", None):
            assert declared.type.length == column.type.length, name
        assert {(fk.target_fullname, fk.ondelete) for fk in declared.foreign_keys} == {
            (fk.target_fullname, fk.ondelete) for fk in column.foreign_keys
        }, name
        assert (declared.server_default is None) is (column.server_default is None), name
    assert table.c["link_type"].default.arg == "verifies"
    declared_checks = {c.name: _sql(c.sqltext) for c in table.constraints if isinstance(c, sa.CheckConstraint)}
    assert declared_checks == {n: _sql(c.sqltext) for n, c in checks.items()}
    (unique,) = [c for c in table.constraints if isinstance(c, sa.UniqueConstraint)]
    assert unique.name == "uq_tcrl_case_key" and [c.name for c in unique.columns] == ["test_case_id", "requirement_key_norm"]
    (index,) = table.indexes
    assert index.name == "ix_tcrl_project_key" and [c.name for c in index.columns] == ["project_id", "requirement_key_norm"]
