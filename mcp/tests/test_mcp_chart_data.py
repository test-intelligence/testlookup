"""VIZ-211: ``get_chart_data`` is the REST chart-data endpoint, over MCP.

An agent asking "what was the pass rate of release R in suite S?" used to get
a project-wide number from a leaderboard tool, because release scoping was an
MCP non-goal. ``get_chart_data`` reverses that for chart data: it takes the
REST parameter names, sends them unchanged and returns the body unchanged, so
an agent, the CLI and the UI's charts read one series for one scope.

What is pinned here:

1. The tool's contract (name, description, input and output schema) against a
   committed snapshot. ``UPDATE_SNAPSHOTS=1`` rewrites it.
2. The wire: repeated ``release_id`` and ``group_by``, no ``None`` sent, and
   the caller's own bearer forwarded.
3. The body: the C3 series plus the C2 ``meta`` from the shared contract
   fixtures, returned deep-equal (resources strip ``meta``; tools keep it).
4. The error path: the analytics error contract's ``code``/``param``/
   ``allowed`` reach the agent, and a 429 carries ``retry_after``.

That ``CHART_METRICS``/``CHART_DIMENSIONS`` match the server is checked from
the backend suite (``test_cli_and_mcp_wire_contract.py``): this job has no
backend to import.
"""
from __future__ import annotations

import asyncio
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
from tools import analytics  # noqa: E402

SNAPSHOT = Path(__file__).parent / "snapshots" / "get_chart_data.tool.json"
FIXTURES = REPO_ROOT / "contracts" / "viz" / "fixtures"
PATH = "/api/v1/analytics/chart-data"
R1 = "22222222-2222-4222-8222-222222222221"
R2 = "22222222-2222-4222-8222-222222222222"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _fixture(*parts: str) -> dict:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))["payload"]


def _body() -> dict:
    """A real chart-data response shape, from the shared contract fixtures."""
    return {
        **_fixture("chart_series", "valid", "series_multi.json"),
        "meta": _fixture("envelope", "valid", "filtered.json"),
    }


@pytest.fixture
def backend(monkeypatch):
    """The real ``client.get`` over a MockTransport; records every request."""
    seen: list[httpx.Request] = []
    state = {"status": 200, "body": _body(), "headers": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(state["status"], json=state["body"], headers=state["headers"])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    return seen, state


def _tool():
    mcp = _FakeMCP()
    analytics.register(mcp)
    return mcp.tools["get_chart_data"]


# ── 1. the contract ─────────────────────────────────────────────────────────


def test_the_tool_contract_matches_its_snapshot():
    """Name, description and schemas are the agent's whole view of the tool.

    A changed argument name breaks every agent prompt written against it, and
    the REST parity test can only see the source, not what FastMCP publishes.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("t")
    analytics.register(mcp)
    listed = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    actual = listed["get_chart_data"].model_dump(mode="json", exclude_none=True)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_bytes((json.dumps(actual, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert actual == expected, (
        "get_chart_data's published contract changed. If that is intended, "
        "rerun with UPDATE_SNAPSHOTS=1 and review the diff."
    )


def test_the_arguments_are_the_rest_parameter_names():
    """The backend parity test compares these names to the route's; pin the
    MCP side of it too, where a rename would be noticed first."""
    schema = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["inputSchema"]
    assert set(schema["properties"]) == {
        "group_by", "metric", "project_id", "release_id", "suite_name", "days", "top_n",
    }
    assert schema["required"] == ["group_by"]


def test_the_docstring_names_every_allowed_value():
    """An agent picks a metric from the description, so it must list them all."""
    doc = " ".join((_tool().__doc__ or "").split())
    missing = [name for name in analytics.CHART_METRICS + analytics.CHART_DIMENSIONS
               if name not in doc]
    assert not missing, f"get_chart_data's docstring does not name {missing}"
    assert "release- and suite-scoped" in doc
    assert list(analytics.CHART_METRICS) == sorted(set(analytics.CHART_METRICS))
    assert list(analytics.CHART_DIMENSIONS) == sorted(set(analytics.CHART_DIMENSIONS))


# ── 2. the wire ─────────────────────────────────────────────────────────────


def test_repeated_filters_go_out_as_repeated_keys_with_the_callers_bearer(backend):
    seen, _ = backend
    asyncio.run(_tool()(
        group_by=["day"], metric="pass_rate", project_id="p1",
        release_id=[R1, R2], suite_name=["checkout"], days=90,
    ))
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET" and request.url.path == PATH
    assert sorted(request.url.params.multi_items()) == sorted([
        ("metric", "pass_rate"),
        ("group_by", "day"),
        ("project_id", "p1"),
        ("release_id", R1),
        ("release_id", R2),
        ("suite_name", "checkout"),
        ("days", "90"),
    ])
    assert request.headers["authorization"] == "Bearer caller-token"


def test_unset_arguments_are_not_sent(backend):
    """``None`` must not reach the server as the string "None"; the route's
    own defaults apply instead."""
    seen, _ = backend
    asyncio.run(_tool()(group_by=["day", "suite"], top_n=5))
    params = seen[0].url.params
    assert params.get_list("group_by") == ["day", "suite"], "order is significant"
    assert dict(params.multi_items()) == {
        "metric": "executions", "group_by": "suite", "days": "30", "top_n": "5",
    }
    assert "None" not in str(seen[0].url)


# ── 3. the body ─────────────────────────────────────────────────────────────


def test_the_rest_body_comes_back_verbatim_with_meta(backend):
    result = asyncio.run(_tool()(group_by=["day", "suite"], metric="pass_rate"))
    assert result == _body()
    assert result["series"][0]["points"][1]["y"] is None, "a gap stays a gap, never 0"
    assert result["meta"]["scope"]["releases"][0]["name"] == "2026.09"


# ── 4. errors ───────────────────────────────────────────────────────────────


def test_a_422_carries_the_contracts_code_param_and_allowed(backend):
    _, state = backend
    state["status"] = 422
    state["body"] = {
        "code": "window_days_range", "param": "days",
        "message": "days must be 1-365", "allowed": {"min": 1, "max": 365},
        "request_id": "req-1", "detail": "days must be 1-365",
    }
    result = asyncio.run(_tool()(group_by=["day"], days=400))
    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["detail"] == "days must be 1-365"
    assert result["code"] == "window_days_range"
    assert result["param"] == "days"
    assert result["allowed"] == {"min": 1, "max": 365}


def test_a_forbidden_release_is_a_403_with_no_data(backend):
    _, state = backend
    state["status"] = 403
    state["body"] = {"code": "forbidden", "message": "No access", "request_id": "r",
                     "detail": "No access"}
    result = asyncio.run(_tool()(group_by=["day"], release_id=[R1]))
    assert result["ok"] is False and result["status_code"] == 403
    assert result["code"] == "forbidden"
    assert "series" not in result and "param" not in result


def test_a_429_says_when_to_retry(backend):
    _, state = backend
    state["status"] = 429
    state["headers"] = {"Retry-After": "12"}
    state["body"] = {"code": "rate_limited", "message": "Too many", "request_id": "r",
                     "detail": "Too many"}
    result = asyncio.run(_tool()(group_by=["day"]))
    assert result["status_code"] == 429
    assert result["code"] == "rate_limited"
    assert result["retry_after"] == "12"


def test_a_non_json_error_still_returns_a_structured_result(monkeypatch):
    """A proxy's HTML 502 has no contract fields; the result must still be
    a dict an agent can branch on, never a traceback."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    result = asyncio.run(_tool()(group_by=["day"]))
    assert result["ok"] is False and result["status_code"] == 502
    assert "code" not in result


def test_a_connection_failure_is_a_structured_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend")
    monkeypatch.setattr(client, "_client", http)
    monkeypatch.setattr(client, "_request_access_token", lambda: "caller-token")
    result = asyncio.run(_tool()(group_by=["day"]))
    assert result["ok"] is False and "status_code" not in result
