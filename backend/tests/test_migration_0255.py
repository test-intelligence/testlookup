"""Migration 0255 (W36 STRAT-03): the cluster-epic opt-in column + ``systemic_cluster_epics`` — shape only.

* the chain 0254 -> 0255, with 0255 the only head (at the time of writing); no
  application import;
* ``projects.cluster_epic_suggestions`` NOT NULL DEFAULT false (every existing
  project reads off — D30: opt-in per project);
* the link table: project FK CASCADE, created_by FK users SET NULL, the unique
  (project_id, membership_key) index built in the same transaction (a NEW table);
* the downgrade drops exactly what the upgrade adds, in reverse;
* the ORM ``SystemicClusterEpic`` / ``Project.cluster_epic_suggestions`` are in
  step with the DDL.

No database: the real DDL round trip (upgrade, downgrade to 0254, upgrade) is
recorded in the W36 G-A report and exercised by
``tests/integration/test_cluster_epic_postgres.py`` against the migrated schema.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0255 = VERSIONS / "0255_cluster_epic_suggestions.py"
TABLE = "systemic_cluster_epics"
INDEX = "uq_sce_project_membership"
COLUMNS = [
    "id", "project_id", "membership_key", "jira_key", "jira_url", "cluster_label", "member_fingerprints",
    "created_by", "created_at",
]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0255)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    return mod


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
            down = values.get("down_revision")
            out[str(values["revision"])] = str(down) if down is not None else None
    return out


def _created(mod):
    (create,) = mod.op.create_table.call_args_list
    assert create.args[0] == TABLE
    return {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}


def test_the_chain_is_linear_with_0255_on_0254():
    revisions = _revisions()
    assert revisions["0255"] == "0254"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0254") == 1, "0254 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0255"
    mod = _load("m0255_rev")
    assert "Revision ID: 0255" in mod.__doc__ and "Revises: 0254" in mod.__doc__


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0255.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_upgrade_shape():
    mod = _load("m0255_up")
    assert (mod.revision, mod.down_revision) == ("0255", "0254")
    mod.upgrade()
    assert [c[0] for c in mod.op.method_calls] == ["add_column", "create_table", "create_index"]
    ((table, column), _kw) = mod.op.add_column.call_args
    assert table == "projects" and column.name == "cluster_epic_suggestions"
    assert column.nullable is False and str(column.server_default.arg) == "false"
    assert column.type.__class__.__name__ == "Boolean"
    columns = _created(mod)
    assert list(columns) == COLUMNS
    assert {n for n, c in columns.items() if not c.nullable} == {
        "id", "project_id", "membership_key", "jira_key", "created_at",
    }
    fks = {n: next(iter(c.foreign_keys)) for n, c in columns.items() if c.foreign_keys}
    assert set(fks) == {"project_id", "created_by"}
    assert fks["project_id"].target_fullname == "projects.id" and fks["project_id"].ondelete == "CASCADE"
    assert fks["created_by"].target_fullname == "users.id" and fks["created_by"].ondelete == "SET NULL"
    assert columns["membership_key"].type.length == 32
    assert str(columns["created_at"].server_default.arg) == "now()"
    assert columns["created_at"].type.timezone is True
    ((name, table, cols), kwargs) = mod.op.create_index.call_args
    assert (name, table, cols) == (INDEX, TABLE, ["project_id", "membership_key"])
    assert kwargs == {"unique": True}, "a NEW table: plain, unique, in the same transaction"


def test_downgrade_is_the_exact_inverse():
    mod = _load("m0255_down")
    mod.downgrade()
    assert [c[0] for c in mod.op.method_calls] == ["drop_index", "drop_table", "drop_column"]
    assert mod.op.drop_index.call_args.args == (INDEX,)
    assert mod.op.drop_index.call_args.kwargs == {"table_name": TABLE}
    assert mod.op.drop_table.call_args.args == (TABLE,)
    assert mod.op.drop_column.call_args.args == ("projects", "cluster_epic_suggestions")


def test_the_orm_matches_0255():
    from app.models.postgres import Project, SystemicClusterEpic

    table = SystemicClusterEpic.__table__
    mod = _load("m0255_orm")
    mod.upgrade()
    ddl = _created(mod)
    assert table.name == TABLE
    assert [c.name for c in table.columns] == list(ddl)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in ddl.items()}
    for name in ("membership_key", "jira_key", "jira_url", "cluster_label"):
        assert table.c[name].type.length == ddl[name].type.length, name
    orm_fks = {c.name: next(iter(c.foreign_keys)) for c in table.columns if c.foreign_keys}
    assert {n: (fk.target_fullname, fk.ondelete) for n, fk in orm_fks.items()} == {
        "project_id": ("projects.id", "CASCADE"), "created_by": ("users.id", "SET NULL"),
    }
    (index,) = table.indexes
    assert index.name == INDEX and index.unique and [c.name for c in index.columns] == ["project_id", "membership_key"]
    column = Project.__table__.c["cluster_epic_suggestions"]
    assert column.nullable is False and str(column.server_default.arg) == "false"
