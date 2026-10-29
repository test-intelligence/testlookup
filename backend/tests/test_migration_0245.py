"""Migration 0245 (W23b FAIL-13 / FAIL-12): shape only, no database.

* the chain 0244 -> 0245, with 0245 the only head (every earlier migration
  test asserts ``heads[0] >= …`` and keeps passing);
* the upgrade adds ``test_runs.run_context`` (JSONB, nullable, no default: a
  catalogue change) and builds ``ix_test_runs_project_branch_created``
  CONCURRENTLY inside ``autocommit_block``, an INVALID leftover of a failed
  build dropped first (CONCURRENTLY, inside the block, before the build);
* the downgrade is the exact inverse: the index dropped CONCURRENTLY inside
  the block, then the column;
* the index SQL is the ORM ``Index`` expression, and the ORM column matches
  the DDL; the ORM index also builds on SQLite (the ``create_all`` tests);
* the file imports no application code.

The real DDL (the index VALID, the plan using it, a downgrade round trip) runs
against Postgres in ``tests/integration/test_migration_0245_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0245 = VERSIONS / "0245_run_context_and_bisect_index.py"
INDEX = "ix_test_runs_project_branch_created"


class _FakeOp:
    """Records every operation in order, and whether it ran inside ``autocommit_block``."""

    def __init__(self, *, invalid_leftover: bool = False, as_sql: bool = False) -> None:
        self.events: list[tuple[str, object, bool]] = []
        self._in_block = False
        self._invalid = invalid_leftover
        self.probes: list[dict] = []
        outer = self

        class _Bind:
            def execute(self, statement, params=None):
                outer.probes.append({"sql": str(statement), "params": params})
                return SimpleNamespace(first=lambda: (1,) if outer._invalid else None)

        class _Context:
            def __init__(self) -> None:
                self.as_sql = as_sql

            @contextmanager
            def autocommit_block(self):
                outer.events.append(("enter_block", None, False))
                outer._in_block = True
                try:
                    yield
                finally:
                    outer._in_block = False
                    outer.events.append(("exit_block", None, False))

        self._context = _Context()
        self._bind = _Bind()

    def get_context(self):
        return self._context

    def get_bind(self):
        return self._bind

    def add_column(self, table, column):
        self.events.append(("add_column", (table, column), self._in_block))

    def drop_column(self, table, column):
        self.events.append(("drop_column", (table, column), self._in_block))

    def execute(self, sql):
        self.events.append(("execute", str(sql), self._in_block))


def _load(op: _FakeOp, name: str):
    spec = importlib.util.spec_from_file_location(name, M0245)
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


def _index_elements(sql: str) -> list[str]:
    """The comma-separated element list of ``CREATE INDEX … ON test_runs (…)``, each element's
    redundant outer parentheses removed and whitespace collapsed."""
    body = re.search(r"\bON\s+test_runs\s*\((.*)\)\s*$", _squash(sql), re.IGNORECASE)
    assert body, sql
    elements, depth, current = [], 0, ""
    for ch in body.group(1):
        if ch == "," and depth == 0:
            elements.append(current)
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        current += ch
    elements.append(current)
    out = []
    for element in elements:
        element = element.strip()
        while element.startswith("(") and element.endswith(")") and _balanced(element[1:-1]):
            element = element[1:-1].strip()
        out.append(_squash(element))
    return out


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


# ── the chain and the hygiene ────────────────────────────────────────────────

def test_the_chain_is_linear_with_0245_the_only_head():
    revisions = _revisions()
    assert revisions["0245"] == "0244"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0244") == 1   # no fork at 0244
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0245"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0245.read_text(encoding="utf-8"))
    modules = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


# ── the upgrade ──────────────────────────────────────────────────────────────

def test_upgrade_adds_a_nullable_jsonb_column_with_no_default_then_builds_the_index_concurrently():
    op = _FakeOp()
    _load(op, "m0245_up").upgrade()
    kinds = [kind for kind, _, _ in op.events]
    assert kinds == ["add_column", "enter_block", "execute", "exit_block"], op.events

    _, (table, column), in_block = op.events[0]
    assert table == "test_runs" and not in_block
    assert column.name == "run_context"
    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is True
    assert column.server_default is None and column.default is None

    _, sql, in_block = op.events[2]
    assert in_block, "a CONCURRENTLY build must run inside autocommit_block (Postgres refuses it in a transaction)"
    assert _squash(sql) == (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_runs_project_branch_created "
        "ON test_runs (project_id, (coalesce(lower(branch), '')), created_at)"
    )
    # The INVALID-leftover probe asked about THIS index, by bound name.
    assert [p["params"] for p in op.probes] == [{"name": INDEX}]
    assert "indisvalid" in op.probes[0]["sql"]


def test_an_invalid_leftover_is_dropped_concurrently_before_the_build_inside_the_block():
    op = _FakeOp(invalid_leftover=True)
    _load(op, "m0245_invalid").upgrade()
    executes = [(sql, in_block) for kind, sql, in_block in op.events if kind == "execute"]
    assert [_squash(str(sql)) for sql, _ in executes] == [
        f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_test_runs_project_branch_created "
        "ON test_runs (project_id, (coalesce(lower(branch), '')), created_at)",
    ]
    assert all(in_block for _, in_block in executes)


def test_offline_sql_mode_skips_the_probe_and_still_builds_concurrently():
    op = _FakeOp(as_sql=True)
    _load(op, "m0245_offline").upgrade()
    assert op.probes == []
    executes = [_squash(str(sql)) for kind, sql, _ in op.events if kind == "execute"]
    assert len(executes) == 1 and executes[0].startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS")


# ── the downgrade ────────────────────────────────────────────────────────────

def test_downgrade_drops_the_index_concurrently_then_the_column():
    op = _FakeOp()
    _load(op, "m0245_down").downgrade()
    assert [kind for kind, _, _ in op.events] == ["enter_block", "execute", "exit_block", "drop_column"], op.events
    _, sql, in_block = op.events[1]
    assert in_block and _squash(sql) == f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}"
    assert op.events[3][1] == ("test_runs", "run_context")
    assert op.events[3][2] is False


# ── the ORM twin ─────────────────────────────────────────────────────────────

def _orm_index() -> sa.Index:
    from app.models.postgres import TestRun

    matches = [ix for ix in TestRun.__table__.indexes if ix.name == INDEX]
    assert len(matches) == 1, [ix.name for ix in TestRun.__table__.indexes]
    return matches[0]


def test_the_index_sql_is_the_orm_index_expression():
    mod = _load(_FakeOp(), "m0245_sql")
    orm_sql = str(CreateIndex(_orm_index()).compile(dialect=postgresql.dialect()))
    assert _index_elements(mod._INDEX_SQL) == _index_elements(orm_sql) == [
        "project_id", "coalesce(lower(branch), '')", "created_at",
    ]
    assert mod.INDEX == INDEX and mod.TABLE == "test_runs" and mod.COLUMN == "run_context"
    assert not _orm_index().unique


def test_the_orm_column_matches_the_ddl():
    from app.models.postgres import TestRun

    column = TestRun.__table__.c.run_context
    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is True
    assert column.server_default is None and column.default is None


def test_the_orm_index_builds_on_sqlite_too():
    # The SQLite ``create_all`` tests build test_runs from the ORM: the expression must be SQLite-valid.
    sql = str(CreateIndex(_orm_index()).compile(dialect=sqlite.dialect()))
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE test_runs (id TEXT PRIMARY KEY, project_id TEXT, branch TEXT, created_at TIMESTAMP)"
        )
        conn.exec_driver_sql(sql)
        names = [row[1] for row in conn.exec_driver_sql("PRAGMA index_list('test_runs')")]
    engine.dispose()
    assert INDEX in names
