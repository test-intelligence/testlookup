"""EXEC-06: ``get_run_timing`` and ``get_execution_metrics`` over MCP.

"How long did the last checkout run take and what got slower?" had no MCP
answer: no tool read a duration beyond a run's summed time. These two read the
endpoints the UI's Timing tab / Trends > Execution time and the CLI read, and
return compact markdown beside the REST body. What is pinned here:

1. The contracts (name, description, input schema) against committed
   snapshots. ``UPDATE_SNAPSHOTS=1`` rewrites them.
2. The wire: the REST parameter names, lists repeated, ``None`` never sent,
   the caller's own bearer forwarded; the paths are routes the backend serves.
3. Honesty: a null figure is "not reported" -- never 0 -- and a block the
   server could not judge says why instead of reading as "nothing slower".
4. Errors: a structured result (404 for a run the caller cannot read; the
   analytics error contract's ``code``/``param``/``allowed`` on a 422).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).parent.parent
REPO_ROOT = MCP_DIR.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
import execution_format  # noqa: E402
from tools import analytics, runs  # noqa: E402

SNAPSHOTS = Path(__file__).parent / "snapshots"
BACKEND_ROUTER = REPO_ROOT / "backend" / "app" / "routers" / "execution_metrics.py"
RUN = "33333333-3333-4333-8333-333333333333"
PROJECT = "11111111-1111-4111-8111-111111111111"
R1 = "22222222-2222-4222-8222-222222222221"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools() -> dict:
    mcp = _FakeMCP()
    runs.register(mcp)
    analytics.register(mcp)
    return mcp.tools


def _run_body() -> dict:
    """``RunTimingResponse`` (EXEC-03 contract, section 1)."""
    return {
        "run_id": RUN, "project_id": PROJECT, "status": "FAILED", "build_number": "1234", "branch": "main",
        "created_at": "2026-10-14T10:00:00+00:00",
        "run": {"wall_clock_ms": 9000, "summed_ms": 17400, "parallelism": 1.93, "timing_source": "report",
                "exec_started_at": None, "exec_finished_at": None,
                "test_count": 12, "timed_count": 12, "duration_count": 12},
        "phases": {"measured": True, "reason": None, "tests_with_phases": 12, "setup_ms": 1440,
                   "call_ms": 14000, "teardown_ms": 960, "setup_share": 0.0883, "teardown_share": 0.0589},
        "suites": {"measured": True, "reason": None, "total": 2, "truncated": False, "items": [
            {"suite_name": "checkout", "suite_key": "checkout", "source": "report", "wall_ms": 5000,
             "summed_ms": 9100, "test_count": 6, "timed_count": 6, "passed": 5, "failed": 1, "broken": 0,
             "skipped": 0, "unknown": 0, "parallelism": 1.82},
            {"suite_name": "cart|`x`", "suite_key": "cart", "source": "derived_sum", "wall_ms": None,
             "summed_ms": 8300, "test_count": 6, "timed_count": 0, "passed": 6, "failed": 0, "broken": 0,
             "skipped": 0, "unknown": 0, "parallelism": None},
        ]},
        "slowest": {"limit": 10, "total": 12, "measured": True, "reason": None, "items": [
            {"test_id": "t1", "test_name": "checkout::test_pay", "suite_name": "checkout", "status": "FAILED",
             "duration_ms": 1800, "baseline": {"mean_ms": 900.0, "stddev_ms": 40.0, "p95_ms": 980.0,
                                              "sample_count": 14}, "baseline_ratio": 2.0},
            {"test_id": "t2", "test_name": "cart::test_empty", "suite_name": None, "status": "PASSED",
             "duration_ms": 0, "baseline": None, "baseline_ratio": None},
        ]},
        "workers": {"measured": True, "reason": None, "total": 2, "truncated": False, "lanes": []},
        "timeline": {"measured": True, "reason": None, "limit": 1, "total": 12, "truncated": True,
                     "origin": "2026-10-14T10:00:00+00:00", "end": "2026-10-14T10:00:09+00:00",
                     "items": [{"test_id": "t1"}]},
        "duration_findings": {
            "run_id": RUN, "measured": True, "reason": None,
            "spikes": [{"test_fingerprint": "fp1", "test_name": "checkout::test_pay", "suite_name": "checkout",
                        "observed_ms": 1800, "baseline_mean_ms": 900.0, "ratio": 2.0, "was_slow_before": False}],
            "spike_count": 1, "suites": [], "suite_regression_count": 0,
        },
    }


def _old_run_body() -> dict:
    body = _run_body()
    body["run"].update({"wall_clock_ms": None, "parallelism": None, "timing_source": "derived_sum",
                        "timed_count": 0})
    body["phases"] = {"measured": False, "reason": "report carried no setup/teardown split"}
    body["workers"] = {"measured": False, "reason": "report carried no timestamps", "total": 0, "lanes": []}
    body["duration_findings"] = {"run_id": RUN, "measured": False, "reason": "not enough history (4 of 10)",
                                 "spikes": [], "suites": []}
    return body


def _pct(n, p50, p95):
    if p50 is None:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "measured": False, "reason": "not reported"}
    return {"n": n, "p50_ms": p50, "p95_ms": p95, "measured": True, "reason": None}


def _metrics_body() -> dict:
    """``ExecutionMetricsResponse`` (EXEC-03 contract, section 3)."""
    buckets = [{"lower_ms": lo, "upper_ms": hi, "count": 0} for lo, hi in (
        (0, 10), (10, 25), (25, 50), (50, 100), (100, 250), (250, 500), (500, 1000), (1000, 2500),
        (2500, 5000), (5000, 10000), (10000, 30000), (30000, 60000), (60000, 300000), (300000, None))]
    buckets[0]["count"] = 40
    buckets[7]["count"] = 6
    buckets[-1]["count"] = 1
    return {
        "project_id": PROJECT, "days": 7, "branch": None, "measured": True, "reason": None,
        "totals": {"runs": 40, "wall_clock_ms": 360000, "wall_runs": 25, "summed_ms": 700000,
                   "summed_runs": 40, "parallelism": 1.9},
        "per_day": {"grain": "suite", "points": []},
        "suites": {"limit": 50, "total": 1, "truncated": False, "items": [
            {"suite_key": "checkout", "suite_name": "Checkout",
             "current": {"runs": 20, "wall": _pct(20, 5000.0, 6100.0), "summed": _pct(20, 9000.0, 9900.0)},
             "previous": {"runs": 18, "wall": _pct(18, 4444.4, 6000.0), "summed": _pct(18, 8000.0, 9900.0)},
             "wall_p50_change_pct": 12.5, "wall_p95_change_pct": 1.7,
             "summed_p50_change_pct": 12.5, "summed_p95_change_pct": 0.0},
        ]},
        "regressions": {
            "rule": {"recent_runs": 5, "baseline_runs": 20, "min_baseline_runs": 10, "ratio": 1.5,
                     "same_branch": True, "test_min_samples": 10, "test_ratio": 1.5},
            "suites": {"measured": True, "reason": None, "evaluated": 1, "considered": 1, "total": 1, "items": [
                {"suite_key": "checkout", "suite_name": "Checkout", "branch": "main", "metric": "wall",
                 "ratio": 2.0, "recent_median_ms": 10000.0, "baseline_median_ms": 5000.0,
                 "was_slow_before": False}]},
            "tests": {"measured": False, "reason": "no test in this project has a duration baseline of at least "
                      "10 samples", "considered": 0, "total": 0, "items": []},
        },
        "slowest_tests": {"limit": 20, "measured": True, "reason": None, "items": [
            {"test_fingerprint": "fp1", "test_name": "checkout::test_pay", "suite_name": "Checkout", "n": 6,
             "p50_ms": 1700.0, "p95_ms": 1800.0, "max_ms": 1810}]},
        "duration_histogram": {"measured": True, "reason": None, "n": 47, "buckets": buckets,
                               "p50_ms": 8.0, "p95_ms": 2400.0},
        "meta": {"scope": {"projects": [{"id": PROJECT, "name": "Checkout"}]}},
    }


@pytest.fixture
def backend(monkeypatch):
    """The real ``client.get`` over a MockTransport; records every request."""
    seen: list[httpx.Request] = []
    state = {"status": 200, "body": _run_body(), "headers": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(state["status"], json=state["body"], headers=state["headers"])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    return seen, state


# ── 1. the contracts ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("module", "name"), [(runs, "get_run_timing"), (analytics, "get_execution_metrics")])
def test_the_tool_contract_matches_its_snapshot(module, name):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("t")
    module.register(mcp)
    listed = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    actual = listed[name].model_dump(mode="json", exclude_none=True)
    snapshot = SNAPSHOTS / f"{name}.tool.json"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot.write_bytes((json.dumps(actual, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    assert actual == json.loads(snapshot.read_text(encoding="utf-8")), (
        f"{name}'s published contract changed. If intended, rerun with UPDATE_SNAPSHOTS=1 and review the diff."
    )


def test_the_arguments_are_the_rest_parameter_names():
    timing = json.loads((SNAPSHOTS / "get_run_timing.tool.json").read_text(encoding="utf-8"))["inputSchema"]
    assert set(timing["properties"]) == {"run_id", "slowest"} and timing["required"] == ["run_id"]
    metrics = json.loads((SNAPSHOTS / "get_execution_metrics.tool.json").read_text(encoding="utf-8"))
    schema = metrics["inputSchema"]
    assert set(schema["properties"]) == {"project_id", "days", "suite_name", "release_id", "branch", "top"}
    assert schema["required"] == ["project_id"]


@pytest.mark.parametrize("name", ["get_run_timing", "get_execution_metrics"])
def test_the_description_says_null_is_not_zero(name):
    doc = " ".join((_tools()[name].__doc__ or "").split())
    assert "NEVER 0" in doc
    assert "measured: false" in doc


# ── 2. the wire ─────────────────────────────────────────────────────────────


def test_the_paths_are_routes_the_backend_serves():
    """The tools call the EXEC-03 routes; a renamed route would 404 every call."""
    source = BACKEND_ROUTER.read_text(encoding="utf-8")
    assert 'prefix="/api/v1"' in source
    assert '"/runs/{run_id}/timing"' in source
    assert '"/analytics/execution-metrics"' in source
    tools_src = (MCP_DIR / "tools" / "runs.py").read_text(encoding="utf-8")
    assert 'f"/api/v1/runs/{run_id}/timing"' in tools_src
    assert '"/api/v1/analytics/execution-metrics"' in (MCP_DIR / "tools" / "analytics.py").read_text(encoding="utf-8")


def test_get_run_timing_asks_for_the_slowest_n_and_no_waterfall(backend):
    seen, _ = backend
    result = asyncio.run(_tools()["get_run_timing"](run_id=RUN))
    assert result["ok"] is True
    request = seen[0]
    assert request.method == "GET" and request.url.path == f"/api/v1/runs/{RUN}/timing"
    assert dict(request.url.params.multi_items()) == {"slowest": "10", "timeline_limit": "1"}
    assert request.headers["authorization"] == "Bearer caller-token"


@pytest.mark.parametrize(("asked", "sent"), [(0, "1"), (25, "25"), (500, "100")])
def test_slowest_is_clamped_to_the_routes_range(backend, asked, sent):
    seen, _ = backend
    asyncio.run(_tools()["get_run_timing"](run_id=RUN, slowest=asked))
    assert seen[0].url.params["slowest"] == sent


def test_get_execution_metrics_sends_the_rest_names_and_no_none(backend):
    seen, state = backend
    state["body"] = _metrics_body()
    asyncio.run(_tools()["get_execution_metrics"](
        project_id=PROJECT, days=7, suite_name=["checkout", "cart"], release_id=[R1],
    ))
    request = seen[0]
    assert request.url.path == "/api/v1/analytics/execution-metrics"
    assert sorted(request.url.params.multi_items()) == sorted([
        ("project_id", PROJECT), ("days", "7"), ("suite_name", "checkout"), ("suite_name", "cart"),
        ("release_id", R1),
    ])
    assert "None" not in str(request.url)
    asyncio.run(_tools()["get_execution_metrics"](project_id=PROJECT, branch="main", top=5))
    assert dict(seen[1].url.params.multi_items()) == {
        "project_id": PROJECT, "days": "30", "branch": "main", "top": "5",
    }


# ── 3. the answers ──────────────────────────────────────────────────────────


def test_run_timing_answers_with_the_kpi_line_and_the_body(backend):
    result = asyncio.run(_tools()["get_run_timing"](run_id=RUN))
    summary = result["summary"]
    assert "**Wall clock 9.0 s · Test time 17.4 s · Parallelism 1.9×**" in summary
    assert "| 1 | checkout::test_pay | checkout | FAILED | 1.8 s | 900 ms | 2.0× |" in summary
    assert "| 2 | cart::test_empty | — | PASSED | 0 ms | — | — |" in summary, "a measured 0 is 0 ms"
    assert "1 test(s) slower than their baseline" in summary
    assert result["has_duration_regressions"] is True
    timing = result["timing"]
    assert timing["run"] == _run_body()["run"], "nulls and figures reach the agent unchanged"
    assert "items" not in timing["timeline"] and timing["timeline"]["total"] == 12


def test_a_figure_the_report_did_not_carry_is_not_reported_never_zero(backend):
    _, state = backend
    state["body"] = _old_run_body()
    result = asyncio.run(_tools()["get_run_timing"](run_id=RUN))
    summary = result["summary"]
    assert "Wall clock not reported · Test time 17.4 s · Parallelism not reported" in summary
    assert "Wall clock 0" not in summary and "Parallelism 0" not in summary
    assert "Phases: report carried no setup/teardown split" in summary
    assert "Workers: report carried no timestamps" in summary
    assert "Not measured: not enough history (4 of 10)." in summary
    assert result["has_duration_regressions"] is False
    assert result["timing"]["run"]["wall_clock_ms"] is None


def test_a_hostile_name_cannot_break_the_table(backend):
    result = asyncio.run(_tools()["get_run_timing"](run_id=RUN))
    row = next(line for line in result["summary"].splitlines() if line.startswith("| cart"))
    assert row.startswith("| cart¦'x' | — |"), row
    assert row.count("|") == 7


def test_execution_metrics_answers_with_suite_figures_and_what_got_slower(backend):
    _, state = backend
    state["body"] = _metrics_body()
    result = asyncio.run(_tools()["get_execution_metrics"](project_id=PROJECT, days=7, suite_name=["checkout"]))
    assert result["ok"] is True
    assert result["metrics"] == _metrics_body(), "the REST body verbatim, meta included"
    summary = result["summary"]
    assert "| Checkout | 20 | 5.0 s | 6.1 s | +12.5% | 9.0 s | 9.9 s | +12.5% |" in summary
    assert "| Checkout | main | wall clock | 10.0 s | 5.0 s | 2.0× | yes |" in summary
    assert "Not measured: no test in this project has a duration baseline" in summary
    assert "Test durations (47 executions): p50 8 ms, p95 2.4 s." in summary
    assert "0 ms–10 ms: 40" in summary and "≥ 5 min 0 s: 1" in summary
    assert "25 ms–50 ms" not in summary, "empty buckets are left out of the sentence"


def test_a_list_cut_by_top_says_so(backend):
    _, state = backend
    body = _metrics_body()
    body["regressions"]["suites"]["total"] = 5
    state["body"] = body
    summary = asyncio.run(_tools()["get_execution_metrics"](project_id=PROJECT, top=1))["summary"]
    assert "_1 of 5 suite regressions shown (biggest ratio first)._" in summary
    state["body"] = _metrics_body()
    summary = asyncio.run(_tools()["get_execution_metrics"](project_id=PROJECT))["summary"]
    assert "suite regressions shown" not in summary, "a whole list carries no cut note"


def test_a_project_the_caller_cannot_read_is_not_measured(backend):
    _, state = backend
    body = _metrics_body()
    body.update({"project_id": None, "measured": False,
                 "reason": "No project in this scope is readable by the caller.",
                 "totals": {"runs": 0, "wall_clock_ms": None, "wall_runs": 0, "summed_ms": None,
                            "summed_runs": 0, "parallelism": None}})
    body["suites"]["items"] = []
    body["regressions"]["suites"] = {"measured": False, "reason": body["reason"], "items": []}
    body["slowest_tests"] = {"measured": False, "reason": body["reason"], "items": []}
    body["duration_histogram"] = {"measured": False, "reason": body["reason"], "n": 0, "buckets": []}
    state["body"] = body
    summary = asyncio.run(_tools()["get_execution_metrics"](project_id=PROJECT))["summary"]
    assert "Not measured: No project in this scope is readable by the caller." in summary
    assert "Wall clock not reported · Test time not reported · Parallelism not reported" in summary
    assert "No suite ran in the window." in summary


def test_the_markdown_never_writes_zero_for_null():
    assert execution_format.ms_phrase(None) == "not reported"
    assert execution_format.ms_cell(None) == "—"
    assert execution_format.ms_cell(0) == "0 ms"
    assert execution_format.format_ms(9000) == "9.0 s"
    assert execution_format.format_ms(125_000) == "2 min 5 s"
    assert execution_format.pct(None) == "—" and execution_format.pct(0.0) == "+0.0%"


# ── 4. errors ───────────────────────────────────────────────────────────────


def test_an_unreadable_run_is_a_structured_404(backend):
    _, state = backend
    state["status"], state["body"] = 404, {"detail": "Test run not found"}
    result = asyncio.run(_tools()["get_run_timing"](run_id=RUN))
    assert result == {"ok": False, "error": result["error"], "status_code": 404, "detail": "Test run not found"}


def test_a_422_carries_the_contracts_code_param_and_allowed(backend):
    _, state = backend
    state["status"] = 422
    state["body"] = {"code": "window_days_range", "param": "days", "message": "days must be 1-365",
                     "allowed": {"min": 1, "max": 365}, "request_id": "r", "detail": "days must be 1-365"}
    result = asyncio.run(_tools()["get_execution_metrics"](project_id=PROJECT, days=400))
    assert result["ok"] is False and result["status_code"] == 422
    assert (result["code"], result["param"], result["allowed"]) == ("window_days_range", "days",
                                                                   {"min": 1, "max": 365})


def test_a_connection_failure_is_a_structured_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    for name, kwargs in (("get_run_timing", {"run_id": RUN}), ("get_execution_metrics", {"project_id": PROJECT})):
        result = asyncio.run(_tools()[name](**copy.deepcopy(kwargs)))
        assert result["ok"] is False and "status_code" not in result, name
