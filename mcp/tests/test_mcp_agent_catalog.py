"""E1.5: the agent catalog and single-agent invocations as MCP tools.

* list_agents / get_agent render the catalog and say what is invocable;
* invoke_agent validates before calling, always sends an Idempotency-Key (the
  caller's, or a fresh one it returns for reuse), and turns HTTP errors into
  structured results;
* get_agent_invocation carries the output and ends with review_state;
* the client forwards extra headers without letting them override auth.
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

from tools import agents  # noqa: E402

PROJECT = str(uuid.uuid4())
RUN = str(uuid.uuid4())


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools(monkeypatch, *, get=None, post=None):
    import client

    calls = []

    async def _get(path, params=None):
        calls.append(("GET", path, None))
        return get

    async def _post(path, json_body=None, params=None, extra_headers=None):
        calls.append(("POST", path, {"json": json_body, "headers": extra_headers}))
        if isinstance(post, Exception):
            raise post
        return post

    monkeypatch.setattr(client, "get", _get)
    monkeypatch.setattr(client, "post", _post)
    mcp = _FakeMCP()
    agents.register(mcp)
    return mcp.tools, calls


# -- rendering and validation ---------------------------------------------------------


def test_the_catalog_marks_which_agents_are_invocable():
    text = agents.render_catalog([
        {"agent_id": "agent.summary.v1", "permission": "read_only", "sync_eligible": False, "produces_report": True},
        {"agent_id": "agent.workflow.v1", "permission": "read_only", "sync_eligible": False, "produces_report": False},
    ])
    assert "| `agent.summary.v1` | read_only | yes | no | yes |" in text
    assert "| `agent.workflow.v1` | read_only | no | no | no |" in text


def test_an_agent_that_is_not_invocable_says_so():
    text = agents.render_agent({"agent_id": "agent.workflow.v1", "input_schema_resolved": False})
    assert "cannot be invoked on its own" in text
    assert "invoke_agent(" not in text


@pytest.mark.parametrize(
    "args, fragment",
    [
        (("summary", PROJECT, RUN), "agent.<name>"),
        (("agent.workflow.v1", PROJECT, RUN), "cannot be invoked"),
        (("agent.summary.v1", "not-a-uuid", RUN), "project_id"),
        (("agent.summary.v1", PROJECT, "nope"), "test_run_id"),
        (("agent.summary.v1", PROJECT, RUN, "later"), "mode"),
    ],
)
def test_invoke_body_refuses_bad_input(args, fragment):
    with pytest.raises(ValueError, match=fragment):
        agents.invoke_body(*args)


def test_invoke_body_uses_the_catalog_input_shape():
    assert agents.invoke_body("agent.summary.v1", PROJECT, RUN) == {
        "project_id": PROJECT,
        "input": {"agent_id": "agent.summary.v1", "payload": {"test_run_id": RUN}},
        "mode": "async",
    }


def test_invoke_body_forwards_tighten_only_config_overrides():
    override = {"retry": {"max_attempts": 1}, "tools": {"allowlist": []}}
    body = agents.invoke_body(
        "agent.summary.v1", PROJECT, RUN, config_overrides=override
    )
    assert body["config_overrides"] == override


# -- the real tool bodies -----------------------------------------------------------------


def test_list_and_get_read_the_catalog_routes(monkeypatch):
    tools, calls = _tools(monkeypatch, get=[{"agent_id": "agent.summary.v1"}])
    asyncio.run(tools["list_agents"]())
    tools, calls2 = _tools(monkeypatch, get={"agent_id": "agent.summary.v1"})
    asyncio.run(tools["get_agent"]("agent.summary.v1"))
    assert calls[0][:2] == ("GET", "/api/v1/agents/catalog")
    assert calls2[0][:2] == ("GET", "/api/v1/agents/catalog/agent.summary.v1")


def test_get_agent_refuses_a_malformed_id_without_calling_the_api(monkeypatch):
    tools, calls = _tools(monkeypatch, get={})
    assert "agent.<name>" in asyncio.run(tools["get_agent"]("../reviews"))
    assert calls == []


def test_invoke_sends_the_callers_idempotency_key(monkeypatch):
    tools, calls = _tools(monkeypatch, post={"id": "inv-1", "status": "in_progress", "review": {"state": "pending_review"}})
    out = asyncio.run(tools["invoke_agent"]("agent.summary.v1", PROJECT, RUN, idempotency_key="key-abcdefgh"))
    method, path, sent = calls[0]
    assert (method, path) == ("POST", "/api/v1/agents/agent.summary.v1/invoke")
    assert sent["headers"] == {"Idempotency-Key": "key-abcdefgh"}
    assert sent["json"]["input"]["payload"] == {"test_run_id": RUN}
    assert out == {
        "ok": True, "invocation_id": "inv-1", "status": "in_progress", "review_state": "pending_review",
        "idempotency_key": "key-abcdefgh", "note": "Poll get_agent_invocation for status and output.",
    }


def test_invoke_forwards_config_overrides_to_the_rest_authority(monkeypatch):
    tools, calls = _tools(monkeypatch, post={"id": "inv-1", "status": "in_progress"})
    override = {"model": {"tier": "deterministic"}}
    asyncio.run(
        tools["invoke_agent"](
            "agent.summary.v1",
            PROJECT,
            RUN,
            config_overrides=override,
        )
    )
    assert calls[0][2]["json"]["config_overrides"] == override


def test_invoke_generates_a_key_and_returns_it_for_reuse(monkeypatch):
    tools, calls = _tools(monkeypatch, post={"id": "inv-1", "status": "in_progress"})
    out = asyncio.run(tools["invoke_agent"]("agent.summary.v1", PROJECT, RUN))
    sent_key = calls[0][2]["headers"]["Idempotency-Key"]
    assert uuid.UUID(sent_key) and out["idempotency_key"] == sent_key
    assert out["review_state"] == "unknown", "no review block must not read as reviewed"


def test_invoke_refuses_bad_input_before_calling_the_api(monkeypatch):
    tools, calls = _tools(monkeypatch, post={})
    out = asyncio.run(tools["invoke_agent"]("agent.workflow.v1", PROJECT, RUN))
    assert out["ok"] is False and calls == []


def test_invoke_turns_http_errors_into_a_structured_result(monkeypatch):
    request = httpx.Request("POST", "http://backend/api/v1/agents/agent.summary.v1/invoke")
    response = httpx.Response(409, json={"detail": "A request with this Idempotency-Key is still being handled"}, request=request)
    tools, _ = _tools(monkeypatch, post=httpx.HTTPStatusError("conflict", request=request, response=response))
    out = asyncio.run(tools["invoke_agent"]("agent.summary.v1", PROJECT, RUN, idempotency_key="key-abcdefgh"))
    assert out["ok"] is False and out["status_code"] == 409
    assert "still being handled" in out["detail"] and out["idempotency_key"] == "key-abcdefgh"


def test_the_invocation_view_carries_output_and_ends_with_review_state(monkeypatch):
    tools, calls = _tools(monkeypatch, get={
        "id": "inv-1", "agent_id": "agent.summary.v1", "status": "completed", "attempt": 1, "max_attempts": 5,
        "output": {"executive_summary": "Checkout regressed."},
        "review": {"state": "pending_review", "message": "Human review required before use."},
        "ai_disclaimer": "AI-generated content. Verify before acting.",
    })
    text = asyncio.run(tools["get_agent_invocation"]("inv-1"))
    assert calls[0][:2] == ("GET", "/api/v1/agents/invocations/inv-1")
    assert json.dumps({"executive_summary": "Checkout regressed."}, indent=2) in text
    assert "**review_state:** `pending_review`" in text
    assert text.rstrip().endswith("AI-generated content. Verify before acting.")


def test_the_invocation_view_shows_the_sanitized_frozen_config():
    snapshot = {
        "agent_id": "agent.summary.v1",
        "config_version": 3,
        "clamps": [{"field": "timeout_seconds", "layer": "env"}],
    }
    text = agents.render_invocation({
        "id": "inv-1",
        "agent_id": "agent.summary.v1",
        "status": "in_progress",
        "attempt": 1,
        "max_attempts": 2,
        "config_snapshot": snapshot,
        "review": {"state": "not_required"},
    })
    assert "### Frozen configuration" in text
    assert json.dumps(snapshot, indent=2) in text


def test_the_agents_module_is_registered_in_the_server():
    import ast

    content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
    # Parse it: these tests read server.py as text, so a syntax error in the
    # instructions string passed them and failed only CI's syntax check.
    ast.parse(content)
    assert "agents.register(mcp)" in content and "invoke_agent" in content


# -- client extra headers -----------------------------------------------------------------------


def test_post_forwards_extra_headers_but_auth_headers_win(monkeypatch):
    import client

    seen = {}

    class _Http:
        async def post(self, path, json=None, params=None, headers=None):
            seen["headers"] = headers
            return httpx.Response(202, json={"id": "inv-1"}, request=httpx.Request("POST", "http://backend" + path))

    async def _auth():
        return {"Authorization": "Bearer real"}, True

    monkeypatch.setattr(client, "_get_client", lambda: _Http())
    monkeypatch.setattr(client, "_auth_context", _auth)
    asyncio.run(client.post(
        "/api/v1/agents/agent.summary.v1/invoke", json_body={},
        extra_headers={"Idempotency-Key": "k-12345678", "Authorization": "Bearer spoofed"},
    ))
    assert seen["headers"] == {"Idempotency-Key": "k-12345678", "Authorization": "Bearer real"}
    assert isinstance(SimpleNamespace(), SimpleNamespace)
