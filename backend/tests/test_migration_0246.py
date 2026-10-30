"""Migration 0246 (W23c AGT-11): shape only, no database.

* the chain 0245 -> 0246, with 0246 the only head (every earlier migration
  test asserts ``heads[0] >= …`` and keeps passing);
* the upgrade adds ``agent_invocations.input_payload`` (JSONB) and
  ``input_sha256`` (VARCHAR(64)), both nullable with no default (catalogue
  changes), then the two CHECKs, each added ``NOT VALID`` and then
  ``VALIDATE``d (the 0146 pattern); no index, no backfill;
* the downgrade is the exact inverse, in reverse order;
* the CHECK texts are the ORM ``CheckConstraint`` texts; the ORM columns match
  the DDL; the regex CHECK renders on PostgreSQL only, so a SQLite build of the
  table (JSONB compiled as JSON) still works with the pair CHECK enforced;
* the file imports no application code and never names the ``review_gate``
  setting (AGT-10's date is unset on upgrade — D17).

The real DDL (types, ``convalidated``, refused inserts, a downgrade round trip)
runs against Postgres in ``tests/integration/test_migration_0246_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.schema import CreateTable

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0246 = VERSIONS / "0246_agent_invocation_inline_input.py"
TABLE = "agent_invocations"
PAIR = "ck_agent_invocations_input_pair"
SHA = "ck_agent_invocations_input_sha256"
PAIR_SQL = "(input_payload IS NULL) = (input_sha256 IS NULL)"
SHA_SQL = "input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'"


class _FakeOp:
    """Records every operation in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def add_column(self, table, column):
        self.events.append(("add_column", (table, column)))

    def drop_column(self, table, column):
        self.events.append(("drop_column", (table, column)))

    def drop_constraint(self, name, table, type_=None):
        self.events.append(("drop_constraint", (name, table, type_)))

    def execute(self, sql):
        self.events.append(("execute", str(sql)))

    def __getattr__(self, name):  # any other op (create_index, …) is a failure of the contract
        raise AssertionError(f"0246 must not call op.{name}")


def _load(op: _FakeOp, name: str):
    spec = importlib.util.spec_from_file_location(name, M0246)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.op = op
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


def _squash(sql: str) -> str:
    return " ".join(sql.split())


# ── the chain and the hygiene ────────────────────────────────────────────────

def test_the_chain_is_linear_with_0246_the_only_head():
    revisions = _revisions()
    assert revisions["0246"] == "0245"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0245") == 1   # no fork at 0245
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0246"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0246.read_text(encoding="utf-8"))
    modules = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


def test_the_migration_never_names_the_review_gate_setting():
    # D17: the enforcement date is UNSET on upgrade — no migration inserts the app_settings key.
    text = M0246.read_text(encoding="utf-8")
    assert "review_gate" not in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]   # past the module docstring
    assert "app_settings" not in body
    assert not re.search(r"\bINSERT\b|\bUPDATE\b", body, re.IGNORECASE)


# ── the upgrade ──────────────────────────────────────────────────────────────

_UPGRADE_SQL = [
    f"ALTER TABLE agent_invocations ADD CONSTRAINT {PAIR} CHECK ({PAIR_SQL}) NOT VALID",
    f"ALTER TABLE agent_invocations VALIDATE CONSTRAINT {PAIR}",
    f"ALTER TABLE agent_invocations ADD CONSTRAINT {SHA} CHECK ({SHA_SQL}) NOT VALID",
    f"ALTER TABLE agent_invocations VALIDATE CONSTRAINT {SHA}",
]


def test_upgrade_adds_two_nullable_columns_without_defaults_then_the_checks_not_valid_then_validated():
    op = _FakeOp()
    _load(op, "m0246_up").upgrade()
    assert [kind for kind, _ in op.events] == ["add_column", "add_column", "execute", "execute", "execute", "execute"]

    (table, payload), (table2, sha) = op.events[0][1], op.events[1][1]
    assert table == table2 == TABLE
    assert payload.name == "input_payload" and isinstance(payload.type, postgresql.JSONB)
    assert sha.name == "input_sha256" and isinstance(sha.type, sa.String) and sha.type.length == 64
    for column in (payload, sha):
        assert column.nullable is True
        assert column.server_default is None and column.default is None

    assert [_squash(sql) for _, sql in op.events[2:]] == _UPGRADE_SQL


def test_upgrade_builds_no_index_and_backfills_nothing():
    op = _FakeOp()
    _load(op, "m0246_noindex").upgrade()
    sql = " ".join(str(payload) for kind, payload in op.events if kind == "execute").upper()
    assert "INDEX" not in sql
    assert "UPDATE AGENT_INVOCATIONS" not in sql and "INSERT" not in sql


# ── the downgrade ────────────────────────────────────────────────────────────

def test_downgrade_is_the_exact_inverse_in_reverse_order():
    op = _FakeOp()
    _load(op, "m0246_down").downgrade()
    assert op.events == [
        ("drop_constraint", (SHA, TABLE, "check")),
        ("drop_constraint", (PAIR, TABLE, "check")),
        ("drop_column", (TABLE, "input_sha256")),
        ("drop_column", (TABLE, "input_payload")),
    ]


def test_the_downgrade_drops_what_the_upgrade_added_and_nothing_else():
    up, down = _FakeOp(), _FakeOp()
    _load(up, "m0246_up2").upgrade()
    _load(down, "m0246_down2").downgrade()
    added_columns = [payload[1].name for kind, payload in up.events if kind == "add_column"]  # type: ignore[index]
    added_checks = [m.group(1) for _, sql in up.events if (m := re.search(r"ADD CONSTRAINT (\w+)", str(sql)))]
    dropped_columns = [payload[1] for kind, payload in down.events if kind == "drop_column"]  # type: ignore[index]
    dropped_checks = [payload[0] for kind, payload in down.events if kind == "drop_constraint"]  # type: ignore[index]
    assert dropped_columns == list(reversed(added_columns))
    assert dropped_checks == list(reversed(added_checks))


# ── the ORM twin ─────────────────────────────────────────────────────────────

def _table() -> sa.Table:
    from app.models.postgres import AgentInvocation

    return AgentInvocation.__table__


def _orm_check(name: str) -> sa.CheckConstraint:
    matches = [c for c in _table().constraints if isinstance(c, sa.CheckConstraint) and c.name == name]
    assert len(matches) == 1, name
    return matches[0]


@pytest.mark.parametrize(("name", "text"), [(PAIR, PAIR_SQL), (SHA, SHA_SQL)])
def test_the_check_texts_are_the_orm_check_constraints(name, text):
    assert _squash(str(_orm_check(name).sqltext)) == text
    mod_text = M0246.read_text(encoding="utf-8")
    assert f"CHECK ({text}) NOT VALID" in _squash(mod_text.replace('"\n        "', "").replace('" "', ""))


def test_the_orm_columns_match_the_ddl():
    payload, sha = _table().c.input_payload, _table().c.input_sha256
    assert isinstance(payload.type, postgresql.JSONB)
    assert isinstance(sha.type, sa.String) and sha.type.length == 64
    for column in (payload, sha):
        assert column.nullable is True
        assert column.server_default is None and column.default is None
    assert not [ix for ix in _table().indexes if {"input_payload", "input_sha256"} & {c.name for c in ix.columns}]


def test_postgres_ddl_renders_both_checks():
    ddl = _squash(str(CreateTable(_table()).compile(dialect=postgresql.dialect())))
    assert f"CONSTRAINT {PAIR} CHECK ({PAIR_SQL})" in ddl
    assert f"CONSTRAINT {SHA} CHECK ({SHA_SQL})" in ddl


class _JsonbAsJsonTypeCompiler(SQLiteTypeCompiler):
    def visit_JSONB(self, type_, **kw):  # noqa: N802 -- SQLAlchemy's visitor name
        return "JSON"


class _SQLiteWithJsonb(sqlite.dialect):  # type: ignore[misc,valid-type]
    """SQLite, with JSONB compiled as JSON (what the ``create_all`` tests' hook does), no global hook."""

    type_compiler_cls = _JsonbAsJsonTypeCompiler


def test_sqlite_builds_the_table_with_the_pair_check_and_without_the_regex_check():
    ddl = str(CreateTable(_table()).compile(dialect=_SQLiteWithJsonb()))
    assert PAIR in ddl and SHA not in ddl and " ~ " not in ddl
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(ddl)
        insert = (
            "INSERT INTO agent_invocations (id, project_id, agent_id, stage_name, test_run_id, pipeline_run_id, "
            "workflow_type, mode, cancel_requested, input_payload, input_sha256) "
            "VALUES (?, ?, 'agent.summary.v1', 'summary', ?, ?, 'offline', 'async', 0, ?, ?)"
        )

        def row(payload, sha):
            return (uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex, payload, sha)

        with engine.begin() as conn:
            conn.exec_driver_sql(insert, row(None, None))
            conn.exec_driver_sql(insert, row('{"schema_version": 1}', "a" * 64))
        for payload, sha in (('{"schema_version": 1}', None), (None, "a" * 64)):
            with pytest.raises(sa.exc.IntegrityError, match="CHECK constraint failed"):
                with engine.begin() as conn:
                    conn.exec_driver_sql(insert, row(payload, sha))
    finally:
        engine.dispose()
