"""Migrations 0239 / 0240 (W22a ACT-05 / ACT-04): shape only, no database.

* the chain 0238 -> 0239 -> 0240, with 0240 the only head;
* 0239's CHECK literal list is ``retention_vocab.HOLD_ENTITY_TYPES`` (the
  ``test_activity_events.py::test_vocabularies_agree_everywhere`` pattern: a
  migration must not import application code, so the copy is pinned here);
* each downgrade undoes exactly what its upgrade did, in reverse order;
* 0240 builds no index; neither migration imports ``app``;
* the ORM is in step with the DDL (CHECK texts, server default, partial index);
  ``keep_last_runs``' nullability / default is pinned against 0242 by
  ``tests/test_migrations_0241_0242.py`` (W22b made it NULL = inherit).

The real DDL is exercised against Postgres in
``tests/integration/test_migrations_0239_0240_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

from app.services import retention_vocab as vocab

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0239 = VERSIONS / "0239_retention_holds.py"
M0240 = VERSIONS / "0240_retention_floor.py"


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

def test_the_chain_is_linear_with_0240_the_only_head():
    revisions = _revisions()
    assert revisions["0239"] == "0238" and revisions["0240"] == "0239"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0238") == 1 and parents.count("0239") == 1   # no fork at or below
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0240"


def test_neither_migration_imports_the_application():
    for path in (M0239, M0240):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not [m for m in modules if m == "app" or m.startswith("app.")], (path.name, modules)


# ── 0239: the holds table ────────────────────────────────────────────────────

def test_0239_check_literals_are_the_vocabulary():
    mod = _load(M0239, "m0239_vocab")
    assert mod._ENTITY_TYPES == vocab.HOLD_ENTITY_TYPES
    mod.upgrade()
    (create,) = mod.op.create_table.call_args_list
    checks = {
        c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"
    }
    literals = tuple(re.findall(r"'([^']+)'", checks["ck_retention_holds_entity_type"]))
    assert literals == vocab.HOLD_ENTITY_TYPES


def test_0239_upgrade_shape():
    mod = _load(M0239, "m0239_up")
    assert (mod.revision, mod.down_revision) == ("0239", "0238")
    mod.upgrade()
    (create,) = mod.op.create_table.call_args_list
    assert create.args[0] == "retention_holds"
    columns = {c.name: c for c in create.args[1:] if c.__class__.__name__ == "Column"}
    assert list(columns) == [
        "id", "project_id", "entity_type", "entity_id", "entity_label", "reason", "expires_at",
        "placed_by_id", "placed_by_name", "placed_at", "released_at", "released_by_id",
        "released_by_name", "release_reason",
    ]
    assert not columns["project_id"].nullable and not columns["reason"].nullable
    assert columns["entity_id"].nullable and not columns["entity_id"].foreign_keys   # polymorphic: no FK
    fks = {name: next(iter(columns[name].foreign_keys)) for name in ("project_id", "placed_by_id", "released_by_id")}
    assert fks["project_id"].ondelete == "CASCADE" and fks["project_id"].target_fullname == "projects.id"
    assert fks["placed_by_id"].ondelete == "SET NULL" and fks["released_by_id"].ondelete == "SET NULL"
    checks = {c.name for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"}
    assert checks == {
        "ck_retention_holds_entity_type", "ck_retention_holds_entity_id",
        "ck_retention_holds_reason", "ck_retention_holds_release_reason",
    }
    indexes = [(c.args[0], c.args[1], c.args[2], c.kwargs) for c in mod.op.create_index.call_args_list]
    assert [i[:3] for i in indexes] == [
        ("ix_retention_holds_live", "retention_holds", ["project_id", "entity_type", "entity_id"]),
        ("ix_retention_holds_project_placed", "retention_holds", ["project_id", "placed_at"]),
    ]
    assert str(indexes[0][3]["postgresql_where"]) == "released_at IS NULL"


def test_0239_downgrade_undoes_the_upgrade_in_reverse():
    mod = _load(M0239, "m0239_down")
    mod.downgrade()
    assert [c.args[0] for c in mod.op.drop_index.call_args_list] == [
        "ix_retention_holds_project_placed", "ix_retention_holds_live",
    ]
    assert all(c.kwargs["table_name"] == "retention_holds" for c in mod.op.drop_index.call_args_list)
    mod.op.drop_table.assert_called_once_with("retention_holds")
    names = [c[0] for c in mod.op.method_calls]
    assert names == ["drop_index", "drop_index", "drop_table"]


# ── 0240: the floor and the purged-baseline stamp ────────────────────────────

def test_0240_upgrade_shape_and_no_index():
    mod = _load(M0240, "m0240_up")
    assert (mod.revision, mod.down_revision) == ("0240", "0239")
    mod.upgrade()
    assert [c[0] for c in mod.op.method_calls] == ["add_column", "create_check_constraint", "add_column"]
    keep, stamp = mod.op.add_column.call_args_list
    assert keep.args[0] == "project_retention_policies"
    column = keep.args[1]
    assert column.name == "keep_last_runs" and not column.nullable
    assert str(column.server_default.arg) == "10"
    assert stamp.args[0] == "run_baselines"
    assert stamp.args[1].name == "baseline_purged_at" and stamp.args[1].nullable
    assert stamp.args[1].server_default is None
    mod.op.create_check_constraint.assert_called_once_with(
        "ck_retention_keep_last_runs", "project_retention_policies",
        "keep_last_runs >= 0 AND keep_last_runs <= 1000",
    )
    mod.op.create_index.assert_not_called()
    source = M0240.read_text(encoding="utf-8")
    assert "create_index" not in source and "CREATE INDEX" not in source.upper()


def test_0240_downgrade_undoes_the_upgrade_in_reverse():
    mod = _load(M0240, "m0240_down")
    mod.downgrade()
    assert mod.op.method_calls[0][0] == "drop_column"
    assert [c.args for c in mod.op.drop_column.call_args_list] == [
        ("run_baselines", "baseline_purged_at"),
        ("project_retention_policies", "keep_last_runs"),
    ]
    mod.op.drop_constraint.assert_called_once_with(
        "ck_retention_keep_last_runs", "project_retention_policies", type_="check",
    )
    assert [c[0] for c in mod.op.method_calls] == ["drop_column", "drop_constraint", "drop_column"]


# ── the ORM is in step with the DDL ──────────────────────────────────────────

def _check_texts(table) -> dict[str, str]:
    return {
        c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    }


def test_the_orm_matches_0239():
    from app.models.postgres import RetentionHold

    table = RetentionHold.__table__
    mod = _load(M0239, "m0239_orm")
    mod.upgrade()
    (create,) = mod.op.create_table.call_args_list
    ddl_checks = {
        c.name: str(c.sqltext) for c in create.args[1:] if c.__class__.__name__ == "CheckConstraint"
    }
    assert _check_texts(table) == ddl_checks
    ddl_columns = [c.name for c in create.args[1:] if c.__class__.__name__ == "Column"]
    assert [c.name for c in table.columns] == ddl_columns
    nullable = {c.name: c.nullable for c in create.args[1:] if c.__class__.__name__ == "Column"}
    assert {c.name: c.nullable for c in table.columns if c.name != "id"} == {
        k: v for k, v in nullable.items() if k != "id"
    }
    assert not table.c.entity_id.foreign_keys
    indexes = {ix.name: ix for ix in table.indexes}
    assert set(indexes) == {"ix_retention_holds_live", "ix_retention_holds_project_placed"}
    live = indexes["ix_retention_holds_live"]
    assert [c.name for c in live.columns] == ["project_id", "entity_type", "entity_id"]
    assert str(live.dialect_options["postgresql"]["where"]) == "released_at IS NULL"
    assert table.c.placed_at.server_default is not None


def test_the_orm_matches_0240():
    from app.models.postgres import ProjectRetentionPolicy, RunBaseline

    policy = ProjectRetentionPolicy.__table__
    # 0240's DDL (NOT NULL, DEFAULT 10) is pinned above; the ORM column became
    # nullable with no default in 0242 (W22b: NULL = inherit, per field), and
    # tests/test_migrations_0241_0242.py pins the ORM against 0242 now.
    assert _check_texts(policy)["ck_retention_keep_last_runs"] == "keep_last_runs >= 0 AND keep_last_runs <= 1000"
    baselines = RunBaseline.__table__
    assert baselines.c.baseline_purged_at.nullable and baselines.c.baseline_purged_at.server_default is None
    assert baselines.c.baseline_purged_at.type.timezone is True
