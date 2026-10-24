"""W12: the raw search text never leaves the request through a log line, an error or a metric.

A static scan (AST, not text) of every module that handles the query on the
server: ``routers/search.py``, the parser, the recents, the suggestions, the
global-search adapters and ``search_usage_service.py``. In those modules:

* no logger call takes the query (``q``, ``query``, ``text``, ``query_text``,
  ``stripped``, ``match_text``, ``parsed.text``, ``entry["q"]``) or a caught
  exception (its text can carry a statement's bound parameters, i.e. the
  query) -- ``type(exc).__name__`` is the only way an exception is logged;
* no ``HTTPException`` detail and no raised exception's message is built
  from the query;
* no metric ``.labels(...)`` value is built from it.

The behavioural halves (a sentinel query through each path, absent from every
captured log line) live in the route tests: ``test_search_recents.py``,
``test_search_similar_semantic_status.py``, ``test_search_semantic_fallback.py``,
``test_search_after_response_writes.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
MODULES = (
    "routers/search.py",
    "services/search_query_parser.py",
    "services/search_recents.py",
    "services/search_suggestions.py",
    "services/global_search_service.py",
    "services/search_usage_service.py",
)

QUERY_NAMES = frozenset({"q", "query", "text", "query_text", "stripped", "match_text"})
QUERY_ATTRS = frozenset({"text", "q", "query", "query_text"})
QUERY_KEYS = frozenset({"q", "query", "text"})
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "msg"})


def _exception_names(tree: ast.AST) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name}


def _offences(node: ast.AST, exception_names: set[str]) -> list[str]:
    """What in ``node``'s subtree carries the query (or a caught exception's text)."""
    found: list[str] = []

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name) and current.func.id == "type":
            return  # type(exc) / type(exc).__name__: the type only
        if isinstance(current, ast.Name) and (current.id in QUERY_NAMES or current.id in exception_names):
            found.append(current.id)
        elif isinstance(current, ast.Attribute) and current.attr in QUERY_ATTRS:
            found.append(f".{current.attr}")
        elif (
            isinstance(current, ast.Subscript)
            and isinstance(current.slice, ast.Constant)
            and current.slice.value in QUERY_KEYS
        ):
            found.append(f"[{current.slice.value!r}]")
        for child in ast.iter_child_nodes(current):
            visit(child)

    visit(node)
    return found


def _is_logger_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in LOG_METHODS:
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name):
        return "log" in receiver.id.lower()
    if isinstance(receiver, ast.Call):  # structlog.get_logger(...).warning(...)
        return "logger" in ast.unparse(receiver.func).lower()
    if isinstance(receiver, ast.Attribute):
        return "log" in receiver.attr.lower()
    return False


def _callee(call: ast.Call) -> str:
    return ast.unparse(call.func)


def violations(source: str, name: str = "<source>") -> list[str]:
    tree = ast.parse(source)
    exception_names = _exception_names(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            values: list[ast.AST] = []
            what = ""
            if _is_logger_call(node) or _callee(node) == "print":
                values, what = [*node.args, *(k.value for k in node.keywords)], "log call"
            elif _callee(node).endswith("HTTPException"):
                values = [k.value for k in node.keywords if k.arg == "detail"] + list(node.args[1:2])
                what = "HTTPException detail"
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "labels":
                values, what = [*node.args, *(k.value for k in node.keywords)], "metric label"
            for value in values:
                if found := _offences(value, exception_names):
                    out.append(f"{name}:{node.lineno}: {what} carries {sorted(set(found))}")
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for value in [*node.exc.args, *(k.value for k in node.exc.keywords)]:
                if found := _offences(value, set()):
                    out.append(f"{name}:{node.lineno}: an exception message carries {sorted(set(found))}")
    return out


@pytest.mark.parametrize("module", MODULES)
def test_no_query_text_leaves_through_a_log_an_error_or_a_label(module):
    path = APP / module
    assert path.exists(), f"{module} moved: update the scan"
    assert violations(path.read_text(encoding="utf-8"), module) == []


def test_the_scan_has_something_to_scan():
    """A scan that found no logger call anywhere would pass on a broken matcher."""
    source = (APP / "routers/search.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _is_logger_call(n)]
    assert len(calls) >= 3


@pytest.mark.parametrize(
    "snippet",
    [
        'logger.warning("search failed: %s", q)',
        'logger.info(f"searched {stripped}")',
        'logger.warning("x", query=parsed.text)',
        'logger.warning("x", entry=entry["q"])',
        'try:\n    pass\nexcept Exception as exc:\n    logger.warning("failed: %s", exc)',
        'try:\n    pass\nexcept Exception as err:\n    _slog.error("failed", error=str(err))',
        'raise HTTPException(status_code=422, detail=f"bad query {q}")',
        'raise ValueError("cannot parse " + query_text)',
        'counter.labels(scope=q).inc()',
        'print(stripped)',
    ],
)
def test_the_scan_catches_each_kind_of_leak(snippet):
    assert violations(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        'try:\n    pass\nexcept Exception as exc:\n    logger.warning("x error_type=%s", type(exc).__name__)',
        'logger.warning("search_recents_unavailable", error_type=type(exc).__name__)',
        'raise HTTPException(status_code=422, detail=QUERY_TOO_LONG)',
        'counter.labels(scope=scope_label).inc()',
    ],
)
def test_the_scan_allows_the_safe_forms(snippet):
    assert violations(snippet) == []
