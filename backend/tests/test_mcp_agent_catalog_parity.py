"""E1.5: the MCP server exposes the same agent catalog the REST API does.

One registry, two transports (architecture section 10). The MCP tools live in a
separate package that cannot import the backend, so it keeps a literal copy of
the invocable agent ids. These tests hold the copy to the registry:

* every agent the backend can invoke on its own is invocable through MCP, and
  nothing else is;
* every catalog agent is reachable through the MCP catalog tools, with the
  same agent-id shape the REST API validates;
* no MCP tool reaches review accept or reject (section 8.3).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app.services import agent_catalog
from app.services.agent_capability_registry import CAPABILITY_REGISTRY
from app.services.agent_planner import invocation_workflow_type

MCP_DIR = Path(__file__).resolve().parents[2] / "mcp"
AGENTS_TOOL = MCP_DIR / "tools" / "agents.py"


def _module() -> ast.Module:
    return ast.parse(AGENTS_TOOL.read_text(encoding="utf-8"))


def _mcp_invokable_ids() -> set[str]:
    for node in ast.walk(_module()):
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        )
        if isinstance(target, ast.Name) and target.id == "INVOKABLE_AGENT_IDS":
            value = node.value
            if isinstance(value, ast.Call) and value.args:
                value = value.args[0]
            return {e.value for e in value.elts if isinstance(e, ast.Constant)}  # type: ignore[union-attr]
    raise AssertionError("INVOKABLE_AGENT_IDS not found in mcp/tools/agents.py")


def _mcp_agent_id_pattern() -> str:
    for node in ast.walk(_module()):
        if (
            isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "AGENT_ID_PATTERN" and isinstance(node.value, ast.Call)
        ):
            return node.value.args[0].value  # type: ignore[attr-defined,no-any-return]
    raise AssertionError("AGENT_ID_PATTERN not found in mcp/tools/agents.py")


def test_every_agent_the_backend_can_invoke_is_invocable_through_mcp_and_nothing_else():
    backend = {
        spec.capability_id for spec in CAPABILITY_REGISTRY.values()
        if invocation_workflow_type(spec.stage_name) is not None
    }
    mcp = _mcp_invokable_ids()
    assert backend, "the registry has no invocable agents: this test would pass vacuously"
    assert mcp == backend, (
        f"MCP INVOKABLE_AGENT_IDS drifted from the registry. Missing in MCP: {sorted(backend - mcp)}. "
        f"Not invocable on the backend: {sorted(mcp - backend)}."
    )


def test_every_catalog_agent_is_reachable_through_the_mcp_catalog_tools():
    source = AGENTS_TOOL.read_text(encoding="utf-8")
    assert '"/api/v1/agents/catalog"' in source
    assert 'f"/api/v1/agents/catalog/{agent_id}"' in source
    assert _mcp_agent_id_pattern() == agent_catalog.AGENT_ID_PATTERN.pattern
    pattern = re.compile(_mcp_agent_id_pattern())
    unreachable = [s.capability_id for s in CAPABILITY_REGISTRY.values() if not pattern.match(s.capability_id)]
    assert unreachable == [], f"catalog ids get_agent would refuse: {unreachable}"


def test_every_mcp_agent_tool_is_registered_with_the_server():
    server = (MCP_DIR / "server.py").read_text(encoding="utf-8")
    assert "from tools import agents" in server and "agents.register(mcp)" in server
    tools = {
        node.name for node in ast.walk(_module())
        if isinstance(node, ast.AsyncFunctionDef)
        and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool" for d in node.decorator_list)
    }
    assert tools == {"list_agents", "get_agent", "invoke_agent", "get_agent_invocation"}


def test_no_mcp_tool_settles_a_review():
    offenders = [
        path.name for path in sorted((MCP_DIR / "tools").glob("*.py"))
        if re.search(r"reviews/[^\"'\s]*/(accept|reject)", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
