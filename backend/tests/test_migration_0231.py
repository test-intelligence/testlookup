"""Migration 0231 (W16 AGT-09): shape only, no database.

``ai_eval_runs`` grows the replay columns (``metric_kind`` / ``status`` with
constant defaults, so every legacy row reads ``label_agreement`` /
``completed``), two named foreign keys and two checks; ``ai_eval_baselines``
grows ``metric_kind`` and its check; NEW ``ai_eval_item_outputs`` with its
unique key, its status index and two checks.

Pinned here: the revision and docstring; the chain 0230 -> 0231, linear, 0231
the only head (every earlier migration test asserts ``heads[0] >= …`` and keeps
passing); no application import; the concurrent-index guard finds only the new
table's own index (nothing is built on an existing table); the upgrade's
operations in order; the downgrade is their exact inverse; the ORM declares the
same names, types, nullability, defaults, keys, index and checks. The
PostgreSQL behaviour runs in ``tests/integration/test_migration_0231_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import sqlalchemy as sa

from tests.regression.test_migration_index_builds_are_concurrent import (
    _blocking,
    _concurrent_outside_autocommit,
    _created_tables,
    _operations,
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0231 = VERSIONS / "0231_ai_eval_candidate_runs.py"
RUNS = "ai_eval_runs"
BASELINES = "ai_eval_baselines"
ITEMS = "ai_eval_item_outputs"

RUN_COLUMNS = [
    "metric_kind", "status", "candidate", "project_id", "requested_by", "started_at", "completed_at",
    "heartbeat_at", "scored_items", "unscored_items", "error_items", "items_capped", "error_code", "metrics",
]
ITEM_COLUMNS = [
    "id", "eval_run_id", "item_index", "item_key", "status", "error_code", "expected", "output", "score",
    "latency_ms", "tokens", "mode_used", "created_at",
]
RUN_CHECKS = {
    "ck_aer_metric_kind": "metric_kind IN ('label_agreement','replay')",
    "ck_aer_status": "status IN ('queued','running','completed','partial','failed')",
}
BASELINE_CHECKS = {"ck_aeb_metric_kind": "metric_kind IN ('label_agreement','replay')"}
ITEM_CHECKS = {
    "ck_aeio_status": "status IN ('correct','incorrect','unscored','error')",
    "ck_aeio_item_index_nonnegative": "item_index >= 0",
}
RUN_FKS = {
    "fk_aer_project_id": ("projects", ["project_id"], ["id"]),
    "fk_aer_requested_by": ("users", ["requested_by"], ["id"]),
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(step: str) -> MagicMock:
    module = _load(M0231, f"m0231_{step}")
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


def _calls(op: MagicMock, name: str) -> list:
    return [c for c in op.method_calls if c[0] == name]


def _added(op: MagicMock, table: str) -> dict[str, sa.Column]:
    return {c.args[1].name: c.args[1] for c in _calls(op, "add_column") if c.args[0] == table}


def _created(op: MagicMock):
    (call,) = _calls(op, "create_table")
    items = call.args[1:]
    columns = {c.name: c for c in items if isinstance(c, sa.Column)}
    checks = {c.name: c for c in items if isinstance(c, sa.CheckConstraint)}
    uniques = [c for c in items if isinstance(c, sa.UniqueConstraint)]
    return call.args[0], columns, checks, uniques


def test_the_revision_and_its_docstring():
    module = _load(M0231, "m0231")
    assert (module.revision, module.down_revision) == ("0231", "0230")
    assert "Revision ID: 0231" in module.__doc__ and "Revises: 0230" in module.__doc__
    # What each object is for is written where an operator reads it.
    for name in (*RUN_CHECKS, *BASELINE_CHECKS, *ITEM_CHECKS, *RUN_FKS, ITEMS, "uq_aeio_run_item", "ix_aeio_run_status"):
        assert name in module.__doc__, name
    assert "never" in module.__doc__ and "recomputed" in module.__doc__


def test_the_chain_is_linear_with_0231_the_only_head():
    revisions = _revisions()
    assert revisions["0231"] == "0230"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0230") == 1, "nothing else forks off 0230"
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0231"


def test_it_imports_no_application_code():
    imported = _imports(M0231)
    assert not any(name.startswith("app") for name in imported), imported


def test_the_only_index_built_is_on_the_table_it_creates():
    source = M0231.read_text(encoding="utf-8")
    assert _created_tables(source) == {ITEMS}
    assert {(o.kind, o.table) for o in _operations(source)} == {("index", ITEMS)}
    assert _blocking(source) == [], "no index on an existing table is built inside the transaction"
    assert _concurrent_outside_autocommit(source) == []


def test_the_upgrade_operations_in_order():
    op = _run("upgrade")
    assert [c[0] for c in op.method_calls] == (
        ["add_column"] * len(RUN_COLUMNS)
        + ["create_foreign_key"] * 2
        + ["create_check_constraint"] * 2
        + ["add_column", "create_check_constraint", "create_table", "create_index"]
    )


def test_the_run_columns_types_nullability_and_constant_defaults():
    op = _run("upgrade")
    columns = _added(op, RUNS)
    assert list(columns) == RUN_COLUMNS
    assert {n for n, c in columns.items() if not c.nullable} == {
        "metric_kind", "status", "scored_items", "unscored_items", "error_items", "items_capped",
    }
    defaults = {n: str(c.server_default.arg) for n, c in columns.items() if c.server_default is not None}
    assert defaults == {
        "metric_kind": "'label_agreement'", "status": "'completed'", "scored_items": "0",
        "unscored_items": "0", "error_items": "0", "items_capped": "false",
    }
    lengths = {n: c.type.length for n, c in columns.items() if isinstance(c.type, sa.String)}
    assert lengths == {"metric_kind": 20, "status": 20, "error_code": 100}
    assert {n for n, c in columns.items() if isinstance(c.type, sa.JSON)} == {"candidate", "metrics"}
    assert {n for n, c in columns.items() if isinstance(c.type, sa.Uuid)} == {"project_id", "requested_by"}
    stamps = {n for n, c in columns.items() if isinstance(c.type, sa.DateTime)}
    assert stamps == {"started_at", "completed_at", "heartbeat_at"}
    assert all(columns[n].type.timezone for n in stamps)
    # The foreign keys are created by name (the downgrade drops them by name), never inline.
    assert not any(c.foreign_keys or c.index or c.unique for c in columns.values())
    fks = {c.args[0]: (c.args[2], c.args[3], c.args[4], c.kwargs) for c in _calls(op, "create_foreign_key")}
    assert fks == {name: (*spec, {"ondelete": "SET NULL"}) for name, spec in RUN_FKS.items()}
    assert all(c.args[1] == RUNS for c in _calls(op, "create_foreign_key"))


def test_the_checks_and_the_baseline_column():
    op = _run("upgrade")
    checks = {c.args[0]: (c.args[1], _sql(c.args[2])) for c in _calls(op, "create_check_constraint")}
    assert checks == {
        **{n: (RUNS, sql) for n, sql in RUN_CHECKS.items()},
        **{n: (BASELINES, sql) for n, sql in BASELINE_CHECKS.items()},
    }
    (column,) = _added(op, BASELINES).values()
    assert column.name == "metric_kind" and column.nullable is False and column.type.length == 20
    assert str(column.server_default.arg) == "'label_agreement'"


def test_the_item_outputs_table_its_key_index_and_checks():
    op = _run("upgrade")
    name, columns, checks, uniques = _created(op)
    assert name == ITEMS
    assert list(columns) == ITEM_COLUMNS
    assert columns["id"].primary_key
    (fk,) = columns["eval_run_id"].foreign_keys
    assert (fk.target_fullname, fk.ondelete) == ("ai_eval_runs.id", "CASCADE")
    assert not any(c.foreign_keys for n, c in columns.items() if n != "eval_run_id")
    assert {n for n, c in columns.items() if c.nullable} == {
        "item_key", "error_code", "output", "latency_ms", "tokens", "mode_used",
    }
    lengths = {n: c.type.length for n, c in columns.items() if isinstance(c.type, sa.String)}
    assert lengths == {"item_key": 160, "status": 20, "error_code": 100, "mode_used": 20}
    assert {n for n, c in columns.items() if isinstance(c.type, sa.JSON)} == {"expected", "output", "score"}
    assert str(columns["score"].server_default.arg) == "'{}'::jsonb"
    assert str(columns["created_at"].server_default.arg) == "now()" and columns["created_at"].type.timezone
    (unique,) = uniques
    assert unique.name == "uq_aeio_run_item"
    assert [str(c) for c in unique._pending_colargs] == ["eval_run_id", "item_index"]
    assert {n: _sql(c.sqltext) for n, c in checks.items()} == ITEM_CHECKS
    (index,) = _calls(op, "create_index")
    assert index.args == ("ix_aeio_run_status", ITEMS, ["eval_run_id", "status"]) and not index.kwargs


def test_the_downgrade_is_the_exact_inverse():
    op = _run("downgrade")
    assert [(c[0], c.args, c.kwargs) for c in op.method_calls] == [
        ("drop_table", (ITEMS,), {}),
        ("drop_constraint", ("ck_aeb_metric_kind", BASELINES), {"type_": "check"}),
        ("drop_column", (BASELINES, "metric_kind"), {}),
        ("drop_constraint", ("ck_aer_status", RUNS), {"type_": "check"}),
        ("drop_constraint", ("ck_aer_metric_kind", RUNS), {"type_": "check"}),
        ("drop_constraint", ("fk_aer_requested_by", RUNS), {"type_": "foreignkey"}),
        ("drop_constraint", ("fk_aer_project_id", RUNS), {"type_": "foreignkey"}),
        *[("drop_column", (RUNS, name), {}) for name in reversed(RUN_COLUMNS)],
    ]


def _assert_same_column(declared: sa.Column, migrated: sa.Column) -> None:
    name = migrated.name
    assert declared.nullable is migrated.nullable, name
    assert type(declared.type)._type_affinity is type(migrated.type)._type_affinity, name
    if getattr(migrated.type, "length", None):
        assert declared.type.length == migrated.type.length, name
    if isinstance(migrated.type, sa.DateTime):
        assert declared.type.timezone is migrated.type.timezone is True, name
    # Same presence of a server default; the constant ones read the same text.
    assert (declared.server_default is None) is (migrated.server_default is None), name
    if migrated.server_default is not None and name != "created_at":
        assert str(declared.server_default.arg) == str(migrated.server_default.arg), name


def _declared_checks(table: sa.Table) -> dict[str, str]:
    return {c.name: _sql(c.sqltext) for c in table.constraints if isinstance(c, sa.CheckConstraint)}


def test_the_orm_run_and_baseline_declare_the_same_columns_keys_and_checks():
    from app.models.postgres import AIEvalBaseline, AIEvalRun

    op = _run("upgrade")
    runs = AIEvalRun.__table__
    assert [c.name for c in runs.columns][-len(RUN_COLUMNS):] == RUN_COLUMNS, "added at the end, as in the DB"
    for name, column in _added(op, RUNS).items():
        _assert_same_column(runs.c[name], column)
    declared_fks = {
        fk.name: (fk.column.table.name, [fk.parent.name], [fk.column.name], fk.ondelete)
        for fk in runs.foreign_keys
        if fk.name
    }
    assert declared_fks == {n: (*spec, "SET NULL") for n, spec in RUN_FKS.items()}
    assert {n: s for n, s in _declared_checks(runs).items()} == RUN_CHECKS
    # Python-side defaults too: a row filled at flush, before the server default would.
    python_defaults = {n: runs.c[n].default.arg for n in RUN_COLUMNS if runs.c[n].default is not None}
    assert python_defaults == {
        "metric_kind": "label_agreement", "status": "completed", "scored_items": 0,
        "unscored_items": 0, "error_items": 0, "items_capped": False,
    }
    assert not any(runs.c[n].onupdate for n in RUN_COLUMNS), "no onupdate column (MissingGreenlet after a flush)"
    assert {i.name for i in runs.indexes} == {"ix_aer_dataset", "ix_aer_time"}, "no new index on the existing table"

    baselines = AIEvalBaseline.__table__
    (column,) = _added(op, BASELINES).values()
    assert [c.name for c in baselines.columns][-1] == "metric_kind"
    _assert_same_column(baselines.c["metric_kind"], column)
    assert baselines.c["metric_kind"].default.arg == "label_agreement"
    assert _declared_checks(baselines) == BASELINE_CHECKS


def test_the_orm_item_output_declares_the_same_table():
    from app.models.postgres import AIEvalItemOutput, AIEvalRun

    name, columns, checks, uniques = _created(_run("upgrade"))
    table = AIEvalItemOutput.__table__
    assert table.name == name == ITEMS
    assert [c.name for c in table.columns] == ITEM_COLUMNS
    for column in columns.values():
        _assert_same_column(table.c[column.name], column)
    assert [c.name for c in table.primary_key.columns] == ["id"]
    assert {(fk.target_fullname, fk.ondelete) for fk in table.foreign_keys} == {("ai_eval_runs.id", "CASCADE")}
    assert _declared_checks(table) == {n: _sql(c.sqltext) for n, c in checks.items()}
    declared_uniques = [c for c in table.constraints if isinstance(c, sa.UniqueConstraint)]
    assert [(u.name, [c.name for c in u.columns]) for u in declared_uniques] == [
        ("uq_aeio_run_item", ["eval_run_id", "item_index"]),
    ]
    assert [(i.name, [c.name for c in i.columns]) for i in table.indexes] == [
        ("ix_aeio_run_status", ["eval_run_id", "status"]),
    ]
    # Declared right after AIEvalRun; a plain row (no relationship attributes).
    from app.models import postgres as models

    names = [n for n, v in vars(models).items() if isinstance(v, type) and hasattr(v, "__tablename__")]
    assert names.index("AIEvalItemOutput") == names.index(AIEvalRun.__name__) + 1
    assert not AIEvalItemOutput.__mapper__.relationships
