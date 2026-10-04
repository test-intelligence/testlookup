"""Regression: MCP surfaces say they are project-wide (S4c).

Why a test rather than a comment
--------------------------------
S4a gave five REST endpoints an optional ``release_id``. The MCP tools call
those same endpoints and were deliberately NOT given the parameter — release
scoping is a decided non-goal for MCP.

The consequence is that the same question now returns different numbers
depending on which surface asked it: a release-filtered dashboard and an MCP
tool reading the same endpoint disagree, legitimately. Nothing is wrong, but
nothing says so either — and the person most likely to hit it is an agent
reading a tool description, or someone comparing the two and concluding one is
broken.

A docstring is the only place an MCP client shows this, so the docstring IS the
feature here. This test exists because a docs-only guarantee has nothing else
holding it in place: the next person to edit these descriptions has no reason
to know the sentence is load-bearing.

VIZ-211 reversed the non-goal for chart data
---------------------------------------------
The product owner approved VIZ-211 (Phase C): MCP's ``get_chart_data`` and the
CLI's ``testlookup analytics`` read ``/api/v1/analytics/chart-data`` with
``release_id`` and ``suite_name``, so for chart data the three surfaces now
answer the same question. The four leaderboard tools and the flaky-tests
resource stay project-wide, and their descriptions now point at
``get_chart_data`` for a release-scoped number. The old CLI test ("the CLI
calls no analytics endpoint") pinned the decision being reversed; it is
replaced by its opposite: every CLI analytics call must be release-scoped.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_MCP = _REPO / "mcp"
_CLI = _REPO / "cli" / "testlookup_cli"
_ANALYTICS_PATH_RE = re.compile(r"""["'](/api/v1/analytics/[a-zA-Z0-9_\-/]*)["']""")

#: Endpoints that gained an optional release_id in S4a/S4b. An MCP surface
#: calling one of these can be release-narrowed in the UI but not here.
RELEASE_SCOPEABLE = (
    "/api/v1/analytics/flaky-tests",
    "/api/v1/analytics/failure-categories",
    "/api/v1/analytics/top-failing",
    "/api/v1/analytics/coverage",
)


def _callers(path: Path):
    """Yield (function_name, docstring) for functions calling a scopeable endpoint."""
    src = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"async def (\w+)\((?:.|\n)*?\"\"\"((?:.|\n)*?)\"\"\"((?:.|\n)*?)(?=\n    @|\n\ndef |\Z)", src):
        name, doc, body = m.group(1), m.group(2), m.group(3)
        if any(ep in body for ep in RELEASE_SCOPEABLE):
            yield path.name, name, doc


def test_every_mcp_surface_on_a_scopeable_endpoint_states_its_scope():
    """The divergence must be discoverable from the tool description itself.

    An agent choosing between tools sees only the description. If it does not
    say the answer is project-wide, the agent has no way to know its number is
    not comparable to a release-filtered one.
    """
    checked = 0
    for fname, func, doc in list(_callers(_MCP / "tools" / "analytics.py")) + list(
        _callers(_MCP / "resources" / "registry.py")
    ):
        checked += 1
        low = doc.lower()
        assert "project" in low and (
            "release" in low
        ), (
            f"{fname}:{func} calls a release-scopeable endpoint but its "
            f"docstring does not say the answer is project-wide and that the "
            f"UI can narrow it — so nothing tells a caller the two surfaces "
            f"legitimately disagree"
        )
    assert checked >= 4, (
        f"expected to find the MCP surfaces on release-scopeable endpoints, "
        f"found {checked} — if the parser stopped matching, this test is "
        f"passing without checking anything"
    )


def _cli_analytics_calls() -> dict[Path, set[str]]:
    """Every CLI module that names an analytics path, with the paths."""
    found: dict[Path, set[str]] = {}
    for path in sorted(_CLI.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        paths = set(_ANALYTICS_PATH_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
        if paths:
            found[path] = paths
    return found


def _route_query_names(path: str) -> set[str]:
    """The query parameters FastAPI binds for ``path``, by their wire name."""
    from fastapi.dependencies.utils import get_flat_dependant

    from app.main import app

    for route in app.routes:
        if getattr(route, "path", None) == path and getattr(route, "dependant", None):
            return {param.alias for param in get_flat_dependant(route.dependant).query_params}
    raise AssertionError(f"no route serves {path}")


def test_every_cli_analytics_call_is_release_scoped():
    """VIZ-211: the CLI's analytics commands answer per release, as the UI does.

    This replaced ``test_the_cli_has_no_divergence_to_document``, which failed
    the moment the CLI named an analytics path. The divergence it guarded
    against is now avoided the other way: a CLI analytics call must target a
    route that accepts ``release_id`` (and ``suite_name``), and the command
    must offer ``--release`` and send it as ``release_id``. A CLI analytics
    command that could not be release-scoped would print a project-wide number
    beside a release-filtered dashboard, with nothing saying why they differ.
    """
    calls = _cli_analytics_calls()
    assert calls, (
        "the CLI no longer calls any analytics endpoint -- if `testlookup "
        "analytics` moved, point this test at it; it is what holds VIZ-211"
    )
    for module, paths in calls.items():
        src = module.read_text(encoding="utf-8")
        rel = module.relative_to(_REPO).as_posix()
        assert '"--release"' in src and '"release_id"' in src, (
            f"{rel} calls {sorted(paths)} but does not offer --release or does "
            f"not send it as release_id"
        )
        for path in sorted(paths):
            accepted = _route_query_names(path)
            assert {"release_id", "suite_name"} <= accepted, (
                f"{rel} calls {path}, which cannot be narrowed to a release and "
                f"a suite (it takes {sorted(accepted)})"
            )


def _tool_docstring(name: str) -> str:
    tree = ast.parse((_MCP / "tools" / "analytics.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"mcp/tools/analytics.py has no tool {name}")


def test_get_chart_data_says_it_is_release_and_suite_scoped():
    """The other half of the reversal: the tool an agent should pick for a
    release-scoped number has to say so, in the only text it reads."""
    doc = " ".join(_tool_docstring("get_chart_data").split())
    assert "release- and suite-scoped" in doc
    assert "project-wide" in doc, "it must say what it covers without project_id"


def test_the_project_wide_surfaces_point_at_get_chart_data():
    """A caller that reached a project-wide tool learns where the scoped one is,
    and no description still calls release scoping a non-goal."""
    surfaces = list(_callers(_MCP / "tools" / "analytics.py")) + list(
        _callers(_MCP / "resources" / "registry.py")
    )
    assert len(surfaces) >= 5, f"expected the four tools and the resource, found {len(surfaces)}"
    for fname, func, doc in surfaces:
        flat = " ".join(doc.split())
        assert "get_chart_data" in flat, f"{fname}:{func} does not name get_chart_data"
        assert "non-goal" not in flat, f"{fname}:{func} still calls release scoping a non-goal"
