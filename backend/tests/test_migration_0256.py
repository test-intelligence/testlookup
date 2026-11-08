"""Migration 0256 (W38: W31 F-1 erasure requests, F-2 the ``scim`` source) — shape only.

* the chain 0255 -> 0256, with 0256 the only head (at the time of writing); no
  application import;
* ``subject_erasure_requests``: the CHECK literals are
  ``subject_rights_vocab.ERASURE_REQUEST_STATUSES`` / ``DECLINE_CODES``; a
  pending row has no ``decided_at``, exactly a declined row has a code; FK
  rules (subject RESTRICT, decider and job SET NULL); the partial unique
  ``uq_ser_one_pending`` and ``ix_ser_status_requested`` built in the same
  transaction (a NEW table);
* ``subject_rights_jobs`` (D-11): ``ck_srj_source`` gains ``'scim'`` (=
  ``REQUEST_SOURCES``) and an erasure is ``admin`` or ``scim`` -- drop + create;
* the downgrade FAILS CLOSED while a ``scim`` job exists and otherwise drops
  exactly what the upgrade made, in reverse (restoring 0253's literals);
* the ORM ``SubjectErasureRequest`` is in step with the DDL.

No database: the real DDL round trip (upgrade, downgrade to 0255, upgrade) is
recorded in the W38 G-A report and exercised by
``tests/integration/test_subject_erasure_requests_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import subject_rights_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0253 = VERSIONS / "0253_subject_rights.py"
M0256 = VERSIONS / "0256_erasure_requests_scim_source.py"
TABLE = "subject_erasure_requests"
JOBS = "subject_rights_jobs"
COLUMNS = ["id", "subject_user_id", "status", "requested_at", "decided_at", "decided_by_id", "decline_code",
           "erasure_job_id"]
INDEXES = ["uq_ser_one_pending", "ix_ser_status_requested"]
CHECKS = {
    "ck_ser_status": "status IN ('pending', 'withdrawn', 'declined', 'accepted')",
    "ck_ser_decided": "(status = 'pending') = (decided_at IS NULL)",
    "ck_ser_decline_code": (
        "decline_code IS NULL OR decline_code IN ('legal_obligation', 'active_contract', 'identity_unconfirmed', 'other')"
    ),
    "ck_ser_declined_has_code": "(status = 'declined') = (decline_code IS NOT NULL)",
}
JOB_CHECKS = {
    "ck_srj_source": "source IN ('self', 'admin', 'scim')",
    "ck_srj_erasure_is_admin": "kind <> 'erasure' OR source IN ('admin', 'scim')",
}
JOB_CHECKS_0253 = {
    "ck_srj_source": "source IN ('self', 'admin')",
    "ck_srj_erasure_is_admin": "kind <> 'erasure' OR source = 'admin'",
}


def _load(path: Path, name: str, *, scim_rows: int = 0, as_sql: bool = False):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = MagicMock()
    mod.op.get_context.return_value.as_sql = as_sql
    mod.op.get_bind.return_value.execute.return_value.scalar.return_value = scim_rows
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


def _ddl_calls(mod) -> list[str]:
    return [c[0] for c in mod.op.method_calls if c[0] not in {"get_context", "get_bind"}]


def _created(mod):
    (create,) = mod.op.create_table.call_args_list
    assert create.args[0] == TABLE
    columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    checks = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    return columns, checks


def test_the_chain_is_linear_with_0256_on_0255():
    revisions = _revisions()
    assert revisions["0256"] == "0255"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0255") == 1, "0255 has two children: the chain forked"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0256"
    mod = _load(M0256, "m0256_rev")
    assert "Revision ID: 0256" in mod.__doc__ and "Revises: 0255" in mod.__doc__


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0256.read_text(encoding="utf-8"))
    modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    modules += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_the_check_literals_are_the_vocabulary_and_the_downgrade_restores_0253s():
    mod = _load(M0256, "m0256_vocab")
    m0253 = _load(M0253, "m0253_for_0256")
    assert mod._STATUSES == vocab.ERASURE_REQUEST_STATUSES
    assert mod._DECLINE_CODES == vocab.DECLINE_CODES
    assert mod._SOURCES == vocab.REQUEST_SOURCES and vocab.SOURCE_SCIM in mod._SOURCES
    assert mod._SOURCES_0253 == m0253._SOURCES
    assert set(mod._SOURCES) - set(mod._SOURCES_0253) == {"scim"}
    assert "self" not in mod._ERASURE_SOURCES, "an erasure is never self-service"
    assert tuple(re.findall(r"'([^']+)'", mod._PENDING)) == vocab.ERASURE_REQUEST_STATUSES[:1]


def test_upgrade_shape():
    mod = _load(M0256, "m0256_up")
    assert (mod.revision, mod.down_revision) == ("0256", "0255")
    mod.upgrade()
    assert _ddl_calls(mod) == (
        ["create_table"] + ["create_index"] * 2 + ["drop_constraint", "create_check_constraint"] * 2
    )
    columns, checks = _created(mod)
    assert list(columns) == COLUMNS
    assert checks == CHECKS
    assert tuple(re.findall(r"'([^']+)'", checks["ck_ser_status"])) == vocab.ERASURE_REQUEST_STATUSES
    assert tuple(re.findall(r"'([^']+)'", checks["ck_ser_decline_code"])) == vocab.DECLINE_CODES
    assert {n for n, c in columns.items() if not c.nullable} == {"id", "subject_user_id", "status", "requested_at"}
    fks = {n: next(iter(c.foreign_keys)) for n, c in columns.items() if c.foreign_keys}
    assert {n: (fk.target_fullname, fk.ondelete) for n, fk in fks.items()} == {
        "subject_user_id": ("users.id", "RESTRICT"),
        "decided_by_id": ("users.id", "SET NULL"),
        "erasure_job_id": ("subject_rights_jobs.id", "SET NULL"),
    }
    assert columns["status"].type.length == 20 and columns["decline_code"].type.length == 40
    assert str(columns["status"].server_default.arg) == "'pending'"
    assert str(columns["requested_at"].server_default.arg) == "now()"
    assert columns["requested_at"].type.timezone is True and columns["decided_at"].type.timezone is True
    indexes = {c.args[0]: c for c in mod.op.create_index.call_args_list}
    assert list(indexes) == INDEXES and all(c.args[1] == TABLE for c in indexes.values())
    pending = indexes["uq_ser_one_pending"]
    assert pending.args[2] == ["subject_user_id"] and pending.kwargs["unique"] is True
    assert str(pending.kwargs["postgresql_where"]) == "status = 'pending'"
    queue = indexes["ix_ser_status_requested"]
    assert queue.args[2][0] == "status" and str(queue.args[2][1]) == "requested_at DESC"
    assert queue.kwargs == {}, "a NEW table: plain, in the same transaction"
    assert not mod.op.execute.called and not mod.op.get_context.return_value.autocommit_block.called
    assert [c.args for c in mod.op.drop_constraint.call_args_list] == [
        ("ck_srj_source", JOBS), ("ck_srj_erasure_is_admin", JOBS),
    ]
    assert all(c.kwargs == {"type_": "check"} for c in mod.op.drop_constraint.call_args_list)
    created = {c.args[0]: (c.args[1], c.args[2]) for c in mod.op.create_check_constraint.call_args_list}
    assert created == {name: (JOBS, text) for name, text in JOB_CHECKS.items()}
    assert tuple(re.findall(r"'([^']+)'", JOB_CHECKS["ck_srj_source"])) == vocab.REQUEST_SOURCES


def test_downgrade_is_the_exact_inverse():
    mod = _load(M0256, "m0256_down", scim_rows=0)
    mod.downgrade()
    assert _ddl_calls(mod) == (
        ["drop_constraint", "create_check_constraint"] * 2 + ["drop_index"] * 2 + ["drop_table"]
    )
    assert [c.args for c in mod.op.drop_constraint.call_args_list] == [
        ("ck_srj_erasure_is_admin", JOBS), ("ck_srj_source", JOBS),
    ]
    restored = {c.args[0]: (c.args[1], c.args[2]) for c in mod.op.create_check_constraint.call_args_list}
    assert restored == {name: (JOBS, text) for name, text in JOB_CHECKS_0253.items()}
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == list(reversed(INDEXES))
    assert all(c.kwargs == {"table_name": TABLE} for c in mod.op.drop_index.call_args_list)
    assert [c.args for c in mod.op.drop_table.call_args_list] == [(TABLE,)]


def test_the_downgrade_restores_exactly_0253s_check_text():
    m0253 = _load(M0253, "m0253_checks")
    m0253.upgrade()
    (create,) = m0253.op.create_table.call_args_list
    checks_0253 = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    assert {name: checks_0253[name] for name in JOB_CHECKS_0253} == JOB_CHECKS_0253


def test_downgrade_fails_closed_while_a_scim_job_exists():
    mod = _load(M0256, "m0256_refuse", scim_rows=2)
    with pytest.raises(RuntimeError, match="2 subject rights job"):
        mod.downgrade()
    assert _ddl_calls(mod) == [], "nothing may be dropped before the refusal"


def test_offline_downgrade_does_not_probe_the_database():
    mod = _load(M0256, "m0256_offline", as_sql=True)
    mod.downgrade()
    assert not mod.op.get_bind.called
    assert mod.op.drop_table.call_count == 1


def test_the_orm_matches_0256():
    from app.models.postgres import SubjectErasureRequest, SubjectRightsJob

    table = SubjectErasureRequest.__table__
    mod = _load(M0256, "m0256_orm")
    mod.upgrade()
    ddl, checks = _created(mod)
    assert table.name == TABLE
    assert [c.name for c in table.columns] == list(ddl)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in ddl.items()}
    orm_checks = {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert orm_checks == checks
    for name in ("subject_user_id", "decided_by_id", "erasure_job_id"):
        (orm_fk,) = table.c[name].foreign_keys
        (ddl_fk,) = ddl[name].foreign_keys
        assert (orm_fk.target_fullname, orm_fk.ondelete) == (ddl_fk.target_fullname, ddl_fk.ondelete), name
    for name in ("status", "decline_code"):
        assert table.c[name].type.length == ddl[name].type.length, name
    for name in ("status", "requested_at"):
        assert table.c[name].server_default is not None, name
    assert max(len(s) for s in vocab.ERASURE_REQUEST_STATUSES) <= table.c["status"].type.length
    assert max(len(s) for s in vocab.DECLINE_CODES) <= table.c["decline_code"].type.length
    indexes = {ix.name: ix for ix in table.indexes}
    assert set(indexes) == set(INDEXES)
    assert indexes["uq_ser_one_pending"].unique
    assert str(indexes["uq_ser_one_pending"].dialect_options["postgresql"]["where"]) == "status = 'pending'"
    jobs = {c.name: str(c.sqltext) for c in SubjectRightsJob.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"}
    assert {name: jobs[name] for name in JOB_CHECKS} == JOB_CHECKS
    assert max(len(s) for s in vocab.REQUEST_SOURCES) <= SubjectRightsJob.__table__.c["source"].type.length
