"""Migrations 0241 / 0242 (W22b ACT-03 / ACT-06): shape only, no database.

* the chain 0240 -> 0241 -> 0242, with one head at or after 0242;
* 0241's CHECK literal lists are ``retention_vocab.EXPORT_TIERS`` /
  ``EXPORT_TARGETS`` (a migration must not import application code, so the
  copy is pinned here);
* each downgrade undoes exactly what its upgrade did, in reverse order, and
  0242's downgrade freezes inherited values without ever enabling retention;
* neither migration builds an index or imports ``app``;
* 0242 never spells ``enabled = true`` or a true ``server_default`` (the
  whole-file scan of ``test_retention_activation.py`` agrees);
* the ORM is in step with 0242 (it REPLACES the ORM half of
  ``test_migrations_0239_0240.py::test_the_orm_matches_0240``).

The real DDL is exercised against Postgres in
``tests/integration/test_migrations_0241_0242_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import retention_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0241 = VERSIONS / "0241_deletion_job_exports.py"
M0242 = VERSIONS / "0242_retention_schedule_instance_policy.py"

_SIX = ("enabled", "raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs")
_EXPORT_COLUMNS = [
    "export_tier", "export_target", "export_bytes", "export_manifest_sha256", "export_archive_sha256",
    "export_location", "export_verified_at", "export_meta", "collect_expires_at", "collected_at",
    "acknowledged_at", "acknowledged_by_id",
]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
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


# ── the chain ────────────────────────────────────────────────────────────────

def test_the_chain_is_linear_with_one_head_at_or_after_0242():
    revisions = _revisions()
    assert revisions["0241"] == "0240" and revisions["0242"] == "0241"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0240") == 1 and parents.count("0241") == 1   # no fork at or below
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0242"


def test_neither_migration_imports_the_application():
    for path in (M0241, M0242):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not [m for m in modules if m == "app" or m.startswith("app.")], (path.name, modules)


def test_neither_migration_builds_an_index():
    for path in (M0241, M0242):
        source = path.read_text(encoding="utf-8")
        assert "create_index" not in source and "CREATE INDEX" not in source.upper(), path.name
    for path, name in ((M0241, "m0241_noidx"), (M0242, "m0242_noidx")):
        mod = _load(path, name)
        mod.upgrade()
        mod.op.create_index.assert_not_called()


# ── 0241: the deletion-job export columns ────────────────────────────────────

def test_0241_check_literals_are_the_vocabulary():
    mod = _load(M0241, "m0241_vocab")
    assert mod._EXPORT_TIERS == vocab.EXPORT_TIERS
    assert mod._EXPORT_TARGETS == vocab.EXPORT_TARGETS
    mod.upgrade()
    checks = {c.args[0]: c.args[2] for c in mod.op.create_check_constraint.call_args_list}
    assert tuple(re.findall(r"'([^']+)'", checks["ck_deletion_jobs_export_tier"])) == vocab.EXPORT_TIERS
    assert tuple(re.findall(r"'([^']+)'", checks["ck_deletion_jobs_export_target"])) == vocab.EXPORT_TARGETS


def test_0241_upgrade_shape():
    mod = _load(M0241, "m0241_up")
    assert (mod.revision, mod.down_revision) == ("0241", "0240")
    mod.upgrade()
    adds = mod.op.add_column.call_args_list
    assert [c.args[0] for c in adds] == ["deletion_jobs"] * 12
    columns = [c.args[1] for c in adds]
    assert [c.name for c in columns] == _EXPORT_COLUMNS
    assert all(c.nullable and c.server_default is None and c.default is None for c in columns)
    types = {c.name: c.type.__class__.__name__ for c in columns}
    assert types["export_bytes"] == "BigInteger" and types["export_meta"] == "JSONB"
    assert {c.name: getattr(c.type, "length", None) for c in columns if types[c.name] == "String"} == {
        "export_tier": 10, "export_target": 10, "export_manifest_sha256": 64,
        "export_archive_sha256": 64, "export_location": 500,
    }
    assert all(
        c.type.timezone for c in columns if types[c.name] == "DateTime"
    ) and sum(types[c.name] == "DateTime" for c in columns) == 4
    mod.op.create_foreign_key.assert_called_once_with(
        "fk_deletion_jobs_acknowledged_by", "deletion_jobs", "users",
        ["acknowledged_by_id"], ["id"], ondelete="SET NULL",
    )
    assert [c.args[:2] for c in mod.op.create_check_constraint.call_args_list] == [
        ("ck_deletion_jobs_export_tier", "deletion_jobs"),
        ("ck_deletion_jobs_export_target", "deletion_jobs"),
        ("ck_deletion_jobs_export_pair", "deletion_jobs"),
    ]
    pair = mod.op.create_check_constraint.call_args_list[2].args[2]
    assert pair == "(export_tier IS NULL) = (export_target IS NULL)"
    assert [c[0] for c in mod.op.method_calls] == (
        ["add_column"] * 12 + ["create_foreign_key"] + ["create_check_constraint"] * 3
    )


def test_0241_downgrade_undoes_the_upgrade_in_reverse():
    mod = _load(M0241, "m0241_down")
    mod.downgrade()
    assert [c.args for c in mod.op.drop_constraint.call_args_list] == [
        ("ck_deletion_jobs_export_pair", "deletion_jobs"),
        ("ck_deletion_jobs_export_target", "deletion_jobs"),
        ("ck_deletion_jobs_export_tier", "deletion_jobs"),
        ("fk_deletion_jobs_acknowledged_by", "deletion_jobs"),
    ]
    assert [c.kwargs["type_"] for c in mod.op.drop_constraint.call_args_list] == [
        "check", "check", "check", "foreignkey",
    ]
    assert [c.args for c in mod.op.drop_column.call_args_list] == [
        ("deletion_jobs", name) for name in reversed(_EXPORT_COLUMNS)
    ]
    assert [c[0] for c in mod.op.method_calls] == ["drop_constraint"] * 4 + ["drop_column"] * 12


# ── 0242: nullable policy columns, the window, the pause, the instance table ─

def test_0242_upgrade_relaxes_the_six_columns_without_touching_a_row():
    mod = _load(M0242, "m0242_up")
    assert (mod.revision, mod.down_revision) == ("0242", "0241")
    assert mod._INHERIT == _SIX
    assert set(mod._INHERIT) | {"purge_window_start_utc"} == set(vocab.INHERITABLE_FIELDS)
    assert mod._CODE_DEFAULTS == {
        "raw_events_days": 90, "runs_days": 365, "artifacts_days": 180, "audit_days": 2555,
        "keep_last_runs": vocab.KEEP_LAST_RUNS_DEFAULT,
    }
    mod.upgrade()
    alters = mod.op.alter_column.call_args_list
    assert [c.args for c in alters] == [("project_retention_policies", col) for col in _SIX]
    assert all(c.kwargs == {"nullable": True, "server_default": None} for c in alters)
    # The upgrade issues ONE statement of its own — the instance row's INSERT. No UPDATE: no row is rewritten.
    (insert,) = mod.op.execute.call_args_list
    assert insert.args == ("INSERT INTO instance_retention_policy (id) VALUES (1)",)


def test_0242_upgrade_adds_the_window_the_pause_and_the_instance_table():
    mod = _load(M0242, "m0242_shape")
    mod.upgrade()
    window, pause = mod.op.add_column.call_args_list
    assert window.args[0] == pause.args[0] == "project_retention_policies"
    assert window.args[1].name == "purge_window_start_utc" and window.args[1].nullable
    assert window.args[1].type.__class__.__name__ == "SmallInteger" and window.args[1].server_default is None
    assert pause.args[1].name == "purge_paused_until" and pause.args[1].nullable
    assert pause.args[1].type.timezone is True and pause.args[1].server_default is None
    mod.op.create_check_constraint.assert_called_once_with(
        "ck_retention_purge_window", "project_retention_policies",
        "purge_window_start_utc IS NULL OR purge_window_start_utc BETWEEN 0 AND 23",
    )
    (create,) = mod.op.create_table.call_args_list
    assert create.args[0] == "instance_retention_policy"
    columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    assert list(columns) == [
        "id", "enabled", "raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs",
        "purge_window_start_utc", "enabled_at", "enabled_by_id", "updated_at", "updated_by_user_id",
    ]
    assert columns["id"].primary_key and columns["id"].autoincrement is False
    enabled = columns["enabled"]
    assert not enabled.nullable and str(enabled.server_default.arg).lower() == "false"   # D12: ships off
    assert all(columns[c].nullable and columns[c].server_default is None for c in list(columns)[2:8])
    for fk_column in ("enabled_by_id", "updated_by_user_id"):
        (fk,) = columns[fk_column].foreign_keys
        assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    checks = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    assert checks == {
        "ck_instance_retention_single_row": "id = 1",
        "ck_instance_retention_window": "purge_window_start_utc IS NULL OR purge_window_start_utc BETWEEN 0 AND 23",
        "ck_instance_retention_keep_last": "keep_last_runs IS NULL OR keep_last_runs BETWEEN 0 AND 1000",
    }
    assert [c[0] for c in mod.op.method_calls] == (
        ["alter_column"] * 6 + ["add_column", "create_check_constraint", "add_column", "create_table", "execute"]
    )


def test_0242_never_spells_enabling_retention():
    """Not even in a comment: the whole-file scan of test_retention_activation.py reads comments too."""
    source = M0242.read_text(encoding="utf-8")
    assert re.search(r"enabled\s*=\s*true", source, re.IGNORECASE) is None
    assert re.search(r"server_default\s*=\s*[^,\n]*\btrue", source, re.IGNORECASE) is None
    assert "sa.true()" not in source and "sa.false()" in source


def test_0242_downgrade_freezes_inherited_values_and_never_enables():
    mod = _load(M0242, "m0242_down")
    mod.downgrade()
    names = [c[0] for c in mod.op.method_calls]
    assert names == (
        ["execute", "execute", "drop_table", "drop_constraint", "drop_column", "drop_column"] + ["alter_column"] * 6
    )
    freeze_instance, freeze_code = (c.args[0] for c in mod.op.execute.call_args_list)
    assert freeze_instance.startswith("UPDATE project_retention_policies AS p ")
    assert freeze_instance.endswith("FROM instance_retention_policy AS i WHERE i.id = 1")
    for col, default in mod._CODE_DEFAULTS.items():
        assert f"{col} = COALESCE(p.{col}, i.{col}, {default})" in freeze_instance
        assert f"{col} = COALESCE({col}, {default})" in freeze_code
    # A project inheriting an enabled instance default is frozen OFF: never i.enabled, never true.
    assert "enabled = COALESCE(p.enabled, false)" in freeze_instance and "i.enabled" not in freeze_instance
    assert "enabled = COALESCE(enabled, false)" in freeze_code
    assert "true" not in (freeze_instance + freeze_code).lower()
    mod.op.drop_table.assert_called_once_with("instance_retention_policy")
    mod.op.drop_constraint.assert_called_once_with(
        "ck_retention_purge_window", "project_retention_policies", type_="check",
    )
    assert [c.args for c in mod.op.drop_column.call_args_list] == [
        ("project_retention_policies", "purge_paused_until"),
        ("project_retention_policies", "purge_window_start_utc"),
    ]
    restored = {c.args[1]: c.kwargs for c in mod.op.alter_column.call_args_list}
    assert list(restored) == list(_SIX)
    assert all(kw["nullable"] is False for kw in restored.values())
    assert {col: str(kw["server_default"].text) for col, kw in restored.items()} == {
        "enabled": "false", "raw_events_days": "90", "runs_days": "365", "artifacts_days": "180",
        "audit_days": "2555", "keep_last_runs": "10",
    }


# ── the ORM is in step with the DDL ──────────────────────────────────────────

def _check_texts(table) -> dict[str, str]:
    return {
        c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    }


def test_the_orm_matches_0242_policy_columns():
    """NULL = inherit: the six columns lost NOT NULL, ``default`` AND ``server_default`` together."""
    from app.models.postgres import ProjectRetentionPolicy

    policy = ProjectRetentionPolicy.__table__
    for col in _SIX:
        column = policy.c[col]
        assert column.nullable, col
        assert column.server_default is None and column.default is None, col
    assert policy.c.purge_window_start_utc.nullable
    assert policy.c.purge_window_start_utc.type.__class__.__name__ == "SmallInteger"
    assert policy.c.purge_paused_until.nullable and policy.c.purge_paused_until.type.timezone is True
    checks = _check_texts(policy)
    assert checks["ck_retention_purge_window"] == (
        "purge_window_start_utc IS NULL OR purge_window_start_utc BETWEEN 0 AND 23"
    )
    assert checks["ck_retention_keep_last_runs"] == "keep_last_runs >= 0 AND keep_last_runs <= 1000"   # unchanged
    # The AI caps stay nullable without a default (test_migration_0206_retention_ai_caps.py).
    assert policy.c.ai_versions_per_run.server_default is None and policy.c.ai_versions_per_run.nullable


def test_the_orm_matches_0242_instance_table():
    from app.models.postgres import InstanceRetentionPolicy

    table = InstanceRetentionPolicy.__table__
    mod = _load(M0242, "m0242_orm")
    mod.upgrade()
    (create,) = mod.op.create_table.call_args_list
    ddl_columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    assert table.name == create.args[0]
    assert [c.name for c in table.columns] == list(ddl_columns)
    assert {c.name: c.nullable for c in table.columns} == {n: c.nullable for n, c in ddl_columns.items()}
    assert {c.name: c.type.__class__.__name__ for c in table.columns if c.name not in {"enabled_by_id", "updated_by_user_id"}} == {
        n: c.type.__class__.__name__ for n, c in ddl_columns.items() if n not in {"enabled_by_id", "updated_by_user_id"}
    }
    ddl_checks = {c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    assert _check_texts(table) == ddl_checks
    assert table.c.id.primary_key and table.c.id.autoincrement is False
    assert str(table.c.enabled.server_default.arg).lower() == "false" and not table.c.enabled.nullable
    assert table.c.updated_at.server_default is not None
    for fk_column in ("enabled_by_id", "updated_by_user_id"):
        (fk,) = table.c[fk_column].foreign_keys
        assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"


def test_the_orm_matches_0241_export_columns():
    from app.models.postgres import DeletionJob

    table = DeletionJob.__table__
    mod = _load(M0241, "m0241_orm")
    mod.upgrade()
    ddl = {c.args[1].name: c.args[1] for c in mod.op.add_column.call_args_list}
    for name, column in ddl.items():
        orm = table.c[name]
        assert orm.nullable and orm.server_default is None and orm.default is None, name
        assert orm.type.__class__.__name__ == column.type.__class__.__name__ or name == "acknowledged_by_id", name
        assert getattr(orm.type, "length", None) == getattr(column.type, "length", None), name
    (fk,) = table.c.acknowledged_by_id.foreign_keys
    assert fk.target_fullname == "users.id" and fk.ondelete == "SET NULL"
    assert fk.constraint.name == "fk_deletion_jobs_acknowledged_by"
    ddl_checks = {c.args[0]: c.args[2] for c in mod.op.create_check_constraint.call_args_list}
    orm_checks = _check_texts(table)
    assert {name: orm_checks[name] for name in ddl_checks} == ddl_checks
    # No new FK targets test_runs (W22a's derived SET NULL reference list is unchanged).
    assert not any(fk.column.table.name == "test_runs" for fk in table.foreign_keys if fk.parent.name in ddl)
