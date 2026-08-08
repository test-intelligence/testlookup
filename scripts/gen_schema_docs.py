#!/usr/bin/env python3
"""Extract the PostgreSQL table inventory from the SQLAlchemy models.

``architecture/DATABASE_SCHEMA.md`` says its ER diagrams and schema reference are
"generated, not hand-maintained", and that the extractor "lives in the PR that
introduced these docs". It was never committed — so the doc could not actually be
regenerated, and it drifted 13 tables behind (96 documented vs 109 declared)
before anyone noticed. This is that extractor, committed.

Reads ``backend/app/models/postgres.py`` with :mod:`ast` — no database
connection and no imports of the app, so it is safe to run anywhere, including
CI and an air-gapped box.

Usage::

    python scripts/gen_schema_docs.py --list                 # table -> model
    python scripts/gen_schema_docs.py --check                # drift vs the doc
    python scripts/gen_schema_docs.py --emit <table> [...]   # reference entries
    python scripts/gen_schema_docs.py --domains              # domain counts

``--check`` exits non-zero when the model file declares a table the doc does not
mention, which is the drift that actually bit us.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS = REPO_ROOT / "backend" / "app" / "models" / "postgres.py"
SCHEMA_DOC = REPO_ROOT / "architecture" / "DATABASE_SCHEMA.md"


class Column:
    __slots__ = ("name", "type", "primary_key", "foreign_key", "nullable", "unique", "index", "default")

    def __init__(self, name: str) -> None:
        self.name = name
        self.type = ""
        self.primary_key = False
        self.foreign_key = ""
        self.nullable = True
        self.unique = False
        self.index = False
        self.default = False

    @property
    def key(self) -> str:
        if self.primary_key:
            return "PK"
        return "FK" if self.foreign_key else ""

    @property
    def flags(self) -> str:
        parts = []
        if not self.nullable:
            parts.append("NN")
        if self.unique:
            parts.append("U")
        if self.index:
            parts.append("IX")
        if self.default:
            parts.append("def")
        return " ".join(parts)


class Table:
    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model
        self.columns: list[Column] = []


def _call_name(node: ast.AST) -> str:
    """``Mapped[...] = mapped_column(String(255), ...)`` -> 'mapped_column'."""
    if isinstance(node, ast.Call):
        func = node.func
        return getattr(func, "id", None) or getattr(func, "attr", "") or ""
    return ""


def _type_repr(node: ast.AST) -> str:
    """Render the SQLAlchemy type argument as it appears in the doc."""
    if isinstance(node, ast.Call):
        base = _call_name(node)
        args = []
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                args.append(str(arg.value))
        if base in {"String", "Numeric"} and args:
            return f"{base}({', '.join(args)})"
        if base == "UUID":
            return "UUID"
        return base
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _parse_column(name: str, call: ast.Call) -> Column:
    col = Column(name)
    positional = list(call.args)
    if positional:
        col.type = _type_repr(positional[0])
    for arg in positional:
        if _call_name(arg) == "ForeignKey" and arg.args:
            target = arg.args[0]
            if isinstance(target, ast.Constant):
                col.foreign_key = str(target.value)
    for kw in call.keywords:
        if kw.arg == "primary_key":
            col.primary_key = bool(getattr(kw.value, "value", False))
        elif kw.arg == "nullable":
            col.nullable = bool(getattr(kw.value, "value", True))
        elif kw.arg == "unique":
            col.unique = bool(getattr(kw.value, "value", False))
        elif kw.arg == "index":
            col.index = bool(getattr(kw.value, "value", False))
        elif kw.arg in {"default", "server_default"}:
            col.default = True
    return col


def _annotation_optional(node: ast.AST | None) -> bool:
    """``Mapped[Optional[int]]`` / ``Mapped[int | None]`` -> nullable."""
    if node is None:
        return False
    text = ast.unparse(node)
    return "Optional[" in text or "| None" in text or "None |" in text


def extract() -> list[Table]:
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    tables: list[Table] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        tablename = None
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if getattr(target, "id", None) == "__tablename__" and isinstance(stmt.value, ast.Constant):
                        tablename = str(stmt.value.value)
        if not tablename:
            continue
        table = Table(tablename, node.name)
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or stmt.value is None:
                continue
            if _call_name(stmt.value) != "mapped_column":
                continue
            col_name = getattr(stmt.target, "id", None)
            if not col_name:
                continue
            col = _parse_column(col_name, stmt.value)
            # An explicit nullable= wins; otherwise infer from the annotation.
            if not any(kw.arg == "nullable" for kw in stmt.value.keywords):
                col.nullable = _annotation_optional(stmt.annotation)
            table.columns.append(col)
        tables.append(table)
    return tables


def emit_entry(table: Table) -> str:
    lines = [
        f"#### `{table.name}`  <sub>(model `{table.model}`)</sub>",
        "",
        "| Column | Type | Key | Flags | References |",
        "|---|---|---|---|---|",
    ]
    for col in table.columns:
        ref = f"`{col.foreign_key}`" if col.foreign_key else ""
        lines.append(f"| `{col.name}` | `{col.type}` | {col.key} | {col.flags} | {ref} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="table -> model")
    parser.add_argument("--check", action="store_true", help="fail on tables missing from the doc")
    parser.add_argument("--emit", nargs="*", metavar="TABLE", help="emit reference entries")
    parser.add_argument("--domains", action="store_true", help="table count")
    args = parser.parse_args()

    tables = extract()
    by_name = {t.name: t for t in tables}

    if args.list:
        for t in sorted(tables, key=lambda x: x.name):
            print(f"{t.name:<40} {t.model}")
    if args.domains:
        print(f"{len(tables)} tables declared in {MODELS.relative_to(REPO_ROOT)}")
    if args.emit is not None:
        names = args.emit or sorted(by_name)
        for name in names:
            if name not in by_name:
                print(f"# unknown table: {name}", file=sys.stderr)
                continue
            print(emit_entry(by_name[name]))
    if args.check:
        doc = SCHEMA_DOC.read_text(encoding="utf-8")
        missing = [t.name for t in tables if f"`{t.name}`" not in doc]
        claimed = re.search(r"(\d+)\s+tables group into", doc)
        print(f"declared: {len(tables)}   documented: {len(tables) - len(missing)}   missing: {len(missing)}")
        if claimed and int(claimed.group(1)) != len(tables):
            print(f"  doc claims '{claimed.group(1)} tables' but {len(tables)} are declared")
        for name in missing:
            print(f"  MISSING  {name}")
        return 1 if missing or (claimed and int(claimed.group(1)) != len(tables)) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
