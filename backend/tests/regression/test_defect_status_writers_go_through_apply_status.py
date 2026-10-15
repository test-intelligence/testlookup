"""Regression guard: every defect status change goes through ``defect_status.apply_status``.

``resolved_at`` used to be set by hand next to each status write, and one
writer forgot: the Jira resolution webhook closed INVALID defects with
``resolved_at`` NULL (``feedback_service.py``), so the defect trend could not
place those closes and "time to resolve" ignored them. ``apply_status`` is now
the ONLY code that changes ``resolution_status`` on an existing row; it stamps
``resolved_at`` on every close and clears it on every re-open (W06 DEF-04,
contract §2.4).

The scan (AST, every module under ``backend/app``) refuses, outside
``app/services/defect_status.py``:

* an assignment to ``<anything>.resolution_status`` (plain, annotated or augmented);
* ``setattr(<x>, "resolution_status", ...)``;
* ``update(Defect).values(resolution_status=...)`` (keyword or dict key);
* ``resolution_status=`` in ``Defect(...)`` or ``insert(Defect).values(...)``
  unless the value is the literal ``"OPEN"`` (creation needs no helper);
* a SQL string that contains ``SET resolution_status``.

Self-tests on written snippets keep the scanner honest in both directions.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED = {"services/defect_status.py"}
FIELD = "resolution_status"
_RAW_SQL_WRITE = re.compile(r"\bset\s+resolution_status\b", re.I)


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_defect_statement(node: ast.AST, verbs: tuple[str, ...]) -> bool:
    """``update(Defect)`` / ``insert(Defect)`` / ``pg_insert(Defect)`` (also ``sa.update(models.Defect)``)."""
    return (
        isinstance(node, ast.Call)
        and _name(node.func) in verbs
        and bool(node.args)
        and _name(node.args[0]) == "Defect"
    )


def _is_open_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "OPEN"


def _values_writes(call: ast.Call) -> list[ast.AST]:
    """The value nodes a ``.values(...)`` call writes into ``resolution_status``."""
    written = [kw.value for kw in call.keywords if kw.arg == FIELD]
    for arg in call.args:
        if isinstance(arg, ast.Dict):
            written += [v for k, v in zip(arg.keys, arg.values) if isinstance(k, ast.Constant) and k.value == FIELD]
    return written


def offenders_in(source: str, label: str = "<snippet>") -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []

    def flag(node: ast.AST, why: str) -> None:
        found.append(f"{label}:{getattr(node, 'lineno', '?')}: {why}")

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            for leaf in ast.walk(target):
                if isinstance(leaf, ast.Attribute) and leaf.attr == FIELD and isinstance(leaf.ctx, ast.Store):
                    flag(node, "assigns .resolution_status")

        if not isinstance(node, ast.Call):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _RAW_SQL_WRITE.search(node.value):
                flag(node, "raw SQL sets resolution_status")
            continue

        if _name(node.func) == "setattr" and len(node.args) >= 2:
            key = node.args[1]
            if isinstance(key, ast.Constant) and key.value == FIELD:
                flag(node, 'setattr(..., "resolution_status", ...)')

        if _name(node.func) == "Defect":
            for kw in node.keywords:
                if kw.arg == FIELD and not _is_open_literal(kw.value):
                    flag(node, "Defect(resolution_status=<not 'OPEN'>)")

        if isinstance(node.func, ast.Attribute) and node.func.attr == "values":
            base = node.func.value
            # ``update(Defect).where(...).values(...)``: walk down the method chain to the statement.
            while isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute) and not _is_defect_statement(
                base, ("update", "insert", "pg_insert")
            ):
                base = base.func.value
            writes = _values_writes(node)
            if writes and _is_defect_statement(base, ("update",)):
                flag(node, "update(Defect).values(resolution_status=...)")
            elif writes and _is_defect_statement(base, ("insert", "pg_insert")):
                if not all(_is_open_literal(value) for value in writes):
                    flag(node, "insert(Defect).values(resolution_status=<not 'OPEN'>)")
    return found


def test_no_module_but_defect_status_changes_a_defect_status():
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel in ALLOWED:
            continue
        offenders += offenders_in(path.read_text(encoding="utf-8"), rel)
    assert not offenders, (
        "A defect status is changed outside app/services/defect_status.py. Call "
        "defect_status.apply_status(defect, target, now=...) instead: it is what keeps "
        "resolved_at honest (stamped on close, cleared on re-open).\n  " + "\n  ".join(offenders)
    )


def test_the_allowed_module_really_is_the_writer():
    source = (APP / "services" / "defect_status.py").read_text(encoding="utf-8")
    assert offenders_in(source), "defect_status.apply_status no longer assigns resolution_status"


# ── The scanner, on written snippets ────────────────────────────────────────


@pytest.mark.parametrize("snippet", [
    'defect.resolution_status = "INVALID"',
    'row.resolution_status: str = "RESOLVED"',
    'a.b.resolution_status = status',
    'x.resolution_status += "X"',
    'first, defect.resolution_status = 1, "RESOLVED"',
    'setattr(defect, "resolution_status", "RESOLVED")',
    'update(Defect).where(Defect.id == i).values(resolution_status="RESOLVED")',
    'sa.update(models.Defect).values({"resolution_status": "INVALID"})',
    'Defect(project_id=p, resolution_status="IN_PROGRESS")',
    'Defect(project_id=p, resolution_status=status)',
    'pg_insert(Defect).values(test_case_id=t, resolution_status=s)',
    'text("UPDATE defects SET resolution_status = :s WHERE id = :i")',
])
def test_the_scanner_flags(snippet):
    assert offenders_in(snippet), snippet


@pytest.mark.parametrize("snippet", [
    'Defect(project_id=p, resolution_status="OPEN")',
    'pg_insert(Defect).values(test_case_id=t, resolution_status="OPEN")',
    'insert(Defect).values({"resolution_status": "OPEN"})',
    'select(Defect).where(Defect.resolution_status == "OPEN")',
    'select(Defect).where(Defect.resolution_status.in_(ACTIVE_STATUSES))',
    'DefectIntakeResponse(resolution_status=defect.resolution_status)',
    'update(TestCase).values(status="x")',
    'status = defect.resolution_status',
    '{"metadata": {"resolution_status": d.resolution_status}}',
    'text("SELECT * FROM defects WHERE resolution_status = :s")',
])
def test_the_scanner_lets_pass(snippet):
    assert not offenders_in(snippet), snippet
