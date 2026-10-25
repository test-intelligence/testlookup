"""W15 AGT-03: the MCP surface of the agent-action queue is read-only.

* ``list_agent_actions`` reads ``GET /api/v1/projects/{p}/agent-actions`` with a
  clamped ``limit`` and a ``view`` of ``pending`` or ``decided`` only.
* Every cell is the server's text, kept on one line and out of the table syntax.
* PARITY: no MCP tool reaches a single action's path (``agent-actions/<id>`` is
  the PATCH that approves, rejects and undoes), and no tool is named for
  approving / rejecting / executing / rolling back / undoing an action. The MCP
  server forwards the caller's bearer, so such a tool would let an agent acting
  for a QA lead approve the effects its own investigation proposed.
"""
import ast
import asyncio
import re
import sys
from pathlib import Path

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tool(monkeypatch, payload):
    import client
    from tools import agent_actions

    calls = []

    async def _get(path, params=None):
        calls.append((path, params))
        return payload

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    agent_actions.register(mcp)
    assert set(mcp.tools) == {"list_agent_actions"}
    return mcp.tools["list_agent_actions"], calls


def _row(**extra):
    row = {
        "id": "a1", "action_type": "quarantine.apply", "canonical_type": "quarantine.apply",
        "title": "Quarantine checkout-retry", "target_id": "fpA1b2C3d4",
        "target": {"kind": "test", "id": "fpA1b2C3d4", "label": "checkout retry"},
        "status": "pending_review", "outcome_text": "Waiting for a QA lead's approval.",
    }
    row.update(extra)
    return row


def test_list_agent_actions_reads_the_pending_queue_with_a_clamped_limit(monkeypatch):
    payload = {"items": [_row()], "count": 1, "total": 3}
    tool, calls = _tool(monkeypatch, payload)
    text = asyncio.run(tool("proj-1", limit=999))
    assert calls == [("/api/v1/projects/proj-1/agent-actions", {"view": "pending", "limit": 200})]
    assert "| Action | Type | Target | Status | Outcome |" in text
    assert "| Quarantine checkout-retry | quarantine.apply | checkout retry | pending_review " in text
    assert "(1 of 3)" in text
    assert "Approving, rejecting or undoing an agent action is not available through MCP." in text


def test_the_decided_view_passes_through_and_anything_else_reads_as_pending(monkeypatch):
    tool, calls = _tool(monkeypatch, {"items": [], "count": 0, "total": 0})
    asyncio.run(tool("proj-1", view="decided", limit=0))
    asyncio.run(tool("proj-1", view="'; DROP", limit=20))
    assert calls == [
        ("/api/v1/projects/proj-1/agent-actions", {"view": "decided", "limit": 1}),
        ("/api/v1/projects/proj-1/agent-actions", {"view": "pending", "limit": 20}),
    ]


def test_an_empty_queue_says_so(monkeypatch):
    tool, _ = _tool(monkeypatch, {"items": [], "count": 0, "total": 0})
    text = asyncio.run(tool("proj-1"))
    assert text.startswith("No agent actions are waiting for approval in project proj-1.")
    assert "not available through MCP" in text


def test_a_cell_cannot_break_the_table(monkeypatch):
    row = _row(title="Quarantine a | b\n| injected | row |", outcome_text=None)
    tool, _ = _tool(monkeypatch, {"items": [row], "count": 1, "total": 1})
    text = asyncio.run(tool("proj-1"))
    table_rows = [line for line in text.splitlines() if line.startswith("| Quarantine")]
    assert table_rows == ["| Quarantine a / b / injected / row / | quarantine.apply | checkout retry "
                          "| pending_review | - |"]


def test_the_agent_actions_module_is_registered_in_the_server():
    content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
    assert "from tools import agent_actions" in content
    assert "agent_actions.register(mcp)" in content
    assert "`list_agent_actions`" in content


# ── parity: no MCP tool can decide an agent action ───────────────────────────


def test_no_mcp_tool_reaches_a_single_agent_action():
    offenders = []
    for path in sorted((MCP_DIR / "tools").glob("*.py")):
        if re.search(r"agent-actions/[^\"'\s]", path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert not offenders, f"an MCP tool reaches /agent-actions/<id> (the decide PATCH): {offenders}"


def test_no_mcp_tool_is_named_for_deciding_an_agent_action():
    verbs = ("approve", "reject", "execute", "rollback", "roll_back", "undo")
    offenders = []
    for path in sorted((MCP_DIR / "tools").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                name = node.name.lower()
                if "action" in name and any(verb in name for verb in verbs):
                    offenders.append(f"{path.name}:{node.name}")
    assert not offenders, offenders
