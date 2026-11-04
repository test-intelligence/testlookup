"""Migration 0247 (W24 ADM-05): ``project_data_jobs`` — shape only, no database.

* the chain 0246 -> 0247, with 0247 the only head (at the time of writing);
* the CHECK literal lists are ``project_export_vocab.JOB_KINDS`` and 0247's
  HISTORIC status literals (a migration file never changes; W30's 0252 grew the
  vocabulary by ``uploading`` — ``tests/test_migration_0252.py`` pins the
  current ``JOB_STATUSES``);
* the downgrade drops exactly what the upgrade created, in reverse order.

(W30, deliberate: the ORM-vs-DDL comparison moved to ``tests/test_migration_0252.py``
— the ORM now matches 0247 + 0252.)

The real DDL runs against Postgres in the two W24 ``*_postgres.py`` modules
(each migrates its throwaway database to head first).
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import project_export_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0247 = VERSIONS / "0247_project_data_jobs.py"
COLUMNS = [
    "id", "kind", "status", "project_id", "project_name", "requested_by_id", "client_token", "storage_key",
    "bytes", "file_count", "manifest_sha256", "archive_sha256", "signature", "signing_key_id", "counts",
    "report", "progress", "error_code", "error", "attempts", "requested_at", "started_at", "finished_at",
    "expires_at",
]
INDEXES = [
    "ix_pdj_project_requested", "uq_pdj_one_live_export", "uq_pdj_import_token", "ix_pdj_status_requested",
    "ix_pdj_requester_kind",
]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, M0247)
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


def test_the_chain_is_linear_with_0247_on_0246():
    revisions = _revisions()
    assert revisions["0247"] == "0246"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0246") == 1, "0246 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0247"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0247.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


#: 0247's status literals as written in 2026-10 (W24). W30 (0252) added "uploading" to the vocabulary; the
#: 0247 file keeps its historic literals forever.
STATUSES_0247 = ("queued", "running", "succeeded", "failed", "expired")


def test_check_literals_are_the_vocabulary():
    mod, create = _created("m0247_vocab")
    assert mod._KINDS == vocab.JOB_KINDS and mod._STATUSES == STATUSES_0247
    assert set(STATUSES_0247) < set(vocab.JOB_STATUSES)   # the vocabulary only grew
    checks = _ddl_checks(create)
    assert set(checks) == {"ck_project_data_jobs_kind", "ck_project_data_jobs_status"}
    assert tuple(re.findall(r"'([^']+)'", checks["ck_project_data_jobs_kind"])) == vocab.JOB_KINDS
    assert tuple(re.findall(r"'([^']+)'", checks["ck_project_data_jobs_status"])) == STATUSES_0247


def test_upgrade_shape():
    mod, create = _created("m0247_up")
    assert (mod.revision, mod.down_revision) == ("0247", "0246")
    assert create.args[0] == "project_data_jobs"
    columns = _ddl_columns(create)
    assert list(columns) == COLUMNS
    not_null = {name for name, c in columns.items() if not c.nullable}
    assert not_null == {"id", "kind", "status", "counts", "attempts", "requested_at"}
    fks = {name: next(iter(columns[name].foreign_keys)) for name in ("project_id", "requested_by_id")}
    assert fks["project_id"].target_fullname == "projects.id" and fks["project_id"].ondelete == "SET NULL"
    assert fks["requested_by_id"].target_fullname == "users.id" and fks["requested_by_id"].ondelete == "SET NULL"
    assert str(columns["counts"].server_default.arg) == "'{}'::jsonb"
    assert str(columns["attempts"].server_default.arg) == "0"
    assert str(columns["status"].server_default.arg) == "'queued'"
    assert str(columns["requested_at"].server_default.arg) == "now()"
    calls = mod.op.create_index.call_args_list
    assert [c.args[0] for c in calls] == INDEXES
    assert all(c.args[1] == "project_data_jobs" for c in calls)
    by_name = {c.args[0]: c for c in calls}
    assert [str(x) for x in by_name["ix_pdj_project_requested"].args[2]] == ["project_id", "requested_at DESC"]
    assert by_name["uq_pdj_one_live_export"].args[2] == ["project_id"]
    assert by_name["uq_pdj_import_token"].args[2] == ["requested_by_id", "client_token"]
    assert by_name["ix_pdj_status_requested"].args[2] == ["status", "requested_at"]
    assert [str(x) for x in by_name["ix_pdj_requester_kind"].args[2]] == ["requested_by_id", "kind", "requested_at DESC"]
    unique = {c.args[0] for c in calls if c.kwargs.get("unique")}
    assert unique == {"uq_pdj_one_live_export", "uq_pdj_import_token"}
    assert str(by_name["uq_pdj_one_live_export"].kwargs["postgresql_where"]) == (
        "kind = 'export' AND status IN ('queued', 'running')"
    )
    assert str(by_name["uq_pdj_import_token"].kwargs["postgresql_where"]) == "kind = 'import' AND status <> 'failed'"
    assert [c[0] for c in mod.op.method_calls] == ["create_table"] + ["create_index"] * 5


def test_downgrade_drops_exactly_what_the_upgrade_created_in_reverse():
    mod = _load("m0247_down")
    mod.downgrade()
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == list(reversed(INDEXES))
    assert all(c.kwargs["table_name"] == "project_data_jobs" for c in mod.op.drop_index.call_args_list)
    mod.op.drop_table.assert_called_once_with("project_data_jobs")
    assert [c[0] for c in mod.op.method_calls] == ["drop_index"] * 5 + ["drop_table"]
