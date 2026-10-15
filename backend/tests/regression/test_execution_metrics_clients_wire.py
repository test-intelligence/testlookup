"""EXEC-06: the CLI, MCP and CSV surfaces of the execution metrics send only
what the routes read.

FastAPI ignores an undeclared query parameter, so a client that sends
``suite=`` where the route reads ``suite_name`` gets the unfiltered answer and
exits 0 (``test_cli_and_mcp_wire_contract.py`` tells the history). These
compare each client's parameter names with the route's, read from the
assembled app, and pin that the CSV export takes the same scope as the JSON
read it exports.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_DIR = REPO_ROOT / "cli" / "testlookup_cli"
MCP_TOOLS_DIR = REPO_ROOT / "mcp" / "tools"

TIMING = "/api/v1/runs/{run_id}/timing"
TIMING_CSV = "/api/v1/runs/{run_id}/timing.csv"
METRICS = "/api/v1/analytics/execution-metrics"
METRICS_CSV = "/api/v1/analytics/execution-metrics.csv"


def _query_names(path: str) -> set[str]:
    from fastapi.dependencies.utils import get_flat_dependant

    from app.main import app

    for route in app.routes:
        if getattr(route, "path", None) == path and getattr(route, "dependant", None):
            return {param.alias for param in get_flat_dependant(route.dependant).query_params}
    raise AssertionError(f"no route serves {path}")


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name} has no {name}")


def _dict_keys(func: ast.AST, *, inside_call: str | None = None) -> set[str]:
    """Every string key a function puts in a dict literal or assigns by
    subscript (``params["top"] = ...``); with ``inside_call``, only the
    ``params={...}`` of calls to that attribute."""
    keys: set[str] = set()
    for node in ast.walk(func):
        if inside_call is not None:
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == inside_call:
                for kw in node.keywords:
                    if kw.arg == "params" and isinstance(kw.value, ast.Dict):
                        keys |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
            continue
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                and isinstance(node.slice, ast.Constant):
            keys.add(node.slice.value)
    return keys


def test_the_routes_declare_what_these_tests_assume():
    assert {"slowest", "timeline_limit"} <= _query_names(TIMING)
    assert {"project_id", "release_id", "suite_name", "days", "branch", "top"} <= _query_names(METRICS)


def test_the_csv_takes_the_json_routes_scope():
    """The file is filtered exactly as the read it exports: every filter the
    JSON route reads (``top`` sizes lists the file does not hold)."""
    json_names = _query_names(METRICS) - {"top", "suite_limit"}
    assert json_names <= _query_names(METRICS_CSV)
    assert _query_names(TIMING_CSV) == set(), "the run file takes no parameter"


def test_cli_runs_timing_sends_only_what_the_route_reads():
    sent = _dict_keys(_function(CLI_DIR / "commands" / "runs.py", "timing_params"))
    assert sent == {"slowest", "timeline_limit"}
    assert sent <= _query_names(TIMING)


def test_cli_analytics_execution_sends_only_what_the_route_reads():
    sent = _dict_keys(_function(CLI_DIR / "commands" / "analytics.py", "execution_params"))
    assert {"project_id", "release_id", "suite_name", "days", "branch", "top"} == sent
    assert sent <= _query_names(METRICS)


def _params_literal(func: ast.AST) -> set[str]:
    """The keys of the ``params = {...}`` the tool builds."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(
            isinstance(target, ast.Name) and target.id == "params" for target in node.targets
        ):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("no params = {...} literal")


def test_mcp_get_execution_metrics_takes_only_what_the_route_reads():
    func = _function(MCP_TOOLS_DIR / "analytics.py", "get_execution_metrics")
    args = {a.arg for a in func.args.args + func.args.kwonlyargs}
    sent = _params_literal(func)
    assert args == sent, "every argument is sent, under its own name"
    assert args <= _query_names(METRICS), (
        f"get_execution_metrics offers {sorted(args - _query_names(METRICS))}, which the route never reads"
    )


def test_mcp_get_run_timing_sends_only_what_the_route_reads():
    func = _function(MCP_TOOLS_DIR / "runs.py", "get_run_timing")
    sent = _dict_keys(func, inside_call="get")
    assert sent == {"slowest", "timeline_limit"}
    assert sent <= _query_names(TIMING)
