"""W18 DEV-05: MCP tools take a release / project by NAME, never by guessing.

``get_chart_data(release_id=["2026.10"], project_id="Checkout Service")`` sends ids;
an ambiguous name returns ``{ok: false, code: unresolved_name}`` listing every
candidate with its id and sends no chart request; ids pass through untouched.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
import release_resolver  # noqa: E402
from tools import analytics  # noqa: E402

P1 = "11111111-1111-4111-8111-111111111111"
R1 = "22222222-2222-4222-8222-222222222221"
R2 = "22222222-2222-4222-8222-222222222222"
R3 = "22222222-2222-4222-8222-222222222223"
RELEASES = {"items": [
    {"id": R1, "name": "2026.10", "version": "2026.10.0", "status": "active", "project_id": P1, "project_name": "Checkout Service"},
    {"id": R2, "name": "Sprint 12", "version": "12a", "status": "planned", "project_id": P1, "project_name": "Checkout Service"},
    {"id": R3, "name": "Sprint 12", "version": "12b", "status": "released", "project_id": P1, "project_name": "Checkout Service"},
]}


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _wire(monkeypatch):
    calls = []

    async def _get(path, params=None):
        calls.append((path, params))
        if path == "/api/v1/releases":
            return RELEASES
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Checkout Service"}]
        return {"series": [], "meta": {}}

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    analytics.register(mcp)
    return calls, mcp.tools["get_chart_data"]


def test_names_resolve_to_ids_before_the_chart_request(monkeypatch):
    calls, tool = _wire(monkeypatch)
    body = asyncio.run(tool(group_by=["day"], metric="pass_rate", project_id="checkout service", release_id=["2026.10"]))
    assert body == {"series": [], "meta": {}}
    chart = [c for c in calls if c[0] == "/api/v1/analytics/chart-data"][0]
    assert chart[1]["project_id"] == P1 and chart[1]["release_id"] == [R1]


def test_an_ambiguous_name_is_an_error_listing_every_candidate(monkeypatch):
    calls, tool = _wire(monkeypatch)
    body = asyncio.run(tool(group_by=["day"], release_id=["Sprint 12"]))
    assert body["ok"] is False and body["code"] == "unresolved_name"
    assert "2 releases are called 'Sprint 12'" in body["detail"] and R2 in body["detail"] and R3 in body["detail"]
    assert not any(c[0] == "/api/v1/analytics/chart-data" for c in calls)


def test_ids_and_unattributed_pass_through_without_a_lookup(monkeypatch):
    calls, _ = _wire(monkeypatch)
    assert asyncio.run(release_resolver.resolve_releases([R1, "Unattributed"])) == [R1, "unattributed"]
    assert asyncio.run(release_resolver.resolve_project(P1)) == P1
    assert calls == []


def test_an_unknown_name_names_the_closest(monkeypatch):
    _wire(monkeypatch)
    try:
        asyncio.run(release_resolver.resolve_release("Sprint 13"))
    except release_resolver.UnresolvedName as exc:
        assert "Closest: Sprint 12" in str(exc)
    else:
        raise AssertionError("expected UnresolvedName")
