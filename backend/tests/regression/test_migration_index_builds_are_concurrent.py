"""An index build on an existing table must not lock out ingestion.

A plain ``CREATE INDEX`` holds a SHARE lock on its table for the whole build,
which blocks every INSERT, UPDATE and DELETE until it finishes. Migration 0166
built a GIN trigram index on ``test_cases`` that way -- the table every ingest
writes, in the millions on a real deployment -- while 0167, in the same batch,
built CONCURRENTLY "because every ingest writes test_runs". A code review
caught the difference. This makes it a rule instead of a reviewer's memory.

From 0166 on, an index on a table the migration did not create itself is built
``CONCURRENTLY``, inside ``autocommit_block()``: Postgres refuses a concurrent
build inside a transaction block. Earlier migrations (0058 and 0138 among them)
predate the rule and are not rewritten, because they have already run
everywhere.

The scan reads the AST, not the text: SQL is split across implicitly
concatenated literals and f-strings, and a migration's docstring discusses SQL
it does not run.
"""
from __future__ import annotations

import ast
import pathlib
import re

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
FIRST_GUARDED = 166

_CREATE_INDEX = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?P<concurrently>CONCURRENTLY\s+)?"
    r"(?:IF\s+NOT\s+EXISTS\s+)?\S+\s+ON\s+(?:ONLY\s+)?(?P<table>[\w\".]+)",
    re.IGNORECASE,
)
_CREATE_TABLE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>[\w\".]+)",
    re.IGNORECASE,
)


def _table(raw: str) -> str:
    return raw.split(".")[-1].strip('"').lower()


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _string_arg(node: ast.Call, index: int, keyword: str) -> str | None:
    if len(node.args) > index:
        value = node.args[index]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    for kw in node.keywords:
        if kw.arg == keyword and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _sql_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, text) of every string literal that is not a docstring.

    An f-string's holes are shown as ``{expr}``; the parts of an f-string are
    not reported again on their own.
    """
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in node.values)
    found = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                str(part.value) if isinstance(part, ast.Constant)
                else "{" + ast.unparse(part.value) + "}"
                for part in node.values
                if isinstance(part, (ast.Constant, ast.FormattedValue))
            )
            found.append((node.lineno, text))
    return found


def _builds(source: str) -> list[tuple[int, str, bool]]:
    """(line, table, concurrent) for every index a migration builds."""
    tree = ast.parse(source)
    builds = [
        (line, _table(match.group("table")), bool(match.group("concurrently")))
        for line, text in _sql_literals(tree)
        for match in _CREATE_INDEX.finditer(text)
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "create_index":
            concurrent = any(
                kw.arg == "postgresql_concurrently"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            table = _string_arg(node, 1, "table_name") or "?"
            builds.append((node.lineno, table.lower(), concurrent))
    return builds


def _created_tables(source: str) -> set[str]:
    tree = ast.parse(source)
    tables = {
        _table(match.group("table"))
        for _line, text in _sql_literals(tree)
        for match in _CREATE_TABLE.finditer(text)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "create_table":
            name = _string_arg(node, 0, "table_name")
            if name:
                tables.add(name.lower())
    return tables


def _blocking_builds(source: str) -> list[str]:
    created = _created_tables(source)
    return [
        f"line {line}: index on {table} built without CONCURRENTLY"
        for line, table, concurrent in _builds(source)
        if not concurrent and table not in created
    ]


def _concurrent_builds_inside_a_transaction(source: str) -> list[int]:
    tree = ast.parse(source)
    spans = [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.With, ast.AsyncWith))
        and any(
            isinstance(item.context_expr, ast.Call)
            and _call_name(item.context_expr) == "autocommit_block"
            for item in node.items
        )
    ]
    return [
        line
        for line, _table_name, concurrent in _builds(source)
        if concurrent and not any(start <= line <= end for start, end in spans)
    ]


def _guarded_migrations() -> list[pathlib.Path]:
    guarded = []
    for path in sorted(VERSIONS.glob("*.py")):
        match = re.match(r"(\d{4})_", path.name)
        if match and int(match.group(1)) >= FIRST_GUARDED:
            guarded.append(path)
    return guarded


# ── The rule, applied to every migration from 0166 on ────────────────────


def test_an_index_on_an_existing_table_is_built_concurrently():
    offenders = [
        f"{path.name} {problem}"
        for path in _guarded_migrations()
        for problem in _blocking_builds(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "a plain CREATE INDEX holds a SHARE lock for the whole build, blocking "
        "every write to the table -- on test_cases or test_runs, every ingest. "
        "Build it CONCURRENTLY inside op.get_context().autocommit_block(), as "
        "0167 does: " + "; ".join(offenders)
    )


def test_a_concurrent_build_runs_outside_the_migration_transaction():
    offenders = [
        f"{path.name} line {line}"
        for path in _guarded_migrations()
        for line in _concurrent_builds_inside_a_transaction(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so "
        "the migration fails at deploy time. Wrap it in "
        "op.get_context().autocommit_block(): " + "; ".join(offenders)
    )


def test_the_scan_sees_the_builds_it_guards():
    """A scan that found nothing would pass forever."""
    found = {path.name[:4]: _builds(path.read_text(encoding="utf-8")) for path in _guarded_migrations()}
    assert [table for _line, table, _c in found.get("0166", [])] == ["test_cases"], found.get("0166")
    assert [table for _line, table, _c in found.get("0167", [])] == ["test_runs"], found.get("0167")


# ── The checker itself ───────────────────────────────────────────────────

BLOCKING = '''
from alembic import op


def upgrade():
    op.execute("CREATE INDEX ix_x ON test_cases (name)")
    op.create_index("ix_y", "test_runs", ["build_number"])
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {NAME} "
        "ON public.test_results (id)"
    )
'''

OWN_TABLE = '''
import sqlalchemy as sa
from alembic import op


def upgrade():
    op.create_table("widgets", sa.Column("id", sa.Integer, primary_key=True))
    op.create_index("ix_widgets_id", "widgets", ["id"])
    op.execute("CREATE TABLE gadgets (id int)")
    op.execute("CREATE INDEX ix_gadgets_id ON gadgets (id)")
'''

PROSE = '''
"""Why: CREATE INDEX ix_doc ON test_cases (name) would lock the table."""
from alembic import op


def upgrade():
    """A plain CREATE INDEX ix_fn ON test_runs (id) is what we avoid."""
'''

INSIDE_TRANSACTION = '''
from alembic import op


def upgrade():
    op.execute("CREATE INDEX CONCURRENTLY ix_x ON test_cases (name)")
    with op.get_context().autocommit_block():
        op.create_index("ix_y", "test_runs", ["id"], postgresql_concurrently=True)
'''


def test_the_checker_flags_every_shape_of_blocking_build():
    problems = _blocking_builds(BLOCKING)
    assert [p.split(": index on ")[1].split(" ")[0] for p in problems] == [
        "test_cases", "test_results", "test_runs",
    ] or sorted(p.split(": index on ")[1].split(" ")[0] for p in problems) == [
        "test_cases", "test_results", "test_runs",
    ], problems


def test_an_index_on_a_table_the_migration_creates_is_allowed():
    assert _blocking_builds(OWN_TABLE) == []


def test_prose_in_a_docstring_is_not_a_build():
    assert _builds(PROSE) == []


def test_a_concurrent_build_inside_the_transaction_is_flagged():
    assert _concurrent_builds_inside_a_transaction(INSIDE_TRANSACTION) == [6]
