"""W18 DEV-01: ``get_release_gate`` returns a release's gate verdict as data.

A name resolves to the id first; never-evaluated is ``evaluated: false`` (not a
verdict); ``evaluate=True`` POSTs the evaluate route; an HTTP error is the tools'
``{ok: false}`` shape.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
from tools import release  # noqa: E402

R1 = "22222222-2222-4222-8222-222222222221"
GATE = {"release_id": R1, "verdict": "NO_GO", "blocking_reasons": ["fp1 is FAILED"],
        "blocking_reasons_display": ["test_pay (checkout) is FAILED"], "blocking_total": 1,
        "decided_at": "2026-10-26T10:00:00", "scorecard": {"pass_rate": 91.0, "evidence_count": 40, "run_count": 2, "measured": True}}


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _status_error(code):
    req = httpx.Request("GET", "http://x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


def _tool(monkeypatch, gate=GATE, missing=False):
    calls = []

    async def _get(path, params=None):
        calls.append(("GET", path))
        if path == "/api/v1/releases":
            return {"items": [{"id": R1, "name": "Checkout 2026.10", "version": "2026.10"}]}
        if missing:
            raise _status_error(404)
        return gate

    async def _post(path, json_body=None, params=None, extra_headers=None):
        calls.append(("POST", path))
        return {**gate, "recorded": True}

    monkeypatch.setattr(client, "get", _get)
    monkeypatch.setattr(client, "post", _post)
    mcp = _FakeMCP()
    release.register(mcp)
    return calls, mcp.tools["get_release_gate"]


def test_a_name_resolves_and_the_verdict_comes_back_as_data(monkeypatch):
    calls, tool = _tool(monkeypatch)
    out = asyncio.run(tool(release="checkout 2026.10"))
    assert out["ok"] and out["release_id"] == R1 and out["verdict"] == "NO_GO"
    assert out["blockers"] == ["test_pay (checkout) is FAILED"] and out["pass_rate"] == 91.0
    assert ("GET", f"/api/v1/releases/{R1}/gate") in calls


def test_never_evaluated_is_not_a_verdict(monkeypatch):
    _, tool = _tool(monkeypatch, missing=True)
    out = asyncio.run(tool(release=R1))
    assert out["ok"] is True and out["evaluated"] is False and "verdict" not in out


def test_evaluate_posts_the_evaluate_route(monkeypatch):
    calls, tool = _tool(monkeypatch)
    out = asyncio.run(tool(release=R1, evaluate=True))
    assert ("POST", f"/api/v1/releases/{R1}/gate/evaluate") in calls and out["recorded"] is True


def test_an_unknown_name_is_an_error_not_a_guess(monkeypatch):
    _, tool = _tool(monkeypatch)
    out = asyncio.run(tool(release="Checkout 2026.11"))
    assert out["ok"] is False and out["code"] == "unresolved_name"
