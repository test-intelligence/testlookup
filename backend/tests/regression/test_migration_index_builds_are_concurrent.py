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

Every way of writing a build counts:

* ``CREATE [UNIQUE] INDEX``, with or without a name, and ``REINDEX``;
* ``op.create_index``, ``batch_op.create_index``, and ``op.add_column`` of a
  column declared ``index=True``;
* a UNIQUE, PRIMARY KEY or EXCLUDE constraint added to an existing table, in
  SQL or through ``create_unique_constraint``, ``create_primary_key``,
  ``create_exclude_constraint`` or a column declared ``unique=True``. These
  build their index under ACCESS EXCLUSIVE, which blocks reads as well as
  writes, and have no concurrent form. The concurrent route is ``CREATE UNIQUE
  INDEX CONCURRENTLY`` and then ``ALTER TABLE ... ADD CONSTRAINT ... UNIQUE
  USING INDEX``, which only attaches the finished index, so that passes.

Dropping counts too. A plain ``DROP INDEX`` takes ACCESS EXCLUSIVE on the
index's table: brief, but it queues behind every open transaction, and every
write queues behind it. 0166 and 0167 dropped an INVALID leftover that way,
from inside a ``DO`` block, which cannot run anything else; they now decide
from Python and drop CONCURRENTLY. The table of a dropped index is the one
this migration built it on, else unknown -- an existing table.

The scan reads the AST, not the text: a migration's docstring discusses SQL it
does not run, and SQL is assembled -- implicitly concatenated literals,
f-strings, ``.format()``, ``+``, ``%``, ``str.join`` -- from constants (0166
and 0167 name their index through one). Each string expression is evaluated
with the names the module binds exactly once, to a value the scan can itself
evaluate. What cannot be resolved stays a hole, and a table the scan cannot
name is treated as an existing table: blocking unless CONCURRENTLY.
"""
from __future__ import annotations

import ast
import pathlib
import re
import string
import textwrap
from typing import NamedTuple

import pytest

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
FIRST_GUARDED = 166

HOLE = "\x00"  # stands for a part of a string the scan could not resolve
UNKNOWN = "<unknown>"  # a table the scan cannot name, treated as an existing one

# An SQL identifier: possibly qualified or quoted, possibly holding holes.
_IDENT = r'(?:"[^"]*"|[\w.\x00])+'

_CREATE_INDEX = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b"
    r"(?P<options>(?:\s+(?!ON\b)" + _IDENT + r"){0,6}?)"
    r"\s+ON\s+(?:ONLY\s+)?(?P<table>" + _IDENT + r")",
    re.IGNORECASE,
)
_REINDEX = re.compile(
    r"\bREINDEX\s+(?:\([^)]*\)\s*)?(?P<target>INDEX|TABLE|SCHEMA|DATABASE|SYSTEM)\b"
    r"(?P<concurrently>\s+CONCURRENTLY\b)?(?:\s+(?P<name>" + _IDENT + r"))?",
    re.IGNORECASE,
)
_ALTER_TABLE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<table>" + _IDENT + r")"
    r"(?P<actions>(?:(?!\bCREATE\b)[^;])*)",
    re.IGNORECASE,
)
# Inside an ALTER TABLE: a constraint that builds its own index. USING INDEX
# adopts a finished index instead, and builds nothing.
_INDEXED_CONSTRAINT = re.compile(
    r"\b(?:UNIQUE|PRIMARY\s+KEY|EXCLUDE)\b(?!\s+USING\s+INDEX\b)",
    re.IGNORECASE,
)
_CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?TEMP(?:ORARY)?\s+|UNLOGGED\s+)?"
    r"(?:TABLE|MATERIALIZED\s+VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>" + _IDENT + r")",
    re.IGNORECASE,
)
_DROP_INDEX = re.compile(
    r"\bDROP\s+INDEX\b(?P<concurrently>\s+CONCURRENTLY\b)?(?:\s+IF\s+EXISTS\b)?"
    r"\s+(?P<names>" + _IDENT + r"(?:\s*,\s*" + _IDENT + r")*)",
    re.IGNORECASE,
)
_CONCURRENTLY = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)
_PERCENT_FIELD = re.compile(
    r"%(?:\([^)]*\))?[-#0 +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[diouxXeEfFgGcrsa]"
)

# Alembic operations that build an index, by kind. Called on ``op`` the table
# is the second argument; on a batch_alter_table object it is the batch's.
_ALEMBIC_BUILDS = {
    "create_index": "index",
    "create_unique_constraint": "constraint",
    "create_primary_key": "constraint",
    "create_exclude_constraint": "constraint",
}


class Operation(NamedTuple):
    line: int
    kind: str  # "index", or "constraint" for the index a constraint builds
    table: str  # UNKNOWN when the scan cannot name it
    concurrent: bool
    statement: str  # what the scan saw, for the failure message


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _table(raw: str | None) -> str:
    """A table or index name as the scan compares it: unqualified, unquoted, lower case."""
    if raw is None:
        return UNKNOWN
    name = raw.split(".")[-1].strip('"').lower()
    return UNKNOWN if not name or HOLE in name else name


def _shown(text: str) -> str:
    return " ".join(text.replace(HOLE, "{?}").split())[:120]


# ── Evaluating the strings a migration assembles ─────────────────────────

_UNRESOLVED = object()


def _bound_names(node: ast.AST) -> list[str]:
    """Every name ``node`` binds, however it binds it."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return [node.id]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.alias):
        return [(node.asname or node.name).split(".")[0]]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [node.name]
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        # Counted as a binding, so a name also assigned anywhere is ambiguous.
        return list(node.names)
    return []


def _constants(tree: ast.Module) -> dict[str, object]:
    """Names the module binds exactly once, to a value the scan can evaluate.

    Scope is ignored on purpose: a name bound twice anywhere, or bound any
    other way (a parameter, a loop target, an import), stays unresolved, so a
    local that shadows a module constant is never read as the constant.
    """
    bindings: dict[str, int] = {}
    assigned: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        for name in _bound_names(node):
            bindings[name] = bindings.get(name, 0) + 1
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigned[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assigned[node.target.id] = node.value
    pending = {name: value for name, value in assigned.items() if bindings.get(name) == 1}
    env: dict[str, object] = {}
    progress = True
    while progress:
        progress = False
        for name, value in list(pending.items()):
            resolved = _value(value, env)
            if resolved is not _UNRESOLVED:
                env[name] = resolved
                del pending[name]
                progress = True
    return env


def _value(node: ast.expr, env: dict[str, object]) -> object:
    """``node`` fully evaluated, or _UNRESOLVED."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, _UNRESOLVED)
    if isinstance(node, (ast.Tuple, ast.List)):
        items = tuple(_value(item, env) for item in node.elts)
        return _UNRESOLVED if any(item is _UNRESOLVED for item in items) else items
    text = _text(node, env)
    return text if text is not None and HOLE not in text else _UNRESOLVED


def _text(node: ast.AST, env: dict[str, object]) -> str | None:
    """The string ``node`` evaluates to, with a HOLE for each part the scan
    cannot resolve; None when ``node`` is not a string expression at all."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        value = env.get(node.id)
        return value if isinstance(value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(_formatted(part, env) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, env), _text(node.right, env)
        if left is None and right is None:
            return None
        return (HOLE if left is None else left) + (HOLE if right is None else right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _text(node.left, env)
        return None if template is None else _percent(template, node.right, env)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        receiver = _text(node.func.value, env)
        if receiver is None:
            return None
        if node.func.attr == "format":
            return _format(receiver, node, env)
        if node.func.attr == "join":
            items = node.args[0] if len(node.args) == 1 else None
            if isinstance(items, (ast.List, ast.Tuple)):
                return receiver.join(_piece(item, env) for item in items.elts)
            return HOLE
    return None


def _piece(node: ast.expr, env: dict[str, object]) -> str:
    """``node`` as it reads inside a larger string."""
    text = _text(node, env)
    if text is not None:
        return text
    value = _value(node, env)
    return HOLE if value is _UNRESOLVED or isinstance(value, tuple) else str(value)


def _formatted(part: ast.expr, env: dict[str, object]) -> str:
    """One part of an f-string."""
    if isinstance(part, ast.Constant):
        return str(part.value)
    if not isinstance(part, ast.FormattedValue):
        return HOLE
    if part.conversion == -1 and part.format_spec is None:
        return _piece(part.value, env)
    value = _value(part.value, env)
    spec = "" if part.format_spec is None else _text(part.format_spec, env)
    if value is _UNRESOLVED or spec is None or HOLE in spec:
        return HOLE
    convert = {-1: lambda v: v, ord("s"): str, ord("r"): repr, ord("a"): ascii}[part.conversion]
    try:
        return format(convert(value), spec)
    except (TypeError, ValueError):
        return HOLE


def _format(template: str, call: ast.Call, env: dict[str, object]) -> str:
    """``template.format(...)``, each field filled with its argument if known."""
    known = not any(isinstance(arg, ast.Starred) for arg in call.args) and all(
        keyword.arg for keyword in call.keywords
    )
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    try:
        fields = list(string.Formatter().parse(template))
    except ValueError:
        return re.sub(r"\{[^{}]*\}", HOLE, template)
    out: list[str] = []
    auto = 0
    for literal, field, spec, conversion in fields:
        out.append(literal)
        if field is None:
            continue
        key = re.match(r"[^.\[]*", field).group(0)
        access = field[len(key):]  # attribute or item access: not evaluated
        if key == "":
            key, auto = str(auto), auto + 1
        argument = None
        if known and not access:
            if key.isdigit():
                argument = call.args[int(key)] if int(key) < len(call.args) else None
            else:
                argument = keywords.get(key)
        piece = HOLE if argument is None else _piece(argument, env)
        if (spec or conversion) and HOLE not in piece:
            try:
                converter = {"r": repr, "a": ascii}.get(conversion or "", str)
                piece = format(converter(piece), spec or "")
            except (TypeError, ValueError):
                piece = HOLE
        out.append(piece)
    return "".join(out)


def _percent(template: str, right: ast.expr, env: dict[str, object]) -> str:
    """``template % right``, with a hole for each value the scan cannot resolve."""
    args: object
    if isinstance(right, ast.Tuple):
        args = tuple(_piece(item, env) for item in right.elts)
    elif isinstance(right, ast.Dict) and all(isinstance(key, ast.Constant) for key in right.keys):
        args = {key.value: _piece(value, env) for key, value in zip(right.keys, right.values)}
    else:
        value = _value(right, env)
        args = value if isinstance(value, tuple) else (_piece(right, env),)
    try:
        return template % args
    except (TypeError, ValueError, KeyError):
        return _PERCENT_FIELD.sub(HOLE, template)


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _uses(tree: ast.AST, env: dict[str, object]) -> dict[int, list[int]]:
    """For each constant's value node, the lines where the constant is read."""
    loads: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in env:
            loads.setdefault(node.id, []).append(node.lineno)
    uses: dict[int, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, value = node.target.id, node.value
        else:
            continue
        if name in env and name in loads:
            uses[id(value)] = sorted(loads[name])
    return uses


def _sql(tree: ast.AST, env: dict[str, object]) -> list[tuple[int, str]]:
    """(line, text) of every string expression outside a docstring.

    Only whole expressions: the parts of an f-string, a ``+`` or a
    ``.format()`` are evaluated with it, not reported again on their own. A
    string bound to a constant is reported where the constant is read, which is
    where it runs.
    """
    skip = _docstrings(tree)
    uses = _uses(tree, env)
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if id(node) in skip:
            return
        if isinstance(node, ast.expr) and not isinstance(node, ast.Name):
            text = _text(node, env)
            if text is not None:
                found.extend((line, text) for line in uses.get(id(node), [node.lineno]))
                return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


# ── What a migration does to indexes ─────────────────────────────────────


def _arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if len(call.args) > index and not any(isinstance(arg, ast.Starred) for arg in call.args[: index + 1]):
        return call.args[index]
    return None


def _arg_text(call: ast.Call, index: int, keyword: str, env: dict[str, object]) -> str | None:
    node = _arg(call, index, keyword)
    return None if node is None else _text(node, env)


def _is_true(call: ast.Call, keyword: str, env: dict[str, object]) -> bool:
    return any(kw.arg == keyword and _value(kw.value, env) is True for kw in call.keywords)


def _batches(tree: ast.AST, env: dict[str, object]) -> list[tuple[int, int, str, str]]:
    """(first line, last line, variable, table) of each ``with op.batch_alter_table(...) as var``."""
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and _call_name(call) == "batch_alter_table"
                    and isinstance(item.optional_vars, ast.Name)
                ):
                    table = _table(_arg_text(call, 0, "table_name", env))
                    spans.append((node.lineno, node.end_lineno or node.lineno, item.optional_vars.id, table))
    return spans


def _batch_table(call: ast.Call, batches: list[tuple[int, int, str, str]]) -> str | None:
    """The batch's table when ``call`` is made on a batch_alter_table object."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return None
    spans = [
        (end - start, table)
        for start, end, var, table in batches
        if var == func.value.id and start <= call.lineno <= end
    ]
    return min(spans)[1] if spans else None


def _operations(source: str) -> list[Operation]:
    """Every index this migration builds, however it is written."""
    tree = ast.parse(source)
    env = _constants(tree)
    strings = _sql(tree, env)
    batches = _batches(tree, env)
    found: list[Operation] = []
    index_tables: dict[str, str] = {}  # index name -> its table, from this migration's builds
    drop_calls: list[tuple[ast.Call, str | None, str]] = []

    for line, text in strings:
        for match in _CREATE_INDEX.finditer(text):
            table = _table(match.group("table"))
            options = match.group("options").split()
            if options and options[-1].upper() not in {"CONCURRENTLY", "EXISTS"}:
                index_tables[_table(options[-1])] = table
            concurrent = _CONCURRENTLY.search(match.group("options")) is not None
            found.append(Operation(line, "index", table, concurrent, _shown(match.group(0))))
        for match in _ALTER_TABLE.finditer(text):
            table = _table(match.group("table"))
            found.extend(
                Operation(line, "constraint", table, False, _shown(match.group(0)))
                for _ in _INDEXED_CONSTRAINT.finditer(match.group("actions"))
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        batch = _batch_table(node, batches)
        shown = _shown(ast.unparse(node))
        if name in _ALEMBIC_BUILDS:
            table = batch if batch is not None else _table(_arg_text(node, 1, "table_name", env))
            concurrent = name == "create_index" and _is_true(node, "postgresql_concurrently", env)
            found.append(Operation(node.lineno, _ALEMBIC_BUILDS[name], table, concurrent, shown))
            index_name = _arg_text(node, 0, "index_name", env)
            if name == "create_index" and index_name:
                index_tables[_table(index_name)] = table
        elif name == "add_column":
            table = batch if batch is not None else _table(_arg_text(node, 0, "table_name", env))
            column = _arg(node, 0 if batch is not None else 1, "column")
            if isinstance(column, ast.Call) and _call_name(column) == "Column":
                if _is_true(column, "index", env):
                    found.append(Operation(node.lineno, "index", table, False, shown))
                if _is_true(column, "unique", env):
                    found.append(Operation(node.lineno, "constraint", table, False, shown))
        elif name == "drop_index":
            drop_calls.append((node, batch, shown))

    # Drops last: an index's table may be known only from this migration's build.
    for node, batch, shown in drop_calls:
        if batch is not None:
            table = batch
        elif _arg(node, 1, "table_name") is not None:
            table = _table(_arg_text(node, 1, "table_name", env))
        else:
            table = index_tables.get(_table(_arg_text(node, 0, "index_name", env)), UNKNOWN)
        concurrent = _is_true(node, "postgresql_concurrently", env)
        found.append(Operation(node.lineno, "drop", table, concurrent, shown))
    for line, text in strings:
        for match in _DROP_INDEX.finditer(text):
            concurrent = bool(match.group("concurrently"))
            found.extend(
                Operation(line, "drop", index_tables.get(_table(name.strip()), UNKNOWN), concurrent,
                          _shown(match.group(0)))
                for name in match.group("names").split(",")
            )

    for line, text in strings:
        for match in _REINDEX.finditer(text):
            target = match.group("target").upper()
            if target == "TABLE":
                table = _table(match.group("name"))
            elif target == "INDEX" and match.group("name"):
                table = index_tables.get(_table(match.group("name")), UNKNOWN)
            else:
                table = UNKNOWN
            found.append(
                Operation(line, "index", table, bool(match.group("concurrently")), _shown(match.group(0)))
            )
    return sorted(found)


def _builds(source: str) -> list[Operation]:
    return [op for op in _operations(source) if op.kind != "drop"]


def _drops(source: str) -> list[Operation]:
    return [op for op in _operations(source) if op.kind == "drop"]


def _created_tables(source: str) -> set[str]:
    tree = ast.parse(source)
    env = _constants(tree)
    tables = {
        _table(match.group("table"))
        for _line, text in _sql(tree, env)
        for match in _CREATE_TABLE.finditer(text)
    }
    tables.update(
        _table(_arg_text(node, 0, "table_name", env))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "create_table"
    )
    tables.discard(UNKNOWN)
    return tables


def _blocking(source: str) -> list[Operation]:
    created = _created_tables(source)
    return [op for op in _operations(source) if not op.concurrent and op.table not in created]


def _explain(op: Operation) -> str:
    if op.kind == "drop":
        return (
            f"line {op.line}: `{op.statement}` takes ACCESS EXCLUSIVE on {op.table}, queued "
            "behind every open transaction with every ingest queued behind it; use DROP "
            "INDEX CONCURRENTLY inside autocommit_block()"
        )
    if op.kind == "constraint":
        return (
            f"line {op.line}: `{op.statement}` builds its index on {op.table} under ACCESS "
            "EXCLUSIVE; build a UNIQUE INDEX CONCURRENTLY, then ADD CONSTRAINT ... USING INDEX"
        )
    return f"line {op.line}: `{op.statement}` builds an index on {op.table} without CONCURRENTLY"


def _blocking_builds(source: str) -> list[str]:
    return [_explain(op) for op in _blocking(source)]


def _autocommit_spans(tree: ast.AST) -> list[tuple[int, int]]:
    return [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.With, ast.AsyncWith))
        and any(
            isinstance(item.context_expr, ast.Call)
            and _call_name(item.context_expr) == "autocommit_block"
            for item in node.items
        )
    ]


def _concurrent_outside_autocommit(source: str) -> list[int]:
    spans = _autocommit_spans(ast.parse(source))
    return [
        op.line
        for op in _operations(source)
        if op.concurrent and not any(start <= op.line <= end for start, end in spans)
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
        "every write to the table -- on test_cases or test_runs, every ingest -- "
        "and a UNIQUE/PRIMARY KEY constraint builds its index under ACCESS "
        "EXCLUSIVE. Build it CONCURRENTLY inside "
        "op.get_context().autocommit_block(), as 0167 does: " + "; ".join(offenders)
    )


def test_a_concurrent_build_runs_outside_the_migration_transaction():
    offenders = [
        f"{path.name} line {line}"
        for path in _guarded_migrations()
        for line in _concurrent_outside_autocommit(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "CONCURRENTLY cannot run inside a transaction block, so the migration "
        "fails at deploy time. Wrap it in op.get_context().autocommit_block(): "
        + "; ".join(offenders)
    )


def test_an_index_on_an_existing_table_is_dropped_concurrently():
    offenders = [
        f"{path.name} {_explain(op)}"
        for path in _guarded_migrations()
        for op in _blocking(path.read_text(encoding="utf-8"))
        if op.kind == "drop"
    ]
    assert not offenders, "; ".join(offenders)


def test_the_scan_sees_the_builds_it_guards():
    """A scan that found nothing would pass forever.

    Each migration builds its index CONCURRENTLY on the real table, and once
    plainly on the empty copy it creates to learn how the server renders the
    definition; it drops a leftover and, on downgrade, its index, CONCURRENTLY.
    """
    found = {
        path.name[:4]: [(op.kind, op.table, op.concurrent) for op in _operations(path.read_text(encoding="utf-8"))]
        for path in _guarded_migrations()
    }
    assert sorted(found.get("0166", [])) == sorted([
        ("index", "test_cases", True), ("index", "_alembic_0166_probe", False),
        ("drop", UNKNOWN, True), ("drop", "test_cases", True),
    ]), found.get("0166")
    assert sorted(found.get("0167", [])) == sorted([
        ("index", "test_runs", True), ("index", "_alembic_0167_probe", False),
        ("drop", UNKNOWN, True), ("drop", "test_runs", True),
    ]), found.get("0167")


# ── The checker itself ───────────────────────────────────────────────────

_TEMPLATE = '''
import sqlalchemy as sa
from alembic import op

TABLE = "test_cases"
NEW = "widgets"
NAME = "ix_x"
PREFIX = "test"
CASES = PREFIX + "_cases"


def upgrade(table=None):
BODY
'''


def _migration(body: str) -> str:
    return _TEMPLATE.replace("BODY", textwrap.indent(textwrap.dedent(body).strip("\n"), "    "))


# Each is one blocking build, on the table named (UNKNOWN: a table the scan
# cannot name, which counts as an existing one).
BLOCKING = {
    "a plain literal": ("test_cases", '''
        op.execute("CREATE INDEX ix_a ON test_cases (name)")
    '''),
    "the name in an f-string, split across literals": ("test_results", '''
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {NAME} "
            "ON public.test_results (id)"
        )
    '''),
    "the table in an f-string, from a module constant": ("test_cases", '''
        op.execute(f"CREATE INDEX ix_a ON {TABLE} (name)")
    '''),
    "the table from a constant built from constants": ("test_cases", '''
        op.execute(f"CREATE INDEX ix_a ON {CASES} (name)")
    '''),
    "the table in an f-string the scan cannot resolve": (UNKNOWN, '''
        op.execute(f"CREATE INDEX ix_a ON {table} (name)")
    '''),
    ".format() with a positional field": ("test_cases", '''
        op.execute("CREATE INDEX ix_b ON {} (name)".format("test_cases"))
    '''),
    ".format() with a keyword field": ("test_cases", '''
        op.execute("CREATE INDEX ix_b ON {t} (name)".format(t=TABLE))
    '''),
    "+": ("test_cases", '''
        op.execute("CREATE INDEX ix_c ON " + TABLE + " (name)")
    '''),
    "% with one value": ("test_cases", '''
        op.execute("CREATE INDEX ix_d ON %s (name)" % TABLE)
    '''),
    "% with a tuple": ("test_runs", '''
        op.execute("CREATE INDEX %s ON %s (name)" % (NAME, "test_runs"))
    '''),
    "str.join": ("test_cases", '''
        op.execute(" ".join(["CREATE INDEX ix_e ON", TABLE, "(name)"]))
    '''),
    "a local holding the whole statement": ("test_cases", '''
        sql = "CREATE INDEX ix_f ON " + TABLE + " (name)"
        op.execute(sql)
    '''),
    "an unnamed index": ("test_cases", '''
        op.execute("CREATE INDEX ON test_cases (name)")
    '''),
    "an unnamed index, lower case, in sa.text": ("test_runs", '''
        op.execute(sa.text("create index on test_runs (build_number)"))
    '''),
    "REINDEX TABLE": ("test_cases", '''
        op.execute("REINDEX TABLE test_cases")
    '''),
    "REINDEX INDEX": (UNKNOWN, '''
        op.execute("REINDEX INDEX ix_test_cases_name_trgm")
    '''),
    "op.create_index": ("test_runs", '''
        op.create_index("ix_g", "test_runs", ["build_number"])
    '''),
    "op.create_index with keywords": ("test_cases", '''
        op.create_index("ix_g", table_name=TABLE, columns=["name"])
    '''),
    "batch_op.create_index": ("test_runs", '''
        with op.batch_alter_table("test_runs") as batch_op:
            batch_op.create_index("ix_h", ["build_number"])
    '''),
    "op.add_column of a column with index=True": ("test_cases", '''
        op.add_column(TABLE, sa.Column("x", sa.String(), index=True))
    '''),
    "ALTER TABLE ADD CONSTRAINT UNIQUE": ("test_cases", '''
        op.execute("ALTER TABLE test_cases ADD CONSTRAINT uq UNIQUE (name)")
    '''),
    "ALTER TABLE ADD PRIMARY KEY": ("test_cases", '''
        op.execute(f"ALTER TABLE {TABLE} ADD PRIMARY KEY (id)")
    '''),
    "ALTER TABLE ADD COLUMN ... UNIQUE": ("test_runs", '''
        op.execute("ALTER TABLE test_runs ADD COLUMN token text UNIQUE")
    '''),
    "ALTER TABLE ADD CONSTRAINT EXCLUDE": ("test_runs", '''
        op.execute("ALTER TABLE test_runs ADD CONSTRAINT ex EXCLUDE USING gist (id WITH =)")
    '''),
    "op.create_unique_constraint": ("test_runs", '''
        op.create_unique_constraint("uq2", "test_runs", ["name"])
    '''),
    "op.create_primary_key": ("test_runs", '''
        op.create_primary_key("pk", "test_runs", ["id"])
    '''),
    "op.create_exclude_constraint": ("test_runs", '''
        op.create_exclude_constraint("ex", "test_runs", ("id", "="))
    '''),
    "batch_op.create_unique_constraint": ("test_cases", '''
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.create_unique_constraint("uq3", ["name"])
    '''),
    "op.add_column of a column with unique=True": ("test_cases", '''
        op.add_column("test_cases", sa.Column("y", sa.String(), unique=True))
    '''),
}


@pytest.mark.parametrize("table, body", list(BLOCKING.values()), ids=list(BLOCKING))
def test_every_shape_of_blocking_build_is_flagged(table, body):
    source = _migration(body)
    assert [op.table for op in _blocking(source)] == [table], _operations(source)
    assert len(_blocking_builds(source)) == 1


# Each passes both rules, having been SEEN: (builds the scan must find, body).
ALLOWED = {
    "concurrent builds inside autocommit_block": (4, '''
        with op.get_context().autocommit_block():
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON {TABLE} (name)")
            op.execute("CREATE INDEX CONCURRENTLY ON test_cases (name)")
            op.execute("REINDEX INDEX CONCURRENTLY ix_test_cases_name_trgm")
            op.create_index("ix_y", "test_runs", ["id"], postgresql_concurrently=True)
    '''),
    "a constraint that adopts a concurrently built index": (1, '''
        with op.get_context().autocommit_block():
            op.execute("CREATE UNIQUE INDEX CONCURRENTLY uq_ix ON test_cases (name)")
        op.execute("ALTER TABLE test_cases ADD CONSTRAINT uq UNIQUE USING INDEX uq_ix")
        op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT pk PRIMARY KEY USING INDEX pk_ix")
    '''),
    "ALTER TABLE forms that build no index": (0, '''
        op.execute("ALTER TABLE test_cases ADD COLUMN x int")
        op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT fk FOREIGN KEY (x) REFERENCES users (id)")
        op.execute("ALTER TABLE test_cases ADD CONSTRAINT ck CHECK (x > 0)")
        op.add_column("test_cases", sa.Column("y", sa.String(), nullable=True))
    '''),
    "builds on tables the migration creates": (10, '''
        op.create_table(NEW, sa.Column("id", sa.Integer, primary_key=True))
        op.create_index("ix_widgets_id", NEW, ["id"])
        op.create_unique_constraint("uq_widgets", NEW, ["id"])
        op.add_column(NEW, sa.Column("code", sa.String(), index=True, unique=True))
        op.execute(f"CREATE INDEX ON {NEW} (code)")
        op.execute(f"ALTER TABLE {NEW} ADD CONSTRAINT uq_code UNIQUE (code)")
        with op.batch_alter_table(NEW) as batch_op:
            batch_op.create_index("ix_widgets_code", ["code"])
        op.execute("CREATE TABLE gadgets (id int)")
        op.execute("CREATE INDEX ix_gadgets_id ON gadgets (id)")
        op.execute("CREATE TEMP TABLE probe (LIKE test_cases)")
        op.execute("CREATE INDEX probe_ix ON probe USING gin ((CAST(tags AS TEXT)) gin_trgm_ops)")
        op.execute("CREATE MATERIALIZED VIEW IF NOT EXISTS mv AS SELECT 1 AS id")
        op.execute("CREATE UNIQUE INDEX mv_id ON mv (id)")
    '''),
}


@pytest.mark.parametrize("seen, body", list(ALLOWED.values()), ids=list(ALLOWED))
def test_the_allowed_forms_pass(seen, body):
    source = _migration(body)
    assert len(_builds(source)) == seen, _builds(source)
    assert _blocking_builds(source) == []
    assert _concurrent_outside_autocommit(source) == []


SHADOWED = '''
from alembic import op

TABLE = "widgets"


def upgrade():
    op.execute(f"CREATE TABLE {TABLE} (id int)")
    _index("test_cases")


def _index(TABLE):
    op.execute(f"CREATE INDEX ix_s ON {TABLE} (id)")
'''


def test_a_name_bound_twice_is_not_resolved():
    """``TABLE`` is the constant in upgrade() but the parameter in _index().

    Read as the constant, the build on test_cases would pass as an index on
    the table the migration creates.
    """
    assert [op.table for op in _blocking(SHADOWED)] == [UNKNOWN]


PROSE = '''
"""Why: CREATE INDEX ix_doc ON test_cases (name) would lock the table."""
from alembic import op


def upgrade():
    """A plain CREATE INDEX ix_fn ON test_runs (id) is what we avoid."""
'''


def test_prose_in_a_docstring_is_not_a_build():
    assert _builds(PROSE) == []


INSIDE_TRANSACTION = '''
from alembic import op


def upgrade():
    op.execute("CREATE INDEX CONCURRENTLY ix_x ON test_cases (name)")
    with op.get_context().autocommit_block():
        op.create_index("ix_y", "test_runs", ["id"], postgresql_concurrently=True)
'''


def test_a_concurrent_build_inside_the_transaction_is_flagged():
    assert _concurrent_outside_autocommit(INSIDE_TRANSACTION) == [6]


CONSTANT_STATEMENT = '''
from alembic import op

BUILD = "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_x ON test_cases (name)"


def upgrade():
    with op.get_context().autocommit_block():
        op.execute(BUILD)


def downgrade():
    op.execute(BUILD)
'''


def test_a_statement_held_in_a_constant_is_checked_where_it_runs():
    """Line 4 defines it; line 9 runs it inside the block, line 13 outside."""
    assert _concurrent_outside_autocommit(CONSTANT_STATEMENT) == [13]


# ── Dropping an index ────────────────────────────────────────────────────
# A plain DROP INDEX takes ACCESS EXCLUSIVE on the index's table. Brief, but it
# queues behind every open transaction, and every write queues behind it.

# Each is one blocking drop, on the table named.
BLOCKING_DROPS = {
    "a plain DROP INDEX, table unknown": (UNKNOWN, '''
        op.execute("DROP INDEX ix_test_cases_name_trgm")
    '''),
    "a drop of an index this migration builds on an existing table": ("test_cases", '''
        with op.get_context().autocommit_block():
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON {TABLE} (name)")
        op.execute(f"DROP INDEX IF EXISTS {NAME}")
    '''),
    "a drop inside a DO block (0166 and 0167 before this rule)": ("test_cases", '''
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1) THEN "
            f"EXECUTE 'DROP INDEX {NAME}'; END IF; END $$"
        )
        with op.get_context().autocommit_block():
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON {TABLE} (name)")
    '''),
    "op.drop_index": ("test_runs", '''
        op.drop_index("ix_g", table_name="test_runs")
    '''),
    "batch_op.drop_index": ("test_runs", '''
        with op.batch_alter_table("test_runs") as batch_op:
            batch_op.drop_index("ix_h")
    '''),
}


@pytest.mark.parametrize("table, body", list(BLOCKING_DROPS.values()), ids=list(BLOCKING_DROPS))
def test_every_shape_of_blocking_drop_is_flagged(table, body):
    source = _migration(body)
    assert [(op.kind, op.table) for op in _blocking(source)] == [("drop", table)], _operations(source)


ALLOWED_DROPS = {
    "DROP INDEX CONCURRENTLY inside autocommit_block": '''
        stale = table
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {stale}")
            op.drop_index("ix_g", table_name="test_runs", postgresql_concurrently=True, if_exists=True)
    ''',
    "a drop on a table the migration creates": '''
        op.create_table(NEW, sa.Column("id", sa.Integer))
        op.create_index("ix_widgets_id", NEW, ["id"])
        op.drop_index("ix_widgets_id")
        op.execute("DROP INDEX ix_widgets_id")
    ''',
}


@pytest.mark.parametrize("body", list(ALLOWED_DROPS.values()), ids=list(ALLOWED_DROPS))
def test_the_allowed_drops_pass(body):
    source = _migration(body)
    assert len(_drops(source)) == 2, _operations(source)
    assert _blocking(source) == []
    assert _concurrent_outside_autocommit(source) == []


DROP_IN_TRANSACTION = '''
from alembic import op


def downgrade():
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_x")
'''


def test_a_concurrent_drop_inside_the_transaction_is_flagged():
    assert _concurrent_outside_autocommit(DROP_IN_TRANSACTION) == [6]


@pytest.mark.parametrize("filename", [
    "0166_test_case_tags_trgm_index.py", "0167_test_runs_natural_build_index.py",
])
def test_the_offline_script_drops_and_builds_concurrently(filename):
    """``alembic upgrade --sql`` has no catalog to read, so the leftover check
    cannot run; the script must still work, and drop and build CONCURRENTLY."""
    import importlib.util
    import io

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(filename[:-3], VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.upgrade()
    script = buffer.getvalue()
    drop = script.index(f"DROP INDEX CONCURRENTLY IF EXISTS {module.INDEX};")
    build = script.index(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {module.INDEX} ")
    assert drop < build, script
    assert re.search(r"\bDROP\s+INDEX\s+(?!CONCURRENTLY)", script) is None, script
