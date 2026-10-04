"""VIZ-211 -- MCP's ``get_chart_data`` answers what REST answers, on real data.

The unit tests prove the tool sends the route's parameter names and returns
the body it is given. This proves the end of the chain: the real tool body,
the real MCP HTTP client, the real FastAPI app over ASGI and real Postgres,
for a release- and suite-scoped question -- the kind MCP could not ask before
VIZ-211 reversed the "release scoping is an MCP non-goal" decision for chart
data.

1. **Parity.** ``pass_rate`` by day for two releases and one suite, and a
   two-dimension case, come back from MCP equal to the REST body, apart from
   ``meta.generated_at`` and ``meta.as_of`` (the read's own clock).
2. **Authorisation.** A release from another project refuses the whole call
   with 403 through MCP, exactly as through REST, and a caller who cannot
   read the project gets 403 too. Nothing is returned for the readable ids.

The seeded world is ``test_analytics_scope_postgres.py``'s (frozen clock,
throwaway projects). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to
head.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.integration.test_analytics_scope_postgres import R1_KEY, R2_KEY, S1
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: pytest finds a fixture by the module attribute's name.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

MCP_DIR = Path(__file__).resolve().parents[3] / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import client as mcp_client  # noqa: E402
from tools import analytics as analytics_tools  # noqa: E402

PATH = "/api/v1/analytics/chart-data"
#: Stamped by the read itself, so two reads of the same numbers differ here.
_READ_CLOCK = ("generated_at", "as_of")


class _ToolRegistry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(function):
            self.tools[function.__name__] = function
            return function

        return decorate


def _bearer(headers: dict) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


@pytest.fixture
def chart_tool(world, monkeypatch):
    """``get_chart_data`` wired to the ASGI app, as a caller of ``world``'s."""
    registry = _ToolRegistry()
    analytics_tools.register(registry)
    caller = {"token": _bearer(world.member)}
    monkeypatch.setattr(mcp_client, "_client", world.client)
    monkeypatch.setattr(mcp_client, "_request_access_token", lambda: caller["token"])
    return registry.tools["get_chart_data"], caller


def _without_read_clock(body: dict) -> dict:
    meta = {key: value for key, value in body["meta"].items() if key not in _READ_CLOCK}
    return {**body, "meta": meta}


async def _rest(world, params: dict, headers=None) -> dict:
    query = [
        (key, str(item))
        for key, value in params.items() if value is not None
        for item in (value if isinstance(value, list) else [value])
    ]
    resp = await world.client.get(PATH, params=query, headers=headers or world.member)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("case", ["releases_and_suite", "two_dimensions"])
async def test_mcp_returns_the_rest_body(world, chart_tool, case) -> None:
    tool, _ = chart_tool
    releases = [str(world.releases[R1_KEY]), str(world.releases[R2_KEY])]
    if case == "releases_and_suite":
        params = {
            "group_by": ["day"], "metric": "pass_rate", "project_id": str(world.p1),
            "release_id": releases, "suite_name": [S1], "days": 30,
        }
    else:
        params = {
            "group_by": ["day", "release"], "metric": "executions",
            "project_id": str(world.p1), "days": 30, "top_n": 3,
        }
    rest = await _rest(world, params)
    via_mcp = await tool(**params)
    assert "ok" not in via_mcp, via_mcp
    assert _without_read_clock(via_mcp) == _without_read_clock(rest)
    if case == "releases_and_suite":
        # The filters reached the server, so the parity is about a scoped
        # answer and not two copies of the project-wide one.
        applied = via_mcp["meta"]["scope"]
        assert sorted(r["id"] for r in applied["releases"]) == sorted(releases)
        assert applied["suites"] == [S1.lower()]
        assert all(len(s["points"]) == 30 for s in via_mcp["series"]), "every day, zero-filled"


async def test_a_release_in_another_project_is_refused_through_mcp(world, chart_tool) -> None:
    tool, _ = chart_tool
    result = await tool(
        group_by=["day"], project_id=str(world.p1),
        release_id=[str(world.releases[R1_KEY]), str(world.r9)],
    )
    assert result["ok"] is False
    assert result["status_code"] in (403, 404), result
    assert result["code"] in ("forbidden", "not_found")
    assert "series" not in result


async def test_a_caller_outside_the_project_is_refused_through_mcp(world, chart_tool) -> None:
    tool, caller = chart_tool
    caller["token"] = _bearer(world.outsider)
    result = await tool(group_by=["day"], project_id=str(world.p1))
    assert result["ok"] is False
    assert result["status_code"] == 403, result
    assert "series" not in result
