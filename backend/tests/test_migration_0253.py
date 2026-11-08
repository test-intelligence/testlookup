"""Migration 0253 (W31 STRAT-12): ``subject_rights_jobs`` + ``users.erased_at`` — shape only.

* the chain 0252 -> 0253, with 0253 the only head (at the time of writing);
* the CHECK literals are ``subject_rights_vocab.JOB_KINDS`` / ``JOB_STATUSES`` /
  ``REQUEST_SOURCES`` / ``ERASURE_PHASES``; an erasure is admin-only;
* the five indexes (two partial uniques) and the FK rules (subject RESTRICT,
  requester SET NULL);
* ``users.erased_at`` + the partial ``ix_users_erased_at``, built CONCURRENTLY
  inside ``autocommit_block`` (the 0166+ rule — ``users`` already exists);
* the downgrade drops exactly what the upgrade made, in reverse;
* the ORM ``SubjectRightsJob`` / ``User.erased_at`` are in step with the DDL.

No database: the real DDL runs in the ``*_postgres.py`` modules (each migrates
its throwaway database to head first).
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import subject_rights_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0253 = VERSIONS / "0253_subject_rights.py"
TABLE = "subject_rights_jobs"
INDEXES = ["ix_srj_subject_kind", "uq_srj_one_live_export", "uq_srj_one_live_erasure", "ix_srj_status_requested",
           "ix_srj_requester"]
LIVE_EXPORT = "kind = 'export' AND status IN ('queued', 'running')"
LIVE_ERASURE = "kind = 'erasure' AND status IN ('queued', 'running')"
CHECKS = {
    "ck_srj_kind": "kind IN ('export', 'erasure')",
    "ck_srj_status": "status IN ('queued', 'running', 'succeeded', 'failed', 'expired')",
    "ck_srj_source": "source IN ('self', 'admin')",
    "ck_srj_phase": "phase IS NULL OR phase IN ('lockout', 'postgres', 'objects', 'redis', 'done')",
    "ck_srj_erasure_is_admin": "kind <> 'erasure' OR source = 'admin'",
}
# W38 2026-11-07: migration 0256 widened these two CHECKs (D-11, the "scim" source); 0253's DDL above is
# pinned as written -- the ORM carries 0256's text (pinned by tests/test_migration_0256.py).
WIDENED_BY_0256 = {
    "ck_srj_source": "source IN ('self', 'admin', 'scim')",
    "ck_srj_erasure_is_admin": "kind <> 'erasure' OR source IN ('admin', 'scim')",
}
SOURCES_0253 = vocab.REQUEST_SOURCES[:2]   # ("self", "admin"); 0256 appended "scim"


def _load(name: str, *, invalid: bool = False, as_sql: bool = False):
    spec = importlib.util.spec_from_file_location(name, M0253)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    mod.op.get_context.return_value.as_sql = as_sql
    mod.op.get_bind.return_value.execute.return_value.first.return_value = (1,) if invalid else None
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


def _executed(mod) -> list[str]:
    return [str(c.args[0]) for c in mod.op.execute.call_args_list]


def test_the_chain_is_linear_with_0253_on_0252():
    revisions = _revisions()
    assert revisions["0253"] == "0252"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0252") == 1, "0252 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0253"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0253.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_the_check_literals_are_the_vocabulary():
    mod = _load("m0253_vocab")
    assert mod._KINDS == vocab.JOB_KINDS
    assert mod._STATUSES == vocab.JOB_STATUSES
    assert mod._SOURCES == SOURCES_0253 == ("self", "admin")
    assert mod._PHASES == vocab.ERASURE_PHASES
    assert tuple(re.findall(r"'([^']+)'", mod._LIVE)) == vocab.LIVE_STATUSES


def _created(mod):
    (create,) = mod.op.create_table.call_args_list
    assert create.args[0] == TABLE
    columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    checks = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    return columns, checks


def test_upgrade_shape():
    mod = _load("m0253_up")
    assert (mod.revision, mod.down_revision) == ("0253", "0252")
    mod.upgrade()
    columns, checks = _created(mod)
    assert checks == CHECKS
    for name, literals in (("ck_srj_kind", vocab.JOB_KINDS), ("ck_srj_status", vocab.JOB_STATUSES),
                           ("ck_srj_source", SOURCES_0253), ("ck_srj_phase", vocab.ERASURE_PHASES)):
        assert tuple(re.findall(r"'([^']+)'", checks[name])) == literals, name
    assert list(columns) == [
        "id", "kind", "status", "source", "phase", "subject_user_id", "requested_by_id", "storage_key", "bytes",
        "file_count", "manifest_sha256", "archive_sha256", "signature", "signing_key_id", "counts", "error_code",
        "error", "attempts", "requested_at", "started_at", "finished_at", "expires_at",
    ]
    (subject_fk,) = columns["subject_user_id"].foreign_keys
    (requester_fk,) = columns["requested_by_id"].foreign_keys
    assert (subject_fk.target_fullname, subject_fk.ondelete) == ("users.id", "RESTRICT")
    assert (requester_fk.target_fullname, requester_fk.ondelete) == ("users.id", "SET NULL")
    assert columns["subject_user_id"].nullable is False and columns["requested_by_id"].nullable is True
    assert str(columns["counts"].server_default.arg) == "'{}'::jsonb"
    assert str(columns["status"].server_default.arg) == "'queued'"
    assert str(columns["source"].server_default.arg) == "'admin'"
    indexes = {c.args[0]: c for c in mod.op.create_index.call_args_list}
    assert list(indexes) == INDEXES
    assert all(c.args[1] == TABLE for c in indexes.values())
    assert str(indexes["uq_srj_one_live_export"].kwargs["postgresql_where"]) == LIVE_EXPORT
    assert str(indexes["uq_srj_one_live_erasure"].kwargs["postgresql_where"]) == LIVE_ERASURE
    assert indexes["uq_srj_one_live_export"].kwargs["unique"] is True
    assert indexes["uq_srj_one_live_erasure"].kwargs["unique"] is True
    assert indexes["uq_srj_one_live_export"].args[2] == ["subject_user_id"]
    assert not any(c.kwargs.get("unique") for n, c in indexes.items() if not n.startswith("uq_"))
    ((table, column),) = [c.args for c in mod.op.add_column.call_args_list]
    assert (table, column.name, column.nullable) == ("users", "erased_at", True)
    assert column.type.timezone is True and column.server_default is None
    # users EXISTS: the index is CONCURRENT inside autocommit_block (the 0166+ rule), never op.create_index
    assert mod.op.get_context.return_value.autocommit_block.called
    assert _executed(mod) == [
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_erased_at ON users (erased_at) WHERE erased_at IS NOT NULL",
    ]
    assert _ddl_calls(mod) == ["create_table"] + ["create_index"] * 5 + ["add_column", "execute"]


def test_upgrade_drops_an_invalid_leftover_first():
    mod = _load("m0253_invalid", invalid=True)
    mod.upgrade()
    assert _executed(mod) == [
        "DROP INDEX CONCURRENTLY IF EXISTS ix_users_erased_at",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_erased_at ON users (erased_at) WHERE erased_at IS NOT NULL",
    ]


def test_offline_upgrade_does_not_probe_the_database():
    mod = _load("m0253_offline", as_sql=True)
    mod.upgrade()
    assert not mod.op.get_bind.called


def test_downgrade_is_the_exact_inverse():
    mod = _load("m0253_down")
    mod.downgrade()
    assert _executed(mod) == ["DROP INDEX CONCURRENTLY IF EXISTS ix_users_erased_at"]
    assert [c.args for c in mod.op.drop_column.call_args_list] == [("users", "erased_at")]
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == list(reversed(INDEXES))
    assert all(c.kwargs == {"table_name": TABLE} for c in mod.op.drop_index.call_args_list)
    assert [c.args for c in mod.op.drop_table.call_args_list] == [(TABLE,)]
    assert _ddl_calls(mod) == ["execute", "drop_column"] + ["drop_index"] * 5 + ["drop_table"]


def test_the_orm_matches_0253():
    from app.models.postgres import SubjectRightsJob, User

    table = SubjectRightsJob.__table__
    mod = _load("m0253_orm")
    mod.upgrade()
    ddl, checks = _created(mod)
    assert table.name == TABLE
    assert [c.name for c in table.columns] == list(ddl)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in ddl.items()}
    orm_checks = {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert orm_checks == {**checks, **WIDENED_BY_0256}
    for name in ("subject_user_id", "requested_by_id"):
        (orm_fk,) = table.c[name].foreign_keys
        (ddl_fk,) = ddl[name].foreign_keys
        assert (orm_fk.target_fullname, orm_fk.ondelete) == (ddl_fk.target_fullname, ddl_fk.ondelete)
    for name in ("status", "source", "counts", "attempts", "requested_at"):
        assert table.c[name].server_default is not None, name
    for name in ("kind", "status", "source", "phase", "signature", "error_code", "manifest_sha256", "signing_key_id"):
        assert table.c[name].type.length == ddl[name].type.length, name
    assert max(len(s) for s in vocab.JOB_STATUSES) <= table.c["status"].type.length
    assert max(len(s) for s in vocab.ERASURE_PHASES) <= table.c["phase"].type.length
    assert max(len(s) for s in vocab.JOB_KINDS) <= table.c["kind"].type.length
    assert max(len(s) for s in vocab.REQUEST_SOURCES) <= table.c["source"].type.length
    assert max(len(k) for k in vocab.ERROR_SENTENCES) <= table.c["error_code"].type.length
    indexes = {ix.name: ix for ix in table.indexes}
    assert set(indexes) == set(INDEXES)
    assert str(indexes["uq_srj_one_live_export"].dialect_options["postgresql"]["where"]) == LIVE_EXPORT
    assert str(indexes["uq_srj_one_live_erasure"].dialect_options["postgresql"]["where"]) == LIVE_ERASURE
    assert indexes["uq_srj_one_live_export"].unique and indexes["uq_srj_one_live_erasure"].unique
    users = User.__table__
    assert users.c["erased_at"].nullable is True and users.c["erased_at"].type.timezone is True
    (erased_ix,) = [ix for ix in users.indexes if ix.name == "ix_users_erased_at"]
    assert [c.name for c in erased_ix.columns] == ["erased_at"]
    assert str(erased_ix.dialect_options["postgresql"]["where"]) == "erased_at IS NOT NULL"
