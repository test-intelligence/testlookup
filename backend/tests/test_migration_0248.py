"""Migration 0248 (W25a HYG-04): ``suite_membership_parity_runs`` — shape only, no database.

* the chain 0247 -> 0248, with a single head;
* the migration imports no application code;
* the CHECK literal lists are ``suite_parity_vocab.RUN_TRIGGERS`` /
  ``RUN_STATUSES`` (a migration must not import application code, so the copy
  is pinned here);
* the upgrade shape (columns, types, nullability, defaults, the FK action, both
  indexes incl. the partial predicate);
* the downgrade drops exactly what the upgrade created, in reverse order;
* the ORM ``SuiteMembershipParityRun`` is in step with the DDL.

The real DDL runs against Postgres in ``integration/test_suite_membership_parity_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import suite_parity_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0248 = VERSIONS / "0248_suite_membership_parity_runs.py"
TABLE = "suite_membership_parity_runs"
COLUMNS = [
    "id", "trigger", "requested_by_id", "status", "started_at", "finished_at", "window_runs", "projects_checked",
    "keys_compared", "blocking_diffs", "expected_divergences", "counts", "per_project", "sample", "error_code",
]
TYPES = {
    "id": "UUID", "trigger": "String", "requested_by_id": "UUID", "status": "String", "started_at": "DateTime",
    "finished_at": "DateTime", "window_runs": "Integer", "projects_checked": "Integer", "keys_compared": "Integer",
    "blocking_diffs": "Integer", "expected_divergences": "Integer", "counts": "JSONB", "per_project": "JSONB",
    "sample": "JSONB", "error_code": "String",
}
INDEXES = ["ix_smpr_started", "uq_smpr_one_running"]
FINISHED_CHECK = "(status = 'running') = (finished_at IS NULL)"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0248)
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
            out[str(values["revision"])] = values.get("down_revision")  # type: ignore[assignment]
    return out


def _created(name: str):
    mod = _load(name)
    mod.upgrade()
    (create,) = mod.op.create_table.call_args_list
    return mod, create


def _ddl_checks(create) -> dict[str, str]:
    return {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}


def _ddl_columns(create) -> dict:
    return {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}


def test_the_chain_is_linear_with_0248_on_0247():
    revisions = _revisions()
    assert revisions["0248"] == "0247"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0247") == 1, "0247 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0248"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0248.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_check_literals_are_the_vocabulary():
    mod, create = _created("m0248_vocab")
    assert mod._TRIGGERS == vocab.RUN_TRIGGERS and mod._STATUSES == vocab.RUN_STATUSES
    checks = _ddl_checks(create)
    assert set(checks) == {"ck_smpr_trigger", "ck_smpr_status", "ck_smpr_finished"}
    assert tuple(re.findall(r"'([^']+)'", checks["ck_smpr_trigger"])) == vocab.RUN_TRIGGERS
    assert tuple(re.findall(r"'([^']+)'", checks["ck_smpr_status"])) == vocab.RUN_STATUSES
    assert checks["ck_smpr_finished"] == FINISHED_CHECK
    assert "'running'" in checks["ck_smpr_finished"] and vocab.RUN_STATUSES[0] == "running"


def test_upgrade_shape():
    mod, create = _created("m0248_up")
    assert (mod.revision, mod.down_revision) == ("0248", "0247")
    assert create.args[0] == TABLE
    columns = _ddl_columns(create)
    assert list(columns) == COLUMNS
    assert {name: type(c.type).__name__ for name, c in columns.items()} == TYPES
    not_null = {name for name, c in columns.items() if not c.nullable}
    assert not_null == set(COLUMNS) - {"requested_by_id", "finished_at", "error_code"}
    assert columns["id"].primary_key
    (fk,) = columns["requested_by_id"].foreign_keys
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    assert not columns["status"].foreign_keys and not columns["trigger"].foreign_keys
    assert columns["trigger"].type.length == 10 and columns["status"].type.length == 10
    assert columns["error_code"].type.length == 64
    assert columns["started_at"].type.timezone and columns["finished_at"].type.timezone
    assert str(columns["started_at"].server_default.arg) == "now()"
    for name in ("projects_checked", "keys_compared", "blocking_diffs", "expected_divergences"):
        assert str(columns[name].server_default.arg) == "0", name
    assert str(columns["counts"].server_default.arg) == "'{}'::jsonb"
    assert str(columns["per_project"].server_default.arg) == "'[]'::jsonb"
    assert str(columns["sample"].server_default.arg) == "'[]'::jsonb"
    for name in ("id", "trigger", "status", "window_runs", "finished_at", "error_code", "requested_by_id"):
        assert columns[name].server_default is None, name
    calls = mod.op.create_index.call_args_list
    assert [c.args[0] for c in calls] == INDEXES
    assert all(c.args[1] == TABLE for c in calls)
    by_name = {c.args[0]: c for c in calls}
    assert [str(x) for x in by_name["ix_smpr_started"].args[2]] == ["started_at DESC"]
    assert by_name["uq_smpr_one_running"].args[2] == ["status"]
    assert {c.args[0] for c in calls if c.kwargs.get("unique")} == {"uq_smpr_one_running"}
    assert str(by_name["uq_smpr_one_running"].kwargs["postgresql_where"]) == "status = 'running'"
    assert not [c for c in calls if c.kwargs.get("postgresql_concurrently")]
    assert [c[0] for c in mod.op.method_calls] == ["create_table"] + ["create_index"] * 2


def test_downgrade_drops_exactly_what_the_upgrade_created_in_reverse():
    mod = _load("m0248_down")
    mod.downgrade()
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == list(reversed(INDEXES))
    assert all(c.kwargs["table_name"] == TABLE for c in mod.op.drop_index.call_args_list)
    mod.op.drop_table.assert_called_once_with(TABLE)
    assert [c[0] for c in mod.op.method_calls] == ["drop_index"] * 2 + ["drop_table"]


def test_the_orm_matches_the_ddl():
    from app.models.postgres import SuiteMembershipParityRun

    table = SuiteMembershipParityRun.__table__
    assert table.name == TABLE
    _mod, create = _created("m0248_orm")
    ddl = _ddl_columns(create)
    assert [c.name for c in table.columns] == list(ddl)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in ddl.items()}
    assert {c.name: type(c.type).__name__ for c in table.columns} == TYPES
    checks = {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert checks == _ddl_checks(create)
    (orm_fk,) = table.c["requested_by_id"].foreign_keys
    (ddl_fk,) = ddl["requested_by_id"].foreign_keys
    assert (orm_fk.target_fullname, orm_fk.ondelete) == (ddl_fk.target_fullname, ddl_fk.ondelete)
    for name in ("started_at", "projects_checked", "keys_compared", "blocking_diffs", "expected_divergences",
                 "counts", "per_project", "sample"):
        assert table.c[name].server_default is not None, name
        assert str(table.c[name].server_default.arg) == str(ddl[name].server_default.arg), name
    for name in ("trigger", "status", "error_code"):
        assert table.c[name].type.length == ddl[name].type.length, name
    indexes = {ix.name: ix for ix in table.indexes}
    assert set(indexes) == set(INDEXES)
    assert {n for n, ix in indexes.items() if ix.unique} == {"uq_smpr_one_running"}
    assert str(indexes["uq_smpr_one_running"].dialect_options["postgresql"]["where"]) == "status = 'running'"
    assert [str(e) for e in indexes["ix_smpr_started"].expressions] == ["started_at DESC"]
    assert "project_id" not in table.c, "an instance-wide run has no project owner"
