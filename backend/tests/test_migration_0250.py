"""Migration 0250 (W25d HYG-02): drop the four dead tables -- shape only, no database.

* the chain 0249 -> 0250 -> 0251, with ONE head above it;
* the file imports no application code;
* the upgrade PROBES every table of ``DEAD_TABLES`` (``to_regclass``, then a
  ``count(*)`` of the ones that exist) BEFORE the first ``drop_table``;
* any non-empty table -> ``RuntimeError`` naming EVERY non-empty table with its
  count ("row" for 1) and NOTHING dropped; an absent table is neither counted
  nor dropped; offline (``--sql``) mode skips the probe and drops the four;
* the drop order is ``DEAD_TABLES``'s and no other operation runs;
* the downgrade recreates the four tables -- four ``create_table`` and seven
  ``create_index`` -- with the 0249 column order, nullability, server defaults,
  primary-key and foreign-key names and ``ondelete`` rules (``run_id`` SET NULL
  on ``ai_provenance_records``), copied from 0001 / 0006 / 0026 + 0113 / 0037 /
  0082 rather than the ORM;
* the ORM maps none of the four tables any more.

The reflected shapes of the tables the downgrade creates, compared with a
snapshot captured from a database at 0249, and the refusal against real rows,
run in ``tests/integration/test_migration_0250_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0250 = VERSIONS / "0250_drop_dead_tables.py"
DEAD_TABLES = ("contract_violations", "ai_provenance_records", "tenant_metric_snapshots", "quality_gates")


class _FakeOp:
    """Records every probe and every operation on ONE ordered event list.

    ``existing`` answers ``to_regclass``; ``counts`` answers ``count(*)`` per table (0 by default). Any
    ``op.<name>(...)`` call is recorded as ``(<name>, args, kwargs)`` instead of raising, so "no other
    operation" is an assertion on the list.
    """

    def __init__(self, *, existing=DEAD_TABLES, counts=None, as_sql: bool = False) -> None:
        self.events: list[tuple] = []
        self._existing = set(existing)
        self._counts = dict(counts or {})
        outer = self

        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar(self):
                return self._value

        class _Bind:
            def execute(self, statement, params=None):
                sql = " ".join(str(statement).split())
                outer.events.append(("probe", sql, dict(params or {})))
                if sql.startswith("SELECT to_regclass("):
                    return _Result((params or {}).get("name") in outer._existing)
                assert sql.startswith("SELECT count(*) FROM "), sql
                table = sql.rsplit(" ", 1)[1]
                assert table in outer._existing, f"counted an absent table: {table}"
                return _Result(outer._counts.get(table, 0))

        class _Context:
            def __init__(self) -> None:
                self.as_sql = as_sql

        self._bind = _Bind()
        self._context = _Context()

    def get_bind(self):
        return self._bind

    def get_context(self):
        return self._context

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _record(*args, **kwargs):
            self.events.append((name, args, kwargs))

        return _record

    # ── views ──
    def kinds(self) -> list[str]:
        return [event[0] for event in self.events]

    def dropped(self) -> list[str]:
        return [event[1][0] for event in self.events if event[0] == "drop_table"]

    def probes(self) -> list[tuple[str, dict]]:
        return [(event[1], event[2]) for event in self.events if event[0] == "probe"]


def _load(op: _FakeOp, name: str):
    spec = importlib.util.spec_from_file_location(name, M0250)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = op
    return module


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


# ── the chain and the hygiene ────────────────────────────────────────────────


def test_the_chain_is_linear_with_0250_on_0249_and_one_head_above_it():
    revisions = _revisions()
    assert revisions["0250"] == "0249"
    assert revisions.get("0251") == "0250", "0251 (the cutover flag) chains onto 0250"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0249") == 1 and parents.count("0250") == 1  # no fork
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0251"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0250.read_text(encoding="utf-8"))
    modules = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_the_dead_tables_are_the_contract_names_in_probe_and_drop_order():
    module = _load(_FakeOp(), "m0250_names")
    assert module.DEAD_TABLES == DEAD_TABLES
    assert module.revision == "0250" and module.down_revision == "0249"


# ── the upgrade: probe first, refuse naming every non-empty table ────────────


def test_empty_tables_are_all_probed_before_the_first_drop_then_dropped_in_order():
    op = _FakeOp()
    _load(op, "m0250_empty").upgrade()
    kinds = op.kinds()
    assert kinds == ["probe"] * 8 + ["drop_table"] * 4, op.events
    assert op.probes() == [
        *[("SELECT to_regclass(:name) IS NOT NULL", {"name": name}) for name in DEAD_TABLES],
        *[(f"SELECT count(*) FROM {name}", {}) for name in DEAD_TABLES],
    ]
    assert op.dropped() == list(DEAD_TABLES)
    # Plain drops: no cascade, no extra keyword (nothing references these tables).
    assert all(event[2] == {} and len(event[1]) == 1 for event in op.events if event[0] == "drop_table")


@pytest.mark.parametrize("table", DEAD_TABLES)
def test_one_non_empty_table_refuses_naming_it_and_drops_nothing(table):
    op = _FakeOp(counts={table: 1})
    with pytest.raises(RuntimeError) as raised:
        _load(op, f"m0250_one_{table}").upgrade()
    assert str(raised.value) == (
        f"0250_drop_dead_tables refused: {table} has 1 row — nothing was dropped. "
        "Export or delete those rows deliberately, then upgrade again."
    )
    assert op.dropped() == [], "the refusal comes BEFORE the first DROP"
    assert set(op.kinds()) == {"probe"}
    # Every table was counted, so the message can name every non-empty one.
    assert [sql for sql, _ in op.probes() if sql.startswith("SELECT count")] == [
        f"SELECT count(*) FROM {name}" for name in DEAD_TABLES
    ]


def test_two_non_empty_tables_are_both_named_with_their_counts():
    op = _FakeOp(counts={"quality_gates": 3, "ai_provenance_records": 1})
    with pytest.raises(RuntimeError) as raised:
        _load(op, "m0250_two").upgrade()
    assert str(raised.value) == (
        "0250_drop_dead_tables refused: ai_provenance_records has 1 row, quality_gates has 3 rows "
        "— nothing was dropped. Export or delete those rows deliberately, then upgrade again."
    )
    assert op.dropped() == []


def test_every_non_empty_table_is_named():
    op = _FakeOp(counts={name: index + 2 for index, name in enumerate(DEAD_TABLES)})
    with pytest.raises(RuntimeError) as raised:
        _load(op, "m0250_all").upgrade()
    message = str(raised.value)
    for index, name in enumerate(DEAD_TABLES):
        assert f"{name} has {index + 2} rows" in message
    assert op.dropped() == []


def test_an_absent_table_is_neither_counted_nor_dropped():
    present = ("contract_violations", "tenant_metric_snapshots")
    op = _FakeOp(existing=present)
    _load(op, "m0250_absent").upgrade()
    counted = [sql.rsplit(" ", 1)[1] for sql, _ in op.probes() if sql.startswith("SELECT count")]
    assert counted == list(present)
    assert op.dropped() == list(present)


def test_an_absent_table_does_not_hide_a_non_empty_one():
    op = _FakeOp(existing=("quality_gates",), counts={"quality_gates": 2})
    with pytest.raises(RuntimeError, match=r"refused: quality_gates has 2 rows — nothing was dropped"):
        _load(op, "m0250_absent_full").upgrade()
    assert op.dropped() == []


def test_offline_sql_mode_skips_the_probe_and_drops_the_four():
    op = _FakeOp(as_sql=True)
    _load(op, "m0250_offline").upgrade()
    assert op.probes() == []
    assert op.kinds() == ["drop_table"] * 4
    assert op.dropped() == list(DEAD_TABLES)


# ── the downgrade: the 0249 shapes, from the original DDL ────────────────────


def _created(op: _FakeOp) -> dict[str, sa.Table]:
    """Each ``create_table`` call rebuilt as a Table on its own MetaData (what alembic itself does)."""
    tables: dict[str, sa.Table] = {}
    metadata = sa.MetaData()
    for name in ("projects", "test_runs", "test_cases"):
        sa.Table(name, metadata, sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True))
    for kind, args, kwargs in op.events:
        if kind == "create_table":
            assert kwargs == {}, kwargs
            tables[args[0]] = sa.Table(args[0], metadata, *args[1:])
    return tables


def _server_default(column: sa.Column) -> str | None:
    default = column.server_default
    if default is None:
        return None
    arg = default.arg  # type: ignore[attr-defined]
    return arg if isinstance(arg, str) else str(arg.compile(dialect=postgresql.dialect()))


#: table -> [(column, type compiled for PostgreSQL, nullable, server default as written)], in 0249 order.
EXPECTED_COLUMNS = {
    "quality_gates": [
        ("id", "UUID", False, "gen_random_uuid()"),
        ("project_id", "UUID", True, None),
        ("name", "VARCHAR(255)", False, None),
        ("rules", "JSONB", True, "[]"),
        ("is_active", "BOOLEAN", True, "true"),
        ("created_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
    ],
    "tenant_metric_snapshots": [
        ("id", "UUID", False, None),
        ("project_id", "UUID", False, None),
        ("recorded_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
        ("total_runs", "INTEGER", True, "0"),
        ("total_tests", "INTEGER", True, "0"),
        ("avg_pass_rate", "FLOAT", True, None),
        ("failed_runs", "INTEGER", True, "0"),
        ("ai_analyses_count", "INTEGER", True, "0"),
        ("release_decisions_count", "INTEGER", True, "0"),
        ("audit_events_count", "INTEGER", True, "0"),
    ],
    "ai_provenance_records": [
        ("id", "UUID", False, None),
        ("entity_type", "VARCHAR(50)", False, None),
        ("entity_id", "UUID", False, None),
        ("run_id", "UUID", True, None),
        ("model_name", "VARCHAR(200)", True, None),
        ("fallback_used", "BOOLEAN", False, "false"),
        ("confidence", "INTEGER", True, None),
        ("confidence_reason", "TEXT", True, None),
        ("evidence_count", "INTEGER", True, "0"),
        ("sources_used", "JSON", True, None),
        ("deterministic_checks_used", "JSON", True, None),
        ("created_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
        ("project_id", "UUID", True, None),  # 0113 appended it: the LAST column
    ],
    "contract_violations": [
        ("id", "UUID", False, "gen_random_uuid()"),
        ("test_run_id", "UUID", False, None),
        ("test_case_id", "UUID", False, None),
        ("endpoint", "VARCHAR(500)", True, None),
        ("violation_type", "VARCHAR(50)", False, None),
        ("field_path", "VARCHAR(500)", True, None),
        ("expected", "VARCHAR(500)", True, None),
        ("actual", "VARCHAR(500)", True, None),
        ("severity", "VARCHAR(20)", False, "'warning'"),  # 0006's literal, quotes included
        ("created_at", "TIMESTAMP WITH TIME ZONE", True, "now()"),
    ],
}
#: table -> {(fk name, column, target table, ondelete)}
EXPECTED_FKS = {
    "quality_gates": {("quality_gates_project_id_fkey", "project_id", "projects", "CASCADE")},
    "tenant_metric_snapshots": {("tenant_metric_snapshots_project_id_fkey", "project_id", "projects", "CASCADE")},
    "ai_provenance_records": {
        ("ai_provenance_records_run_id_fkey", "run_id", "test_runs", "SET NULL"),
        ("ai_provenance_records_project_id_fkey", "project_id", "projects", "CASCADE"),
    },
    "contract_violations": {
        ("contract_violations_test_run_id_fkey", "test_run_id", "test_runs", "CASCADE"),
        ("contract_violations_test_case_id_fkey", "test_case_id", "test_cases", "CASCADE"),
    },
}
EXPECTED_INDEXES = [
    ("ix_quality_gates_project", "quality_gates", ["project_id"]),
    ("ix_tms_project_time", "tenant_metric_snapshots", ["project_id", "recorded_at"]),
    ("ix_provenance_entity", "ai_provenance_records", ["entity_type", "entity_id"]),
    ("ix_provenance_run", "ai_provenance_records", ["run_id"]),
    ("ix_provenance_project", "ai_provenance_records", ["project_id"]),
    ("ix_contract_violations_run", "contract_violations", ["test_run_id"]),
    ("ix_contract_violations_tc", "contract_violations", ["test_case_id"]),
]


def test_the_downgrade_is_four_create_tables_and_seven_create_indexes_and_reads_nothing():
    op = _FakeOp()
    _load(op, "m0250_down_ops").downgrade()
    assert op.probes() == [], "the downgrade reads nothing"
    assert op.kinds() == [
        "create_table", "create_index",
        "create_table", "create_index",
        "create_table", "create_index", "create_index", "create_index",
        "create_table", "create_index", "create_index",
    ], op.kinds()
    created = [event[1][0] for event in op.events if event[0] == "create_table"]
    assert created == list(reversed(DEAD_TABLES)), "recreated in reverse drop order"
    indexes = [(args[0], args[1], list(args[2]), kwargs) for kind, args, kwargs in op.events if kind == "create_index"]
    # Plain builds: each table is new in this migration (the concurrency guard's own exemption).
    assert indexes == [(name, table, cols, {}) for name, table, cols in EXPECTED_INDEXES]


@pytest.mark.parametrize("table", list(reversed(DEAD_TABLES)))
def test_each_recreated_table_has_its_0249_columns_in_order(table):
    op = _FakeOp()
    _load(op, f"m0250_down_cols_{table}").downgrade()
    built = _created(op)[table]
    got = [
        (c.name, c.type.compile(dialect=postgresql.dialect()), c.nullable, _server_default(c))
        for c in built.columns
    ]
    assert got == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize("table", list(reversed(DEAD_TABLES)))
def test_each_recreated_table_has_its_named_primary_key_and_foreign_keys(table):
    op = _FakeOp()
    _load(op, f"m0250_down_keys_{table}").downgrade()
    built = _created(op)[table]
    assert built.primary_key.name == f"{table}_pkey"
    assert [c.name for c in built.primary_key.columns] == ["id"]
    fks = {
        (fk.constraint.name, fk.parent.name, fk.column.table.name, fk.ondelete)
        for fk in built.foreign_keys
    }
    assert fks == EXPECTED_FKS[table]
    assert not [c for c in built.constraints if isinstance(c, sa.UniqueConstraint)], "no unique constraint at 0249"


def test_the_provenance_run_pointer_is_set_null_as_0113_left_it():
    op = _FakeOp()
    _load(op, "m0250_down_provenance").downgrade()
    built = _created(op)["ai_provenance_records"]
    (run_fk,) = built.c.run_id.foreign_keys
    assert run_fk.ondelete == "SET NULL"
    (project_fk,) = built.c.project_id.foreign_keys
    assert project_fk.ondelete == "CASCADE"


# ── the ORM ─────────────────────────────────────────────────────────────────


def test_the_orm_maps_none_of_the_dropped_tables():
    from app.models import postgres as models

    assert not set(DEAD_TABLES) & set(models.Base.metadata.tables)
    # No mapped class is left over any of the four (a class there would put its table in the metadata).
    assert not [m.class_.__name__ for m in models.Base.registry.mappers if m.local_table.name in DEAD_TABLES]
    assert "quality_gates" not in models.Project.__mapper__.relationships
    # The deep investigation's contract findings are a JSON COLUMN of deep_findings -- it stays.
    assert "contract_violations" in models.DeepFinding.__table__.c
