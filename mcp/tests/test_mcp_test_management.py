"""W34 STRAT-11: the MCP test-management tools read the Test Management routes, read-only.

Pinned: a project name resolves and the list sends exactly the route's names;
the markdown table carries the short id and says when there is more; the case
carries its steps and (on request) its history; an HTTP error is
``{ok: false, status_code}``; an unresolved project or a non-id never reaches
the API; the module is registered in the server.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
from tools import test_management  # noqa: E402

P1 = "11111111-1111-4111-8111-111111111111"
C1 = "1a2b3c4d-0000-4000-8000-000000000001"
LIST = "/api/v1/test-management/cases"
CASE = {"id": C1, "title": "Login | works", "status": "approved", "priority": "high", "test_type": "functional",
        "severity": "major", "suite_name": "checkout", "owner": "Dana", "is_automated": True, "version": 3,
        "automation_status": "automated", "tags": ["smoke"], "description": "not listed",
        "steps": [{"step_number": 1, "action": "Open login", "expected_result": "Form shows"}]}


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools(monkeypatch, deny=None):
    calls = []

    async def _get(path, params=None):
        calls.append((path, params))
        if path == "/api/v1/projects":
            return [{"id": P1, "name": "Payment Service"}]
        if deny:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError("e", request=req, response=httpx.Response(deny, request=req, json={"detail": "no"}))
        if path == LIST:
            return {"items": [CASE], "total": 30, "page": params["page"], "size": params["size"], "pages": 2}
        if path == f"{LIST}/{C1}":
            return CASE
        if path == f"{LIST}/{C1}/history":
            return [{"version": 3, "created_at": "2026-11-01T10:00:00Z", "change_type": "update",
                     "changed_by_name": "Dana", "status": "approved", "changed_fields": ["title"]}]
        raise AssertionError(path)

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    test_management.register(mcp)
    return calls, mcp.tools


def test_both_tools_register():
    mcp = _FakeMCP()
    test_management.register(mcp)
    assert set(mcp.tools) == {"list_managed_test_cases", "get_managed_test_case"}


def test_list_resolves_the_project_and_sends_the_routes_names(monkeypatch):
    calls, tools = _tools(monkeypatch)
    out = asyncio.run(tools["list_managed_test_cases"](
        "Payment Service", status=["draft", "approved"], test_type=["functional"], priority=["P1"],
        suite_name="checkout", search="login", owner="me", automation="automated", requirement="REQ-7",
        include_archived=True, page=1, size=500,
    ))
    assert out["ok"] and out["project_id"] == P1
    path, params = calls[-1]
    assert path == LIST and params == {
        "project_id": P1, "status": ["draft", "approved"], "test_type": ["functional"], "priority": ["P1"],
        "suite_name": "checkout", "search": "login", "owner": "me", "automation": "automated",
        "requirement": "REQ-7", "include_archived": True, "page": 1, "size": 200,
    }


def test_list_markdown_and_items(monkeypatch):
    _, tools = _tools(monkeypatch)
    out = asyncio.run(tools["list_managed_test_cases"](P1))
    assert out["total"] == 30 and out["pages"] == 2 and out["more"] is True
    assert "## Test cases — page 1/2 (30 total)" in out["summary"]
    assert f"| TC-1A2B3C | Login \\| works | approved | high | functional | checkout | Dana | yes | `{C1}` |" in out["summary"]
    assert "more — call again with page=2" in out["summary"]
    item = out["items"][0]
    assert item["short_id"] == "TC-1A2B3C" and item["suite_name"] == "checkout" and "description" not in item


def test_the_case_carries_its_steps_and_history_on_request(monkeypatch):
    calls, tools = _tools(monkeypatch)
    out = asyncio.run(tools["get_managed_test_case"](C1))
    assert out["ok"] and out["case"]["id"] == C1 and "history" not in out
    assert "| 1 | Open login | Form shows |" in out["summary"]
    assert [p for p, _ in calls] == [f"{LIST}/{C1}"]
    out = asyncio.run(tools["get_managed_test_case"](C1, include_history=True))
    assert out["history"][0]["version"] == 3 and "### History (1 saved, newest first)" in out["summary"]
    assert calls[-1][0] == f"{LIST}/{C1}/history"


def test_a_refusal_is_an_error_payload(monkeypatch):
    _, tools = _tools(monkeypatch, deny=403)
    out = asyncio.run(tools["list_managed_test_cases"](P1))
    assert out["ok"] is False and out["status_code"] == 403 and out["detail"] == "no"
    out = asyncio.run(tools["get_managed_test_case"](C1, include_history=True))
    assert out["ok"] is False and out["status_code"] == 403


def test_an_unresolved_project_never_reaches_the_list(monkeypatch):
    calls, tools = _tools(monkeypatch)
    out = asyncio.run(tools["list_managed_test_cases"]("Payment Servce"))
    assert out["ok"] is False and out["code"] == "unresolved_name" and "Payment Service" in out["detail"]
    assert [p for p, _ in calls] == ["/api/v1/projects"]


def test_a_short_id_is_not_sent_as_a_case_id(monkeypatch):
    calls, tools = _tools(monkeypatch)
    out = asyncio.run(tools["get_managed_test_case"]("TC-1A2B3C"))
    assert out["ok"] is False and out["status_code"] == 400 and "search" in out["detail"]
    assert calls == []


def test_the_server_registers_the_module():
    content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
    assert "from tools import test_management" in content and "test_management.register(mcp)" in content
    assert "list_managed_test_cases" in content and "get_managed_test_case" in content
