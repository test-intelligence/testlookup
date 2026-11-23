"""Every MCP tool with a ``days`` window defaults to the REST API's 30 and says which window it used.

D-121 / S-C-7: ``list_test_runs``, ``get_dashboard_metrics``, ``get_test_trends``
and ``check_release_readiness`` defaulted to 7 days while the API, the app and
the other analytics tools default to 30, and ``list_test_runs`` printed
"(12 total)" with no window at all. An assistant reported "12 total" for a
project with 54 runs, or compared a 7-day figure with a 30-day one.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

RUNS_ROUTER = MCP_DIR.parent / "backend" / "app" / "routers" / "runs.py"
SUMMARY = MCP_DIR / "tests" / "snapshots" / "metrics_summary.policy_downgrade.json"
PROJECT = "11111111-1111-4111-8111-111111111111"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools(monkeypatch, answer):
    from tools import analysis, metrics, release, runs

    import client

    calls = []

    async def _get(path, params=None):
        calls.append((path, dict(params or {})))
        return answer(path, params) if callable(answer) else answer

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    for module in (runs, metrics, release, analysis):
        module.register(mcp)
    return mcp.tools, calls


def _run(tools, name, *args, **kwargs):
    return asyncio.run(tools[name](*args, **kwargs))


def _runs_page(total=54):
    return {
        "items": [{"id": "abcdef12-0000-4000-8000-000000000001", "build_number": "1201", "branch": "main",
                   "status": "PASSED", "pass_rate": 100.0, "total_tests": 10, "duration_ms": 1000,
                   "trigger_source": "ci", "start_time": "2026-11-20T10:00:00"}],
        "total": total, "page": 1, "pages": 3,
    }


# ── one default, the API's ──────────────────────────────────────────────────


def test_the_default_matches_the_rest_api():
    from tools.metrics import DEFAULT_WINDOW_DAYS

    assert DEFAULT_WINDOW_DAYS == 30
    if not RUNS_ROUTER.exists():  # pragma: no cover - MCP-only checkouts
        pytest.skip("backend not present in this checkout")
    tree = ast.parse(RUNS_ROUTER.read_text(encoding="utf-8"))
    defaults = [
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Query"
        and node.args and isinstance(node.args[0], ast.Constant)
        and any(kw.arg == "description" and "last N days" in str(getattr(kw.value, "value", "")) for kw in node.keywords)
    ]
    assert defaults == [DEFAULT_WINDOW_DAYS], "GET /api/v1/runs and the MCP tools must default to the same window"


def test_every_published_tool_with_a_days_window_has_the_same_default():
    import server
    from tools.metrics import DEFAULT_WINDOW_DAYS

    tools = asyncio.run(server.mcp.list_tools())
    defaults = {}
    for tool in tools:
        schema = tool.model_dump(mode="json", by_alias=True).get("inputSchema") or {}
        days = (schema.get("properties") or {}).get("days")
        if days is not None:
            defaults[tool.name] = days.get("default")
    assert {"list_test_runs", "get_dashboard_metrics", "get_test_trends", "check_release_readiness"} <= set(defaults)
    wrong = {name: d for name, d in defaults.items() if d != DEFAULT_WINDOW_DAYS}
    assert not wrong, f"tools whose days default is not {DEFAULT_WINDOW_DAYS}: {wrong}"


# ── each output names its window ────────────────────────────────────────────


def test_list_test_runs_sends_30_and_names_the_window(monkeypatch):
    tools, calls = _tools(monkeypatch, _runs_page())
    text = _run(tools, "list_test_runs", PROJECT)

    assert calls[0][1]["days"] == 30
    assert "## Test Runs in the last 30 days — Page 1/3 (54 total)" in text
    assert "54 run(s) in the last 30 days; pass days=0 for all time." in text


def test_list_test_runs_with_days_0_says_all_time(monkeypatch):
    tools, calls = _tools(monkeypatch, _runs_page())
    text = _run(tools, "list_test_runs", PROJECT, days=0)

    assert calls[0][1]["days"] == 0
    assert "## Test Runs of all time — Page 1/3 (54 total)" in text
    assert "pass days=0" not in text


def test_an_empty_run_list_names_the_window_too(monkeypatch):
    tools, _ = _tools(monkeypatch, {"items": [], "total": 0, "pages": 0})
    text = _run(tools, "list_test_runs", PROJECT, days=7)
    assert "in the last 7 days" in text and "Pass days=0 for all time." in text


def test_dashboard_and_readiness_default_to_30_days(monkeypatch):
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    tools, calls = _tools(monkeypatch, lambda path, params: summary if "summary" in path else [])

    dashboard = _run(tools, "get_dashboard_metrics", PROJECT)
    assert calls[0] == ("/api/v1/metrics/summary", {"project_id": PROJECT, "days": 30})
    assert "## Dashboard Metrics — Last 30 Days" in dashboard

    calls.clear()
    readiness = _run(tools, "check_release_readiness", PROJECT)
    assert ("/api/v1/metrics/summary", {"project_id": PROJECT, "days": 30}) in calls
    assert "Project readiness (last 30 days)" in readiness


def test_trends_default_to_30_days(monkeypatch):
    point = {"date": "2026-11-20", "passed": 9, "failed": 1, "broken": 0, "skipped": 0, "total": 10, "pass_rate": 90.0}
    tools, calls = _tools(monkeypatch, {"data": [point]})
    text = _run(tools, "get_test_trends", PROJECT)
    assert calls[0][1]["days"] == 30
    assert "## Test Trends — Last 30 Days" in text


def test_search_names_its_window(monkeypatch):
    tools, _ = _tools(monkeypatch, {
        "items": [{"test_name": "test_pay", "suite_name": "checkout", "status": "FAILED", "failure_count": 2}],
        "total": 1, "pages": 1,
    })
    assert "1 matches in the last 30 days" in _run(tools, "search_tests", "pay")

    tools, _ = _tools(monkeypatch, {"items": [], "total": 0, "pages": 0})
    assert "in the last 30 days" in _run(tools, "search_tests", "pay")
