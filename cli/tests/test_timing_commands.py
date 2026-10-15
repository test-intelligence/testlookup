"""EXEC-06: ``testlookup runs timing`` and ``testlookup analytics execution``.

``runs timing <run-id>`` reads ``GET /api/v1/runs/{run_id}/timing`` and
``analytics execution`` reads ``GET /api/v1/analytics/execution-metrics`` --
the endpoints the UI's Timing tab and Trends > Execution time read, and MCP's
``get_run_timing`` / ``get_execution_metrics``. What is pinned here:

* the request: exactly one call, the REST parameter names, nothing sent for an
  unused option;
* the views (golden, byte for byte): the KPI line the story names ("Wall clock
  9.0 s · Test time 17.4 s · Parallelism 1.9×"), the suites, the slowest N, the
  duration regressions; a figure the report did not carry is "not reported" /
  ``-``, never 0;
* ``--json`` is the REST body verbatim;
* the exit codes: the stable HTTP mapping (``test_command_exit_codes.py``), a
  usage error is 2 with no request, and ``--fail-on-regression`` is 7 on a run
  with duration findings and 0 otherwise -- slowness never changes the exit
  code without it.

``UPDATE_GOLDEN=1`` rewrites the golden files; read the diff before committing.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import client, timing_render
from testlookup_cli.app import app
from testlookup_cli.errors import (
    EXIT_AUTH,
    EXIT_NOT_FOUND,
    EXIT_PERMISSION,
    EXIT_REGRESSION,
    EXIT_VALIDATION,
    CLIError,
    map_http_error,
)

CLI_DIR = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"
RUN = "33333333-3333-4333-8333-333333333333"
PROJECT = "11111111-1111-4111-8111-111111111111"
R1 = "22222222-2222-4222-8222-222222222221"
R2 = "22222222-2222-4222-8222-222222222222"
TIMING_PATH = f"/api/v1/runs/{RUN}/timing"
EXECUTION_PATH = "/api/v1/analytics/execution-metrics"


def _test(i: int, name: str, suite, status: str, duration, *, baseline=None, ratio=None) -> dict:
    return {
        "test_id": f"00000000-0000-4000-8000-{i:012d}", "test_name": name, "test_fingerprint": f"fp{i}",
        "suite_name": suite, "status": status, "duration_ms": duration,
        "started_at": "2026-10-14T10:00:00+00:00", "finished_at": "2026-10-14T10:00:01+00:00",
        "setup_ms": None, "call_ms": None, "teardown_ms": None, "worker_id": "gw0", "timing_source": "report",
        "baseline": baseline, "baseline_ratio": ratio,
    }


def _run_body() -> dict:
    """``RunTimingResponse`` (contract section 1) for the story's run."""
    slow = [
        _test(1, "checkout::test_pay", "checkout", "FAILED", 1800,
              baseline={"mean_ms": 900.0, "stddev_ms": 40.0, "p95_ms": 980.0, "sample_count": 14,
                        "last_observed_ms": 1800, "last_observed_at": None}, ratio=2.0),
        _test(2, "checkout::test_refund", "checkout", "PASSED", 1500),
        _test(3, "[bold red]cart::test_add[/]", "cart", "PASSED", 1200),
        _test(4, "cart::test_remove", "cart", "PASSED", 999),
        _test(5, "cart::test_empty", None, "PASSED", 0),
    ]
    return {
        "run_id": RUN, "project_id": PROJECT, "status": "FAILED", "build_number": "1234",
        "branch": "main", "created_at": "2026-10-14T10:00:00+00:00",
        "run": {
            "wall_clock_ms": 9000, "summed_ms": 17400, "parallelism": 1.93, "timing_source": "report",
            "exec_started_at": "2026-10-14T10:00:00+00:00", "exec_finished_at": "2026-10-14T10:00:09+00:00",
            "test_count": 12, "timed_count": 12, "duration_count": 12,
        },
        "phases": {"measured": True, "reason": None, "tests_with_phases": 12, "setup_ms": 1440,
                   "call_ms": 14000, "teardown_ms": 960, "setup_share": 0.0883, "teardown_share": 0.0589},
        "suites": {"measured": True, "reason": None, "total": 2, "truncated": False, "items": [
            {"suite_name": "checkout", "suite_key": "checkout", "source": "report",
             "started_at": None, "finished_at": None, "wall_ms": 5000, "summed_ms": 9100,
             "setup_ms": None, "teardown_ms": None, "test_count": 6, "timed_count": 6,
             "passed": 5, "failed": 1, "broken": 0, "skipped": 0, "unknown": 0, "parallelism": 1.82},
            {"suite_name": "cart", "suite_key": "cart", "source": "derived_sum",
             "started_at": None, "finished_at": None, "wall_ms": None, "summed_ms": 8300,
             "setup_ms": None, "teardown_ms": None, "test_count": 6, "timed_count": 0,
             "passed": 6, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0, "parallelism": None},
        ]},
        "slowest": {"limit": 10, "total": 12, "measured": True, "reason": None, "items": slow},
        "workers": {"measured": True, "reason": None, "total": 1, "truncated": False, "lanes": []},
        "timeline": {"measured": True, "reason": None, "limit": 1, "total": 12, "truncated": True,
                     "origin": None, "end": None, "items": []},
        "duration_findings": {
            "run_id": RUN, "schema_version": 1, "measured": True, "reason": None, "stored": True,
            "evaluated_at": "2026-10-14T10:00:10+00:00",
            "spikes": [{"test_fingerprint": "fp1", "test_name": "checkout::test_pay", "suite_name": "checkout",
                        "observed_ms": 1800, "baseline_mean_ms": 900.0, "baseline_stddev_ms": 40.0,
                        "baseline_p95_ms": 980.0, "sample_count": 14, "sigma": 22.5, "delta_ms": 900,
                        "ratio": 2.0, "previous_ms": 910, "was_slow_before": False, "new": True}],
            "spike_count": 1,
            "suites": [{"suite_name": "checkout", "suite_key": "checkout", "metric": "wall", "ratio": 1.8,
                        "recent_median_ms": 9000.0, "baseline_median_ms": 5000.0, "recent_runs": 5,
                        "baseline_runs": 20, "observed_ms": 9000, "was_slow_before": True, "new": False}],
            "suite_regression_count": 1,
            "test_check": {"measured": True}, "suite_check": {"measured": True}, "notified": {},
        },
    }


def _old_run_body() -> dict:
    """A JUnit run: no timestamps, so no wall clock, no phases, no lanes."""
    body = _run_body()
    body["run"].update({"wall_clock_ms": None, "parallelism": None, "timing_source": "derived_sum",
                        "exec_started_at": None, "exec_finished_at": None, "timed_count": 0})
    body["phases"] = {"measured": False, "reason": "report carried no setup/teardown split",
                      "tests_with_phases": 0, "setup_ms": None, "call_ms": None, "teardown_ms": None,
                      "setup_share": None, "teardown_share": None}
    body["suites"]["items"][0].update({"wall_ms": None, "parallelism": None, "source": "derived_sum"})
    body["duration_findings"] = {
        "run_id": RUN, "measured": False, "reason": "not enough history (4 of 10)", "stored": False,
        "spikes": [], "spike_count": 0, "suites": [], "suite_regression_count": 0,
    }
    return body


def _pct(n, p50, p95):
    if p50 is None:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "measured": False, "reason": "not reported"}
    return {"n": n, "p50_ms": p50, "p95_ms": p95, "measured": True, "reason": None}


def _execution_body() -> dict:
    """``ExecutionMetricsResponse`` (contract section 3)."""
    return {
        "project_id": PROJECT, "days": 30, "branch": "main",
        "window_start": "2026-09-15T00:00:00+00:00", "window_end": "2026-10-14T10:00:00+00:00",
        "previous_window_start": "2026-08-16T00:00:00+00:00",
        "measured": True, "reason": None,
        "totals": {"runs": 40, "wall_clock_ms": 360000, "wall_runs": 25, "summed_ms": 700000,
                   "summed_runs": 40, "parallelism": 1.9},
        "per_day": {"grain": "run", "points": []},
        "suites": {"limit": 50, "total": 2, "truncated": False, "items": [
            {"suite_key": "checkout", "suite_name": "Checkout",
             "current": {"runs": 20, "wall": _pct(20, 5000.0, 6100.0), "summed": _pct(20, 9000.0, 9900.0)},
             "previous": {"runs": 18, "wall": _pct(18, 4444.4, 6000.0), "summed": _pct(18, 8000.0, 9900.0)},
             "wall_p50_change_pct": 12.5, "wall_p95_change_pct": 1.7,
             "summed_p50_change_pct": 12.5, "summed_p95_change_pct": 0.0},
            {"suite_key": "cart", "suite_name": "Cart",
             "current": {"runs": 20, "wall": _pct(0, None, None), "summed": _pct(20, 800.0, 950.0)},
             "previous": {"runs": 0, "wall": _pct(0, None, None), "summed": _pct(0, None, None)},
             "wall_p50_change_pct": None, "wall_p95_change_pct": None,
             "summed_p50_change_pct": None, "summed_p95_change_pct": None},
        ]},
        "regressions": {
            "rule": {"recent_runs": 5, "baseline_runs": 20, "min_baseline_runs": 10, "ratio": 1.5,
                     "same_branch": True, "test_min_samples": 10, "test_ratio": 1.5},
            "suites": {"measured": True, "reason": None, "evaluated": 2, "considered": 2, "total": 1, "items": [
                {"suite_key": "checkout", "suite_name": "Checkout", "branch": "main", "metric": "wall",
                 "ratio": 2.0, "recent_median_ms": 10000.0, "baseline_median_ms": 5000.0, "recent_runs": 5,
                 "baseline_runs": 20, "observed_ms": 10200, "was_slow_before": False,
                 "latest_run_id": RUN, "latest_run_at": "2026-10-14T10:00:00+00:00"},
            ]},
            "tests": {"measured": False, "reason": "no test in this project has a duration baseline of at "
                      "least 10 samples", "considered": 0, "total": 0, "items": []},
        },
        "slowest_tests": {"limit": 20, "measured": True, "reason": None, "items": [
            {"test_fingerprint": "fp1", "test_name": "checkout::test_pay", "suite_name": "Checkout",
             "n": 6, "p50_ms": 1700.0, "p95_ms": 1800.0, "max_ms": 1810},
        ]},
        "duration_histogram": {"measured": True, "reason": None, "n": 6, "buckets": [],
                               "p50_ms": 1700.0, "p95_ms": 1800.0},
        "meta": {
            "scope": {"projects": [{"id": PROJECT, "name": "Checkout"}], "releases": [], "suites": [],
                      "window": {"from": "2026-09-15T00:00:00Z", "to": "2026-10-14T10:00:00Z", "days": 30}},
            "generated_at": "2026-10-14T10:00:00Z",
        },
    }


@pytest.fixture
def api(monkeypatch):
    calls: list[dict] = []
    state: dict = {"body": _run_body(), "error": None}

    async def _request(method, path, *, profile_name=None, params=None, json_body=None, **_kw):
        calls.append({"method": method, "path": path, "params": params})
        if state["error"] is not None:
            raise state["error"]
        return state["body"]

    monkeypatch.setattr(client, "request", _request)
    return calls, state


def _run(*args, env=None):
    return CliRunner().invoke(app, list(args), env=env)


def _golden(name: str, actual: bytes) -> bytes:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(actual)
    return path.read_bytes()


def _trimmed(stdout: str) -> bytes:
    # Rich pads centred titles and captions with spaces; trimmed on the right
    # so an editor that strips trailing blanks cannot break the golden file.
    return "".join(line.rstrip() + "\n" for line in stdout.splitlines()).encode("utf-8")


# ── runs timing: the request ────────────────────────────────────────────────


def test_runs_timing_asks_for_the_slowest_ten_and_no_timeline(api):
    calls, _ = api
    result = _run("runs", "timing", RUN)
    assert result.exit_code == 0, result.output
    assert calls == [{"method": "GET", "path": TIMING_PATH, "params": {"slowest": 10, "timeline_limit": 1}}]


def test_runs_timing_json_is_the_rest_body_with_the_routes_timeline(api):
    calls, _ = api
    for argv in (("--json",), ("-o", "json")):
        result = _run("runs", "timing", RUN, "--slowest", "5", *argv)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == _run_body()
    assert [c["params"] for c in calls] == [{"slowest": 5}, {"slowest": 5}]


@pytest.mark.parametrize("argv", [("--slowest", "0"), ("--slowest", "101"), ("-o", "csv")])
def test_a_usage_error_exits_2_without_a_request(api, argv):
    calls, _ = api
    result = _run("runs", "timing", RUN, *argv)
    assert result.exit_code == EXIT_VALIDATION
    assert calls == []


# ── runs timing: the view ───────────────────────────────────────────────────


def test_runs_timing_prints_the_kpi_line(api):
    result = _run("runs", "timing", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    assert "Wall clock 9.0 s · Test time 17.4 s · Parallelism 1.9×" in result.stdout
    assert "Setup 1.4 s · Test body 14.0 s · Teardown 960 ms" in result.stdout


def test_runs_timing_table_matches_the_golden(api):
    result = _run("runs", "timing", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    actual = _trimmed(result.stdout)
    assert actual == _golden("runs_timing_table.txt", actual)


def test_slowest_n_lists_n_tests(api):
    _, state = api
    result = _run("runs", "timing", RUN, "--slowest", "2", env={"COLUMNS": "120"})
    assert "Slowest 2 of 12 tests" in result.stdout
    assert "checkout::test_refund" in result.stdout and "cart::test_remove" not in result.stdout


def test_a_figure_the_report_did_not_carry_is_not_reported_never_zero(api):
    _, state = api
    state["body"] = _old_run_body()
    result = _run("runs", "timing", RUN, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Wall clock not reported · Test time 17.4 s · Parallelism not reported" in out
    assert "Phases: report carried no setup/teardown split" in out
    assert "Duration regressions: not measured (not enough history (4 of 10))" in out
    assert "Wall clock 0" not in out and "Parallelism 0" not in out
    checkout = next(line for line in out.splitlines() if line.startswith("│checkout"))
    cells = [cell.strip() for cell in checkout.strip("│").split("│")]
    assert cells[1:4] == ["-", "9.1 s", "-"], "no wall clock, so no parallelism: '-', never 0"


def test_a_measured_zero_is_zero(api):
    result = _run("runs", "timing", RUN, env={"COLUMNS": "120"})
    line = next(line for line in result.stdout.splitlines() if "cart::test_empty" in line)
    assert "0 ms" in line


def test_a_test_name_is_text_never_rich_markup(api):
    result = _run("runs", "timing", RUN, env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.output
    assert "[bold red]cart::test_add[/]" in result.stdout


def test_a_run_not_timed_yet_says_so(api):
    _, state = api
    body = _old_run_body()
    body["run"]["timing_source"] = None
    state["body"] = body
    result = _run("runs", "timing", RUN, env={"COLUMNS": "120"})
    assert "Timing: not computed for this run yet" in result.stdout


# ── runs timing: --fail-on-regression ───────────────────────────────────────


def test_fail_on_regression_exits_7_on_a_run_with_duration_findings(api):
    result = _run("runs", "timing", RUN, "--fail-on-regression")
    assert result.exit_code == EXIT_REGRESSION == 7, result.output
    assert "duration regressions" in result.stderr
    assert "Wall clock 9.0 s" in result.stdout, "the view is still printed"


def test_without_the_flag_slowness_never_changes_the_exit_code(api):
    assert _run("runs", "timing", RUN).exit_code == 0
    assert _run("runs", "timing", RUN, "--json").exit_code == 0


def test_fail_on_regression_exits_0_on_a_normal_run(api):
    _, state = api
    body = _run_body()
    body["duration_findings"].update({"spikes": [], "spike_count": 0, "suites": [], "suite_regression_count": 0})
    state["body"] = body
    result = _run("runs", "timing", RUN, "--fail-on-regression")
    assert result.exit_code == 0, result.output
    assert "Duration regressions: 0 slower tests, 0 slower suites" in result.stdout


def test_fail_on_regression_also_gates_the_json_output(api):
    result = _run("runs", "timing", RUN, "--json", "--fail-on-regression")
    assert result.exit_code == EXIT_REGRESSION
    assert json.loads(result.stdout)["duration_findings"]["spike_count"] == 1


@pytest.mark.parametrize("findings", [
    None,
    {"measured": False, "reason": "not enough history (4 of 10)", "spikes": [], "suites": []},
])
def test_unjudged_slowness_does_not_fail_ci_but_says_so(api, findings):
    _, state = api
    body = _run_body()
    body["duration_findings"] = findings
    state["body"] = body
    result = _run("runs", "timing", RUN, "--fail-on-regression")
    assert result.exit_code == 0, result.output
    assert "could not be judged" in result.stderr


def test_has_duration_findings_reads_the_counts_and_the_lists():
    assert timing_render.has_duration_findings({"measured": True, "spike_count": 1})
    assert timing_render.has_duration_findings({"measured": True, "suites": [{"suite_key": "a"}]})
    assert not timing_render.has_duration_findings({"measured": True, "spike_count": 0, "spikes": []})
    assert not timing_render.has_duration_findings({"measured": False, "spike_count": 3})
    assert not timing_render.has_duration_findings(None)


# ── analytics execution ─────────────────────────────────────────────────────


@pytest.fixture
def execution_api(api):
    calls, state = api
    state["body"] = _execution_body()
    return calls, state


def test_execution_sends_exactly_the_scope_it_was_given(execution_api):
    calls, _ = execution_api
    result = _run(
        "analytics", "execution", "--project", PROJECT, "--release", R1, "--release", R2,
        "--suite", "checkout", "--days", "14", "--branch", "main", "--top", "5", "--json",
    )
    assert result.exit_code == 0, result.output
    assert calls == [{"method": "GET", "path": EXECUTION_PATH, "params": {
        "project_id": PROJECT, "days": 14, "release_id": [R1, R2], "suite_name": ["checkout"],
        "branch": "main", "top": 5,
    }}]
    assert json.loads(result.stdout) == _execution_body()


def test_execution_sends_nothing_for_an_unused_option(execution_api):
    calls, _ = execution_api
    assert _run("analytics", "execution", "-p", PROJECT, "-o", "json").exit_code == 0
    assert calls[0]["params"] == {"project_id": PROJECT, "days": 30}


def test_execution_needs_a_project(execution_api):
    calls, _ = execution_api
    result = _run("analytics", "execution")
    assert result.exit_code == EXIT_VALIDATION
    assert calls == []


def test_execution_table_matches_the_golden(execution_api):
    result = _run("analytics", "execution", "-p", PROJECT, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    actual = _trimmed(result.stdout)
    assert actual == _golden("analytics_execution_table.txt", actual)


def test_execution_shows_a_missing_wall_clock_as_not_reported(execution_api):
    result = _run("analytics", "execution", "-p", PROJECT, env={"COLUMNS": "120"})
    cart = next(line for line in result.stdout.splitlines() if line.startswith("│Cart"))
    cells = [cell.strip() for cell in cart.strip("│").split("│")]
    assert cells[2:5] == ["-", "-", "-"], cells
    assert cells[5] == "800 ms"
    assert "Test regressions: not measured (no test in this project has a duration baseline" in result.stdout


def test_a_list_cut_by_top_says_so(execution_api):
    """``--top 1`` keeps one regression of five: the title must not read as
    "the only one" (``total`` is the count before the cut)."""
    _, state = execution_api
    body = _execution_body()
    body["regressions"]["suites"]["total"] = 5
    state["body"] = body
    result = _run("analytics", "execution", "-p", PROJECT, "--top", "1", env={"COLUMNS": "120"})
    assert "Suite regressions (1 of 5)" in result.stdout


def test_a_denied_or_empty_scope_says_not_measured(execution_api):
    _, state = execution_api
    body = _execution_body()
    body.update({"project_id": None, "measured": False, "reason": "No project in this scope is readable by "
                 "the caller.", "totals": {"runs": 0, "wall_clock_ms": None, "wall_runs": 0,
                                           "summed_ms": None, "summed_runs": 0, "parallelism": None}})
    body["suites"]["items"] = []
    state["body"] = body
    result = _run("analytics", "execution", "-p", PROJECT, env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    assert "Not measured: No project in this scope is readable by the caller." in result.stdout
    assert "Runs 0 · Wall clock not reported · Test time not reported" in result.stdout


def test_the_wire_repeats_list_keys_and_sends_the_api_key(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_execution_body())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        client.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(client, "get_profile", lambda _name=None: {
        "url": "http://testserver", "auth_type": "api_key", "api_key": "tl_test_key",
    })
    result = _run("analytics", "execution", "-p", PROJECT, "--release", R1, "--release", R2,
                  "--suite", "checkout", "-o", "json")
    assert result.exit_code == 0, result.output
    assert seen[0].url.path == EXECUTION_PATH
    assert sorted(seen[0].url.params.multi_items()) == sorted([
        ("project_id", PROJECT), ("days", "30"), ("release_id", R1), ("release_id", R2),
        ("suite_name", "checkout"),
    ])
    assert seen[0].headers["x-api-key"] == "tl_test_key"


# ── exit codes (the stable HTTP mapping) ────────────────────────────────────


@pytest.mark.parametrize("argv", [
    ("runs", "timing", RUN),
    ("analytics", "execution", "--project", PROJECT),
])
@pytest.mark.parametrize(("status", "code"), [
    (404, EXIT_NOT_FOUND), (403, EXIT_PERMISSION), (401, EXIT_AUTH), (422, EXIT_VALIDATION), (500, 1),
])
def test_http_errors_keep_the_stable_exit_codes(api, argv, status, code):
    _, state = api
    state["error"] = map_http_error(status, "sentinel")
    result = _run(*argv)
    assert result.exit_code == code, result.output
    assert "sentinel" in result.stderr


@pytest.mark.parametrize("argv", [
    ("runs", "timing", RUN, "--fail-on-regression"),
    ("analytics", "execution", "--project", PROJECT),
])
def test_a_client_error_code_is_preserved(api, argv):
    """``test_command_exit_codes.py``'s convention: whatever code the shared
    client mapped is the command's exit code (a 404 is never a regression)."""
    _, state = api
    state["error"] = CLIError("sentinel", EXIT_NOT_FOUND)
    result = _run(*argv)
    assert result.exit_code == EXIT_NOT_FOUND


def test_a_429_says_it_is_rate_limited(execution_api):
    _, state = execution_api
    state["error"] = map_http_error(429, "Too many requests")
    result = _run("analytics", "execution", "-p", PROJECT)
    assert result.exit_code == 1
    assert "Rate limited" in result.stderr


def test_the_regression_code_is_not_any_http_code():
    assert EXIT_REGRESSION not in {EXIT_AUTH, EXIT_NOT_FOUND, EXIT_PERMISSION, EXIT_VALIDATION, 0, 1, 6}


# ── a console that cannot encode the glyphs ─────────────────────────────────

_SNIPPET = """
import json, sys
from testlookup_cli import client
from testlookup_cli.app import app
body = json.loads(open(sys.argv[1], encoding="utf-8").read())
async def _request(*args, **kwargs):
    return body
client.request = _request
app(sys.argv[2:])
"""


def _cp1252(tmp_path, body: dict, *argv: str) -> subprocess.CompletedProcess:
    body_file = tmp_path / "body.json"
    body_file.write_text(json.dumps(body), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET, str(body_file), *argv],
        capture_output=True,
        # The CLI package directory first, so the subprocess imports THIS
        # source tree and not an installed copy of the CLI.
        cwd=str(CLI_DIR),
        env={**os.environ, "PYTHONIOENCODING": "cp1252", "COLUMNS": "120"},
    )


def test_a_cp1252_console_gets_a_view_not_a_crash(tmp_path):
    body = _run_body()
    body["slowest"]["items"][1]["suite_name"] = "決済"
    proc = _cp1252(tmp_path, body, "runs", "timing", RUN)
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err[-800:]
    assert "UnicodeEncodeError" not in err
    out = proc.stdout.decode("cp1252")
    assert "Parallelism 1.9×" in out, "× and · exist in cp1252"
    assert "??" in out, "the CJK suite name degrades to ?, the command does not die"


def test_an_ascii_console_gets_ascii_separators(tmp_path):
    body_file = tmp_path / "body.json"
    body_file.write_text(json.dumps(copy.deepcopy(_run_body())), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _SNIPPET, str(body_file), "runs", "timing", RUN],
        capture_output=True, cwd=str(CLI_DIR),
        env={**os.environ, "PYTHONIOENCODING": "ascii", "COLUMNS": "120"},
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-800:]
    assert b"Wall clock 9.0 s | Test time 17.4 s | Parallelism 1.9x" in proc.stdout
