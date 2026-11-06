"""W34 STRAT-11: the CLI ``cases`` commands and the MCP test-management tools
send only what the Test Management routes read.

FastAPI ignores an undeclared query parameter, so a client that sends
``suite_name`` to the Excel export (which reads none) gets every suite's cases
in the workbook and exits 0 (``test_cli_and_mcp_wire_contract.py`` tells the
history). These compare each client's parameter names with the route's, read
from the assembled app, as ``test_execution_metrics_clients_wire.py`` does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_CASES = REPO_ROOT / "cli" / "testlookup_cli" / "commands" / "cases.py"
MCP_TOOLS = REPO_ROOT / "mcp" / "tools" / "test_management.py"

CASES = "/api/v1/test-management/cases"
CASE = "/api/v1/test-management/cases/{case_id}"
HISTORY = "/api/v1/test-management/cases/{case_id}/history"
EXCEL = "/api/v1/test-management/cases/export/excel"

LIST_FILTERS = {"project_id", "status", "test_type", "priority", "suite_name", "search", "owner",
                "automation", "requirement", "include_archived", "page", "size"}
EXPORT_FILTERS = {"project_id", "status", "test_type", "priority", "search", "owner", "automation", "requirement"}


def _query_names(path: str) -> set[str]:
    from fastapi.dependencies.utils import get_flat_dependant

    from app.main import app

    for route in app.routes:
        if getattr(route, "path", None) == path and "GET" in (getattr(route, "methods", None) or ()) \
                and getattr(route, "dependant", None):
            return {param.alias for param in get_flat_dependant(route.dependant).query_params}
    raise AssertionError(f"no GET route serves {path}")


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name} has no {name}")


def _dict_keys(func: ast.AST) -> set[str]:
    """Every string key a function puts in a dict literal or assigns by
    subscript (``params["suite_name"] = ...``)."""
    keys: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                and isinstance(node.slice, ast.Constant):
            keys.add(node.slice.value)
    return keys


def _params_literal(func: ast.AST) -> set[str]:
    """The keys of the ``params = {...}`` the tool builds."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(
            isinstance(target, ast.Name) and target.id == "params" for target in node.targets
        ):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("no params = {...} literal")


def _called_names(func: ast.AST) -> set[str]:
    return {node.func.id for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def _string_constants(func: ast.AST) -> set[str]:
    return {node.value for node in ast.walk(func) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_the_routes_declare_what_these_tests_assume():
    assert LIST_FILTERS <= _query_names(CASES)
    excel = _query_names(EXCEL)
    assert EXPORT_FILTERS <= excel
    # Why this file exists: the export reads no suite and no include-archived.
    assert not {"suite_name", "include_archived", "page", "size"} & excel
    assert _query_names(CASE) == set() and _query_names(HISTORY) == set()


def test_cli_cases_list_sends_only_what_the_route_reads():
    sent = _dict_keys(_function(CLI_CASES, "list_params"))
    assert sent == LIST_FILTERS
    assert sent <= _query_names(CASES)


def test_cli_cases_export_sends_only_what_the_excel_route_reads():
    sent = _dict_keys(_function(CLI_CASES, "export_params"))
    assert sent == EXPORT_FILTERS
    assert sent <= _query_names(EXCEL), (
        f"testlookup cases export sends {sorted(sent - _query_names(EXCEL))}, which the Excel route never reads "
        "-- the filter is dropped and the workbook holds everything"
    )


def test_the_cli_commands_build_their_query_with_those_helpers():
    """A command that built its own dict would bypass the two checks above."""
    listing, export = _function(CLI_CASES, "list_cases"), _function(CLI_CASES, "export_cases")
    assert "list_params" in _called_names(listing) and "export_params" in _called_names(export)
    assert EXCEL in _string_constants(export)
    for func in (listing, export):
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                assert not any(kw.arg == "params" and isinstance(kw.value, ast.Dict) for kw in node.keywords), (
                    f"{func.name} passes its own params={{...}}"
                )
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                assert getattr(node.value, "id", "") != "params", f"{func.name} adds a query key by hand"


def test_mcp_list_managed_test_cases_takes_only_what_the_route_reads():
    func = _function(MCP_TOOLS, "list_managed_test_cases")
    args = {a.arg for a in func.args.args + func.args.kwonlyargs}
    sent = _params_literal(func)
    assert args == sent, "every argument is sent, under its own name"
    assert args <= _query_names(CASES), (
        f"list_managed_test_cases offers {sorted(args - _query_names(CASES))}, which the route never reads"
    )


def test_the_by_id_reads_send_no_query():
    for func in (_function(MCP_TOOLS, "get_managed_test_case"), _function(CLI_CASES, "get_case"),
                 _function(CLI_CASES, "case_history")):
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("get", "request"):
                assert not any(kw.arg == "params" for kw in node.keywords), (
                    f"{func.name} sends a query to a route that reads none"
                )
