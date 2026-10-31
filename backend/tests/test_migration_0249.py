"""Migration 0249 (W25b HYG-05): shape only, no database.

* the chain 0248 -> 0249, with 0249 the only head (every earlier migration
  test asserts ``heads[0] >= …`` and keeps passing);
* the upgrade builds ``ix_ctc_first_seen_run`` / ``ix_ctc_last_seen_run`` /
  ``ix_ctc_deleted_at_run`` -- partial (``<col> IS NOT NULL``), CONCURRENTLY,
  ``IF NOT EXISTS`` -- in that order inside ONE ``autocommit_block``; an
  INVALID leftover of a failed build is dropped (CONCURRENTLY, inside the
  block) immediately before its own build; offline (``--sql``) mode skips the
  catalog probe;
* the downgrade drops exactly the three, CONCURRENTLY, inside the block;
* NOTHING else, in either direction: no DML, no table change, and the only
  read is the catalog probe -- the D26 proof that 0249 changes no row of
  ``canonical_test_cases`` (the suite-membership parity clock);
* each statement is its ORM ``Index`` twin compiled for PostgreSQL (same
  column, same predicate), and the twins also build on SQLite (the
  ``create_all`` tests);
* the file imports no application code.

The real DDL (VALID indexes, the referential statement's plan, an INVALID
leftover rebuilt, the row checksum unchanged, the round trip) runs against
Postgres in ``tests/integration/test_migration_0249_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
M0249 = VERSIONS / "0249_ctc_run_fk_indexes.py"
TABLE = "canonical_test_cases"
#: index name -> its column, in the order the upgrade builds them.
INDEXES = {
    "ix_ctc_first_seen_run": "first_seen_run_id",
    "ix_ctc_last_seen_run": "last_seen_run_id",
    "ix_ctc_deleted_at_run": "deleted_at_run_id",
}


def _create(name: str) -> str:
    column = INDEXES[name]
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
        f"ON canonical_test_cases ({column}) WHERE {column} IS NOT NULL"
    )


def _drop(name: str) -> str:
    return f"DROP INDEX CONCURRENTLY IF EXISTS {name}"


class _FakeOp:
    """Records EVERY operation in order, and whether it ran inside ``autocommit_block``.

    ``execute`` and the block are modelled; any other ``op.<name>(...)`` call (``add_column``,
    ``create_index``, ``alter_column``, ``bulk_insert``, ...) is recorded as ``("other:<name>", …)``
    instead of raising, so "no other operation" is an assertion on the event list, not on an
    ``AttributeError``.
    """

    def __init__(self, *, invalid: tuple[str, ...] = (), as_sql: bool = False) -> None:
        self.events: list[tuple[str, object, bool]] = []
        self.probes: list[dict] = []
        self._in_block = False
        self._invalid = invalid
        outer = self

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return list(self._rows)

        class _Bind:
            def execute(self, statement, params=None):
                outer.probes.append({"sql": str(statement), "params": params})
                asked = set((params or {}).get("names", ()))
                return _Result([(name,) for name in outer._invalid if name in asked])

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

    def execute(self, sql, *args, **kwargs):
        self.events.append(("execute", str(sql), self._in_block))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _other(*args, **kwargs):
            self.events.append((f"other:{name}", (args, kwargs), self._in_block))

        return _other


def _load(op: _FakeOp, name: str):
    spec = importlib.util.spec_from_file_location(name, M0249)
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


def _executes(op: _FakeOp) -> list[tuple[str, bool]]:
    return [(_squash(str(sql)), in_block) for kind, sql, in_block in op.events if kind == "execute"]


def _assert_only_index_ddl(op: _FakeOp) -> None:
    """The D26 proof on the code side: every event is the block or an index DDL statement."""
    kinds = {kind for kind, _, _ in op.events}
    assert kinds <= {"enter_block", "execute", "exit_block"}, op.events
    allowed = re.compile(
        r"^(CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ctc_(first_seen|last_seen|deleted_at)_run "
        r"ON canonical_test_cases \(\w+\) WHERE \w+ IS NOT NULL"
        r"|DROP INDEX CONCURRENTLY IF EXISTS ix_ctc_(first_seen|last_seen|deleted_at)_run)$"
    )
    for sql, _ in _executes(op):
        assert allowed.match(sql), sql
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|MERGE|COPY)\b", sql, re.I), sql
    # The only read: the catalog probe (pg_class / pg_index), never the table itself.
    for probe in op.probes:
        sql = _squash(probe["sql"])
        assert sql.startswith("SELECT ") and "pg_index" in sql and "pg_class" in sql, sql
        assert TABLE not in sql, sql


# ── the chain and the hygiene ────────────────────────────────────────────────


def test_the_chain_is_linear_with_0249_the_only_head():
    revisions = _revisions()
    assert revisions["0249"] == "0248"
    parents = [p for p in revisions.values() if p]
    assert parents.count("0248") == 1  # no fork at 0248
    heads = sorted(r for r in revisions if r not in set(parents))
    assert len(heads) == 1, heads
    assert heads[0] >= "0249"


def test_the_migration_imports_no_application_code():
    tree = ast.parse(M0249.read_text(encoding="utf-8"))
    modules = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m == "app" or m.startswith("app.")], modules


# ── the upgrade ──────────────────────────────────────────────────────────────


def test_upgrade_builds_the_three_partial_indexes_concurrently_in_one_block():
    op = _FakeOp()
    _load(op, "m0249_up").upgrade()
    assert [kind for kind, _, _ in op.events] == [
        "enter_block", "execute", "execute", "execute", "exit_block",
    ], op.events
    assert _executes(op) == [(_create(name), True) for name in INDEXES]
    _assert_only_index_ddl(op)
    # The INVALID-leftover probe asked about exactly these three names, by bind.
    assert [p["params"] for p in op.probes] == [{"names": list(INDEXES)}]
    assert "indisvalid" in op.probes[0]["sql"]


def test_an_invalid_leftover_is_dropped_concurrently_right_before_its_own_build():
    op = _FakeOp(invalid=("ix_ctc_last_seen_run",))
    _load(op, "m0249_invalid").upgrade()
    assert _executes(op) == [
        (_create("ix_ctc_first_seen_run"), True),
        (_drop("ix_ctc_last_seen_run"), True),
        (_create("ix_ctc_last_seen_run"), True),
        (_create("ix_ctc_deleted_at_run"), True),
    ]
    assert [kind for kind, _, _ in op.events].count("enter_block") == 1
    _assert_only_index_ddl(op)


def test_every_invalid_leftover_is_dropped_before_its_build():
    op = _FakeOp(invalid=tuple(INDEXES))
    _load(op, "m0249_all_invalid").upgrade()
    want = []
    for name in INDEXES:
        want += [(_drop(name), True), (_create(name), True)]
    assert _executes(op) == want
    _assert_only_index_ddl(op)


def test_offline_sql_mode_skips_the_probe_and_still_builds_concurrently():
    op = _FakeOp(as_sql=True)
    _load(op, "m0249_offline").upgrade()
    assert op.probes == []
    assert _executes(op) == [(_create(name), True) for name in INDEXES]
    _assert_only_index_ddl(op)


# ── the downgrade ────────────────────────────────────────────────────────────


def test_downgrade_drops_exactly_the_three_concurrently_inside_the_block():
    op = _FakeOp()
    _load(op, "m0249_down").downgrade()
    assert [kind for kind, _, _ in op.events] == [
        "enter_block", "execute", "execute", "execute", "exit_block",
    ], op.events
    assert _executes(op) == [
        (_drop("ix_ctc_deleted_at_run"), True),
        (_drop("ix_ctc_last_seen_run"), True),
        (_drop("ix_ctc_first_seen_run"), True),
    ]
    assert op.probes == [], "the downgrade reads nothing"
    _assert_only_index_ddl(op)


# ── the ORM twins ────────────────────────────────────────────────────────────


def _orm_index(name: str) -> sa.Index:
    from app.models.postgres import CanonicalTestCase

    matches = [ix for ix in CanonicalTestCase.__table__.indexes if ix.name == name]
    assert len(matches) == 1, sorted(ix.name for ix in CanonicalTestCase.__table__.indexes)
    return matches[0]


@pytest.mark.parametrize("name", list(INDEXES))
def test_each_statement_is_its_orm_twin_compiled_for_postgres(name):
    mod = _load(_FakeOp(), f"m0249_orm_{name}")
    statement = {
        "ix_ctc_first_seen_run": mod._FIRST_SEEN_SQL,
        "ix_ctc_last_seen_run": mod._LAST_SEEN_SQL,
        "ix_ctc_deleted_at_run": mod._DELETED_AT_SQL,
    }[name]
    index = _orm_index(name)
    orm_sql = _squash(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
    column = INDEXES[name]
    # The ORM renders the plain build; the migration adds only the online / idempotent keywords.
    assert _squash(statement).replace("CONCURRENTLY IF NOT EXISTS ", "") == orm_sql
    assert orm_sql == f"CREATE INDEX {name} ON canonical_test_cases ({column}) WHERE {column} IS NOT NULL"
    assert [c.name for c in index.columns] == [column]
    assert not index.unique
    assert mod.TABLE == TABLE and mod.INDEXES == tuple(INDEXES)


def test_the_orm_twins_build_on_sqlite_too():
    # The SQLite ``create_all`` tests build canonical_test_cases from the ORM: no sqlite_where, so
    # each twin is a plain index there -- harmless, and it must still be valid SQLite.
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE canonical_test_cases (id TEXT PRIMARY KEY, first_seen_run_id TEXT, "
            "last_seen_run_id TEXT, deleted_at_run_id TEXT)"
        )
        for name in INDEXES:
            conn.exec_driver_sql(str(CreateIndex(_orm_index(name)).compile(dialect=sqlite.dialect())))
        names = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list('canonical_test_cases')")}
    engine.dispose()
    assert set(INDEXES) <= names
