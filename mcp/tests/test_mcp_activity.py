"""W18 DEV-04: the MCP activity tools read the Activity tab's routes, read-only.

A project name resolves; a page with more says so (``more`` + ``note`` with the
cursor); the event detail carries the diff; an HTTP error is ``{ok: false}``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
from tools import activity  # noqa: E402

P1 = "11111111-1111-4111-8111-111111111111"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools(monkeypatch, deny=False):
    calls = []

    async def _get(path, params=None):
        calls.append((path, params))
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Payment Service"}]
        if deny:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError("e", request=req, response=httpx.Response(403, request=req))
        if path.endswith("/activity"):
            return {"items": [{"id": "e0", "event_type": "defect.created", "summary": "Defect filed", "extra": "x"}], "next_cursor": "c2"}
        return {"id": "e0", "diff": {"before": {"a": 1}, "after": {"a": 2}, "changed_fields": ["a"]}}

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    activity.register(mcp)
    return calls, mcp.tools


def test_list_resolves_the_project_and_says_there_is_more(monkeypatch):
    calls, tools = _tools(monkeypatch)
    out = asyncio.run(tools["list_project_activity"]("Payment Service", since="7d"))
    assert out["ok"] and out["project_id"] == P1 and out["more"] is True and "cursor=c2" in out["note"]
    assert out["items"][0]["event_type"] == "defect.created" and "extra" not in out["items"][0]
    path, params = calls[-1]
    assert path == f"/api/v1/projects/{P1}/activity" and params["limit"] == 50 and params["since"]


def test_the_event_carries_its_diff(monkeypatch):
    _, tools = _tools(monkeypatch)
    out = asyncio.run(tools["get_activity_event"](P1, "e0"))
    assert out["ok"] and out["diff"]["changed_fields"] == ["a"]


def test_a_refusal_is_an_error_payload(monkeypatch):
    _, tools = _tools(monkeypatch, deny=True)
    out = asyncio.run(tools["list_project_activity"](P1))
    assert out["ok"] is False and out["status_code"] == 403
