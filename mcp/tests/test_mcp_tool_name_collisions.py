"""
Regression: MCP tool names must be globally unique across all tool modules.

Bug (2026-06-09): two tool *names* were each defined in two different modules
with different signatures, and both modules are registered in ``server.py``.
Because FastMCP registers tools by function name, the later registration
silently shadowed the earlier one — so two implementations were dead code and
only 46 of the 48 declared tools were actually reachable:

  * ``check_release_readiness`` — ``release.py`` (project-level, ``project_id``)
    was shadowed by ``reports.py`` (run-level, ``run_id``).
  * ``search_tests`` — ``analysis.py`` (full filters) was shadowed by
    ``search.py`` (legacy two-arg).

Fix renamed the two shadowed-by duplicates to distinct names
(``check_run_release_readiness`` and ``search_test_cases``) so all 48 tools are
distinct and reachable.

These tests parse the tool modules statically (AST) — no FastMCP / backend
import required, matching the rest of ``test_mcp_server.py``.
"""
import ast
from collections import Counter
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"


def _tool_names_by_module() -> dict[str, list[str]]:
    """Map each tools/*.py module to the function names it decorates with @mcp.tool()."""
    out: dict[str, list[str]] = {}
    for py_file in sorted(TOOLS_DIR.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        names: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in node.decorator_list:
                # Match @mcp.tool() and @mcp.tool
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "tool"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "mcp"
                ):
                    names.append(node.name)
        if names:
            out[py_file.name] = names
    return out


def _all_tool_names() -> list[str]:
    names: list[str] = []
    for module_names in _tool_names_by_module().values():
        names.extend(module_names)
    return names


def test_no_duplicate_tool_names_across_modules():
    """The core regression: a tool name must never be declared twice (it would
    shadow the earlier registration and make one implementation dead code)."""
    names = _all_tool_names()
    dupes = {name: count for name, count in Counter(names).items() if count > 1}
    assert not dupes, (
        f"Duplicate @mcp.tool() names registered in more than one module: {dupes}. "
        "FastMCP registers by function name, so duplicates silently shadow each "
        "other. Rename one to a distinct name (see test docstring)."
    )


def test_former_collision_pairs_are_distinct_and_present():
    """Both members of each previously-colliding pair must coexist as distinct
    tools — guards against a regression that re-merges or drops either one."""
    names = set(_all_tool_names())
    for a, b in [
        ("check_release_readiness", "check_run_release_readiness"),
        ("search_tests", "search_test_cases"),
    ]:
        assert a in names, f"Expected tool '{a}' to be present"
        assert b in names, f"Expected tool '{b}' to be present"


def test_declared_tool_count_matches_distinct_count():
    """Every declared @mcp.tool() resolves to a distinct, reachable tool —
    the raw decorator count must equal the distinct-name count."""
    names = _all_tool_names()
    assert len(names) == len(set(names)), (
        f"{len(names)} @mcp.tool() decorators but only {len(set(names))} distinct "
        "names — some tools are shadowed and unreachable."
    )
