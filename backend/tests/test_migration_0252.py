"""Migration 0252 (W30 ADM-05 phase 2): ``project_data_jobs`` gains options / upload / cleanup — shape only.

* the chain 0251 -> 0252, with 0252 the only head (at the time of writing);
* the status CHECK literals are ``project_export_vocab.JOB_STATUSES`` (with
  ``uploading``); the downgrade restores 0247's historic literals;
* ``ck_project_data_jobs_uploading_is_import`` — only an import uploads;
* the downgrade FAILS CLOSED while a job is ``uploading`` and otherwise drops
  exactly what the upgrade made, in reverse;
* the ORM ``ProjectDataJob`` is in step with 0247's DDL + 0252's changes
  (moved here from ``test_migration_0247.py``, W30 deliberate).

No database: the real DDL runs in the ``*_postgres.py`` modules (each migrates
its throwaway database to head first).
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import project_export_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0247 = VERSIONS / "0247_project_data_jobs.py"
M0252 = VERSIONS / "0252_project_data_job_history.py"
NEW_COLUMNS = ["options", "upload", "cleanup"]
STATUS_CHECK = "status IN ('uploading', 'queued', 'running', 'succeeded', 'failed', 'expired')"
UPLOADING_CHECK = "status <> 'uploading' OR kind = 'import'"


def _load(path: Path, name: str, *, uploading: int = 0, as_sql: bool = False):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    mod.op.get_context.return_value.as_sql = as_sql
    mod.op.get_bind.return_value.execute.return_value.scalar.return_value = uploading
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
    return [c[0] for c in mod.op.method_calls if c[0] not in {"get_context", "get_bind"}]


def test_the_chain_is_linear_with_0252_on_0251():
    revisions = _revisions()
    assert revisions["0252"] == "0251"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0251") == 1, "0251 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0252"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0252.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_status_literals_are_the_vocabulary_and_the_downgrade_restores_0247s():
    mod = _load(M0252, "m0252_vocab")
    m0247 = _load(M0247, "m0247_for_0252")
    assert mod._STATUSES == vocab.JOB_STATUSES and "uploading" in mod._STATUSES
    assert mod._STATUSES_0247 == m0247._STATUSES
    assert set(mod._STATUSES) - set(mod._STATUSES_0247) == {"uploading"}


def test_upgrade_shape():
    mod = _load(M0252, "m0252_up")
    assert (mod.revision, mod.down_revision) == ("0252", "0251")
    mod.upgrade()
    added = [c.args for c in mod.op.add_column.call_args_list]
    assert [table for table, _column in added] == ["project_data_jobs"] * 3
    columns = {column.name: column for _table, column in added}
    assert list(columns) == NEW_COLUMNS
    assert columns["options"].nullable is False
    assert str(columns["options"].server_default.arg) == "'{}'::jsonb"
    assert columns["upload"].nullable is True and columns["cleanup"].nullable is True
    assert all(columns[n].type.__class__.__name__ == "JSONB" for n in NEW_COLUMNS)
    (drop,) = mod.op.drop_constraint.call_args_list
    assert drop.args == ("ck_project_data_jobs_status", "project_data_jobs") and drop.kwargs == {"type_": "check"}
    checks = {c.args[0]: c.args[2] for c in mod.op.create_check_constraint.call_args_list}
    assert checks == {"ck_project_data_jobs_status": STATUS_CHECK, "ck_project_data_jobs_uploading_is_import": UPLOADING_CHECK}
    assert tuple(re.findall(r"'([^']+)'", checks["ck_project_data_jobs_status"])) == vocab.JOB_STATUSES
    assert _ddl_calls(mod) == ["add_column"] * 3 + ["drop_constraint"] + ["create_check_constraint"] * 2
    assert not mod.op.create_index.called, "0252 adds no index"


def test_downgrade_is_the_exact_inverse():
    mod = _load(M0252, "m0252_down", uploading=0)
    mod.downgrade()
    assert [c.args[0] for c in mod.op.drop_constraint.call_args_list] == [
        "ck_project_data_jobs_uploading_is_import", "ck_project_data_jobs_status",
    ]
    (restored,) = mod.op.create_check_constraint.call_args_list
    assert restored.args == (
        "ck_project_data_jobs_status", "project_data_jobs",
        "status IN ('queued', 'running', 'succeeded', 'failed', 'expired')",
    )
    assert [c.args for c in mod.op.drop_column.call_args_list] == [
        ("project_data_jobs", "cleanup"), ("project_data_jobs", "upload"), ("project_data_jobs", "options"),
    ]
    assert _ddl_calls(mod) == ["drop_constraint"] * 2 + ["create_check_constraint"] + ["drop_column"] * 3


def test_downgrade_fails_closed_while_a_job_is_uploading():
    mod = _load(M0252, "m0252_refuse", uploading=2)
    with pytest.raises(RuntimeError, match="2 project data job"):
        mod.downgrade()
    assert _ddl_calls(mod) == [], "nothing may be dropped before the refusal"


def test_offline_downgrade_does_not_probe_the_database():
    mod = _load(M0252, "m0252_offline", as_sql=True)
    mod.downgrade()
    assert not mod.op.get_bind.called
    assert mod.op.drop_column.call_count == 3


def _ddl_0247():
    mod = _load(M0247, "m0247_ddl")
    mod.upgrade()
    (create,) = mod.op.create_table.call_args_list
    columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    checks = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    return mod, columns, checks


def test_the_orm_matches_0247_plus_0252():
    from app.models.postgres import ProjectDataJob

    table = ProjectDataJob.__table__
    m0247, ddl, checks_0247 = _ddl_0247()
    up = _load(M0252, "m0252_orm")
    up.upgrade()
    added = {column.name: column for _t, column in (c.args for c in up.op.add_column.call_args_list)}
    expected = {**ddl, **added}
    assert [c.name for c in table.columns] == list(expected)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in expected.items()}
    checks = {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert checks == {
        "ck_project_data_jobs_kind": checks_0247["ck_project_data_jobs_kind"],
        "ck_project_data_jobs_status": STATUS_CHECK,
        "ck_project_data_jobs_uploading_is_import": UPLOADING_CHECK,
    }
    for name in ("project_id", "requested_by_id"):
        (orm_fk,) = table.c[name].foreign_keys
        (ddl_fk,) = ddl[name].foreign_keys
        assert (orm_fk.target_fullname, orm_fk.ondelete) == (ddl_fk.target_fullname, ddl_fk.ondelete)
    for name in ("status", "counts", "attempts", "requested_at", "options"):
        assert table.c[name].server_default is not None, name
    for name in ("kind", "status", "project_name", "signature", "error_code", "manifest_sha256"):
        assert table.c[name].type.length == ddl[name].type.length, name
    assert max(len(s) for s in vocab.JOB_STATUSES) <= table.c["status"].type.length
    indexes = {ix.name: ix for ix in table.indexes}
    assert set(indexes) == {
        "ix_pdj_project_requested", "uq_pdj_one_live_export", "uq_pdj_import_token", "ix_pdj_status_requested",
        "ix_pdj_requester_kind",
    }
    assert str(indexes["uq_pdj_one_live_export"].dialect_options["postgresql"]["where"]) == (
        "kind = 'export' AND status IN ('queued', 'running')"
    )
    assert str(indexes["uq_pdj_import_token"].dialect_options["postgresql"]["where"]) == (
        "kind = 'import' AND status <> 'failed'"
    )
    assert m0247._STATUSES == up._STATUSES_0247


def test_the_cleanup_ledger_is_deferred_and_never_loaded_by_a_plain_select():
    from sqlalchemy import inspect as sa_inspect, select

    from app.models.postgres import ProjectDataJob

    assert sa_inspect(ProjectDataJob).attrs["cleanup"].deferred is True
    assert sa_inspect(ProjectDataJob).attrs["options"].deferred is False
    sql = str(select(ProjectDataJob))
    assert "project_data_jobs.options" in sql and "project_data_jobs.upload" in sql
    assert "cleanup" not in sql
