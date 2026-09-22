"""VIZ-201 ratchet: the suite-match rule is spelled in ONE module.

``app/services/analytics_scope.py`` owns the effective-suite expression and
every clause that matches a requested suite against ``test_cases`` /
``test_runs``. Before VIZ-201 the rule existed in five copies (analytics,
summary report, suite history, dashboard metrics, test-management exports)
and they had drifted -- "two modules, one rule".

This scan fails when a module outside ``analytics_scope`` spells one of the
shapes below. The ``_LEGACY`` table holds the sites that predate the rule and
serve routes VIZ-201 did not convert (runs, run compare, search, the
test-management catalogue); each count may only go DOWN. Converting one of
those routes (VIZ-202) means deleting its entry, not raising it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
OWNER = "services/analytics_scope.py"

#: Shapes of a suite-match clause, raw SQL and Core.
SHAPES = {
    # The effective-suite expression itself (raw SQL).
    "effective_suite_sql": re.compile(
        r"trigger_source\s*=\s*'live_stream'\s*THEN\s+NULLIF\(\s*TRIM\(\s*[\w{}]+\.primary_suite_name",
        re.I | re.S,
    ),
    # A suite column normalised for matching (raw SQL).
    "lower_trim_suite_sql": re.compile(
        r"LOWER\(\s*TRIM\(\s*(?:COALESCE\(\s*)?(?:[\w{}]+\.)?(?:primary_suite_name|suite_name|effective_suite)\b",
        re.I,
    ),
    # LOWER() over an interpolated expression compared with a value -- the
    # shape ``f"LOWER({_effective_suite_sql()}) = :suite_name"`` (raw SQL; an
    # f-string hole is scanned as ``{}``).
    "lower_effective_suite_sql": re.compile(r"LOWER\(\{\}\)\s*(?:=|IN)\s", re.I),
    # A suite column normalised for matching (Core).
    "lower_trim_suite_core": re.compile(
        r"func\.lower\(\s*func\.trim\(\s*(?:func\.coalesce\(\s*)?(?:TestCase|TestRun)\.(?:primary_)?suite_name"
    ),
    # The effective-suite expression itself (Core).
    "effective_suite_core": re.compile(
        r"TestRun\.trigger_source\s*==\s*[\"']live_stream[\"']\s*,\s*func\.nullif\(\s*func\.trim\(\s*TestRun\.primary_suite_name",
        re.S,
    ),
}

#: The five former copies and the converted routers: none may define a clause.
CONVERTED = (
    "services/analytics_service.py",
    "services/metrics_service.py",
    "services/summary_report_service.py",
    "services/suite_history_service.py",
    "routers/test_management_exports.py",
    "routers/analytics.py",
    "routers/metrics.py",
    "routers/summary_report.py",
)

#: Pre-VIZ-201 sites on routes not yet converted. Counts may only decrease.
_LEGACY = {
    "routers/runs.py": 2,                       # /runs suite filter (run label)
    "services/global_search_service.py": 1,     # search result grouping
    "services/run_compare_service.py": 7,       # run compare (#559)
    "services/runs_service.py": 3,              # /runs list service
    "services/stream_service.py": 1,            # live stream suite lookup
    "services/test_management_service.py": 4,   # test-management (#560)
    "services/test_suite_service.py": 2,        # suite catalogue
}


_SQL_SHAPES = ("effective_suite_sql", "lower_trim_suite_sql", "lower_effective_suite_sql")


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                body[0] = ast.Pass()
    return tree


def _sql_text(tree: ast.AST) -> str:
    """Every string literal in the code (docstrings and comments excluded),
    f-string holes rendered as ``{}`` -- where SQL lives."""
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts.append("".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                for v in node.values
            ))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
    return "\n".join(parts)


def _count(path: Path) -> int:
    tree = _strip_docstrings(ast.parse(path.read_text(encoding="utf-8")))
    sql, code = _sql_text(tree), ast.unparse(tree)
    return sum(
        len(shape.findall(sql if name in _SQL_SHAPES else code))
        for name, shape in SHAPES.items()
    )


def _counts() -> dict[str, int]:
    out = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel == OWNER:
            continue
        n = _count(path)
        if n:
            out[rel] = n
    return out


def test_no_module_outside_analytics_scope_adds_a_suite_match_clause():
    counts = _counts()
    new = {rel: n for rel, n in counts.items() if n > _LEGACY.get(rel, 0)}
    assert not new, (
        "suite-match clause(s) spelled outside app/services/analytics_scope.py -- "
        f"use its builders instead: {new}"
    )


def test_the_converted_modules_hold_no_copy():
    counts = _counts()
    offenders = {rel: counts[rel] for rel in CONVERTED if rel in counts}
    assert not offenders, offenders


def test_the_legacy_table_only_shrinks():
    """A legacy entry whose file now holds fewer clauses must be lowered, so
    the table never keeps headroom a new copy could hide in."""
    counts = _counts()
    stale = {
        rel: (allowed, counts.get(rel, 0))
        for rel, allowed in _LEGACY.items()
        if counts.get(rel, 0) < allowed
    }
    assert not stale, f"lower these _LEGACY entries to the current count: {stale}"


def test_the_owner_really_defines_the_rule():
    """The scan is not vacuous: every shape matches the owner module."""
    tree = _strip_docstrings(ast.parse((APP / OWNER).read_text(encoding="utf-8")))
    sql, code = _sql_text(tree), ast.unparse(tree)
    from app.services.analytics_scope import effective_suite_sql

    assert SHAPES["effective_suite_sql"].search(effective_suite_sql())
    assert SHAPES["lower_trim_suite_sql"].search(sql)
    for name in ("lower_trim_suite_core", "effective_suite_core"):
        assert SHAPES[name].search(code), name
    # ...and the scan sees a copy that is added elsewhere.
    planted = ast.parse('q = f"AND LOWER(TRIM(tc.suite_name)) = :s"')
    assert SHAPES["lower_trim_suite_sql"].search(_sql_text(planted))


_NULL_TOLERANT = re.compile(r":\w+\s+IS\s+NULL\s+OR\b", re.I)


def test_no_null_tolerant_filter_in_the_converted_modules():
    """``(:x IS NULL OR col = :x)`` defeats ``ix_test_runs_project_release_created``
    under a generic plan; filters are conditional fragments instead. Only SQL
    string literals are scanned: the docstrings that explain the rule quote it."""
    for rel in (*CONVERTED, OWNER):
        tree = _strip_docstrings(ast.parse((APP / rel).read_text(encoding="utf-8")))
        assert not _NULL_TOLERANT.search(_sql_text(tree)), rel
