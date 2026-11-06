"""Migration 0254 (W34 STRAT-13): ``drop_folder_builds`` + the ``drop_folder_watcher`` flag seeded ON — shape only.

* the chain 0253 -> 0254, with 0254 the only head (at the time of writing); no
  application import;
* the CHECK literals are ``drop_folder_watcher.BUILD_STATUSES``; a queued row
  carries its task id and time;
* the unique build prefix and the partial pending index (a NEW table: built in
  the same transaction, non-concurrently);
* the flag row: ON (enabled, rollout 100) on every install, an existing row left
  alone (``ON CONFLICT (key) DO NOTHING``); the downgrade deletes it only while
  it still equals the untouched seed, then drops the indexes and the table;
* the ORM ``DropFolderBuild`` is in step with the DDL, and the service's
  ``FEATURE_FLAG`` is the seeded key.

No database: the real DDL round trip (upgrade, downgrade -1, upgrade) runs in
``tests/integration/test_drop_folder_watcher_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import drop_folder_watcher as dfw

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0254 = VERSIONS / "0254_drop_folder_watcher.py"
TABLE = "drop_folder_builds"
INDEXES = ["uq_dfb_build_prefix", "ix_dfb_pending_first_seen"]
CHECKS = {
    "ck_dfb_status": "status IN ('pending', 'queued', 'baseline', 'abandoned', 'refused')",
    "ck_dfb_queued_has_task": "status <> 'queued' OR (queued_at IS NOT NULL AND task_id IS NOT NULL)",
}
INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, true, 100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)
DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0254)
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


def _ddl_calls(mod) -> list[str]:
    return [c[0] for c in mod.op.method_calls]


def _executed(mod) -> list[tuple[str, dict]]:
    out = []
    for call in mod.op.execute.call_args_list:
        clause = call.args[0]
        out.append((str(clause), {k: bp.value for k, bp in clause._bindparams.items()}))
    return out


def _created(mod):
    (create,) = mod.op.create_table.call_args_list
    assert create.args[0] == TABLE
    columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    checks = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    return columns, checks


def test_the_chain_is_linear_with_0254_on_0253():
    revisions = _revisions()
    assert revisions["0254"] == "0253"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0253") == 1, "0253 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0254"
    mod = _load("m0254_rev")
    assert "Revision ID: 0254" in mod.__doc__ and "Revises: 0253" in mod.__doc__


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0254.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules
    assert "os" not in modules


def test_the_check_literals_are_the_vocabulary_and_the_flag_is_the_services():
    mod = _load("m0254_vocab")
    assert mod._STATUSES == dfw.BUILD_STATUSES
    ((key, description),) = mod._FLAGS
    assert key == dfw.FEATURE_FLAG == "drop_folder_watcher"
    assert description.startswith("ON: ") and description.isascii()
    assert (mod.INSERT_SQL, mod.DELETE_SQL) == (INSERT_SQL, DELETE_SQL)


def test_upgrade_shape():
    mod = _load("m0254_up")
    assert (mod.revision, mod.down_revision) == ("0254", "0253")
    mod.upgrade()
    columns, checks = _created(mod)
    assert checks == CHECKS
    assert tuple(re.findall(r"'([^']+)'", checks["ck_dfb_status"])) == dfw.BUILD_STATUSES
    assert list(columns) == [
        "id", "build_prefix", "project_segment", "status", "sentinel_etag", "sentinel_last_modified",
        "first_seen_at", "last_checked_at", "queued_at", "task_id",
    ]
    assert {n for n, c in columns.items() if not c.nullable} == {
        "id", "build_prefix", "project_segment", "status", "first_seen_at", "last_checked_at",
    }
    assert not any(c.foreign_keys for c in columns.values()), "keyed by the bucket: no FK"
    assert str(columns["status"].server_default.arg) == "'pending'"
    assert str(columns["first_seen_at"].server_default.arg) == "now()"
    assert columns["sentinel_last_modified"].type.timezone is True
    indexes = {c.args[0]: c for c in mod.op.create_index.call_args_list}
    assert list(indexes) == INDEXES
    assert all(c.args[1] == TABLE for c in indexes.values())
    assert indexes["uq_dfb_build_prefix"].args[2] == ["build_prefix"]
    assert indexes["uq_dfb_build_prefix"].kwargs["unique"] is True
    assert indexes["ix_dfb_pending_first_seen"].args[2] == ["first_seen_at"]
    assert str(indexes["ix_dfb_pending_first_seen"].kwargs["postgresql_where"]) == "status = 'pending'"
    assert "unique" not in indexes["ix_dfb_pending_first_seen"].kwargs
    # ON everywhere, an existing row untouched
    assert _executed(mod) == [(INSERT_SQL, {"key": "drop_folder_watcher", "description": mod._FLAGS[0][1]})]
    assert _ddl_calls(mod) == ["create_table", "create_index", "create_index", "execute"]


def test_downgrade_is_the_exact_inverse():
    mod = _load("m0254_down")
    mod.downgrade()
    assert _executed(mod) == [(DELETE_SQL, {"key": "drop_folder_watcher", "description": mod._FLAGS[0][1]})]
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == list(reversed(INDEXES))
    assert all(c.kwargs == {"table_name": TABLE} for c in mod.op.drop_index.call_args_list)
    assert [c.args for c in mod.op.drop_table.call_args_list] == [(TABLE,)]
    assert _ddl_calls(mod) == ["execute", "drop_index", "drop_index", "drop_table"]


def test_the_orm_matches_0254():
    from app.models.postgres import DropFolderBuild

    table = DropFolderBuild.__table__
    mod = _load("m0254_orm")
    mod.upgrade()
    ddl, checks = _created(mod)
    assert table.name == TABLE
    assert [c.name for c in table.columns] == list(ddl)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in ddl.items()}
    orm_checks = {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert orm_checks == checks
    for name in ("build_prefix", "project_segment", "status", "sentinel_etag", "task_id"):
        assert table.c[name].type.length == ddl[name].type.length, name
    for name in ("status", "first_seen_at", "last_checked_at"):
        assert table.c[name].server_default is not None, name
    assert max(len(s) for s in dfw.BUILD_STATUSES) <= table.c["status"].type.length
    indexes = {ix.name: ix for ix in table.indexes}
    assert set(indexes) == set(INDEXES)
    assert indexes["uq_dfb_build_prefix"].unique and [c.name for c in indexes["uq_dfb_build_prefix"].columns] == [
        "build_prefix"
    ]
    assert str(indexes["ix_dfb_pending_first_seen"].dialect_options["postgresql"]["where"]) == "status = 'pending'"
