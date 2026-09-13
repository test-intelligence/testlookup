"""E8.4 (slice 4): the MCP surface of the human review gate.

* Report tools end with ``review_state`` and the AI disclaimer, and a payload
  without a review block reads ``unknown`` with a warning, never as reviewed.
* ``list_pending_reviews`` is read-only.
* No MCP tool can accept or reject a review. The server forwards the caller's
  bearer, so such a tool would let an agent approve its own output (section 8.3).
"""
import asyncio
import ast
import re
import sys
from pathlib import Path

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

import review_notice  # noqa: E402

DISCLAIMER = "AI-generated content. Verify before acting."


def _pending(**extra):
    return {
        "review": {"state": "pending_review", "message": "Human review required before use.", "review_id": "r1"},
        "requires_human_review": True,
        "ai_disclaimer": DISCLAIMER,
        **extra,
    }


# ── review_notice ────────────────────────────────────────────────────────────


def test_a_pending_report_carries_its_state_message_and_disclaimer():
    text = "\n".join(review_notice.review_lines(_pending()))
    assert "**review_state:** `pending_review`" in text
    assert "Human review required before use." in text
    assert DISCLAIMER in text


def test_a_deterministic_report_has_no_disclaimer():
    data = {"review": {"state": "not_applicable", "message": "Not AI-generated."}, "ai_disclaimer": None}
    text = "\n".join(review_notice.review_lines(data))
    assert "`not_applicable`" in text and DISCLAIMER not in text


def test_a_payload_without_a_review_block_reads_unknown_never_reviewed():
    text = "\n".join(review_notice.review_lines({"executive_summary": "x"}))
    assert "`unknown`" in text
    assert review_notice.UNKNOWN_WARNING in text


# ── the real tool bodies ─────────────────────────────────────────────────────


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _tools(monkeypatch, payload):
    import client
    from tools import intelligence, reports, reviews

    calls = []

    async def _get(path, params=None):
        calls.append((path, params))
        return payload

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    for module in (intelligence, reports, reviews):
        module.register(mcp)
    return mcp.tools, calls


def test_report_tools_end_with_the_review_state_and_disclaimer(monkeypatch):
    payload = _pending(executive_summary="Checkout regressed.", recommendation="PENDING_REVIEW", risk_score=40)
    tools, _ = _tools(monkeypatch, payload)
    for name, args in (
        ("get_run_summary", ("run-1",)),
        ("get_run_intelligence", ("run-1",)),
        ("check_run_release_readiness", ("run-1",)),
    ):
        text = asyncio.run(tools[name](*args))
        assert "**review_state:** `pending_review`" in text, name
        assert DISCLAIMER in text, name


def test_report_tools_say_unknown_when_the_backend_sends_no_review(monkeypatch):
    tools, _ = _tools(monkeypatch, {"executive_summary": "Checkout regressed."})
    assert "`unknown`" in asyncio.run(tools["get_run_summary"]("run-1"))


def test_list_pending_reviews_reads_the_open_queue(monkeypatch):
    rows = [{
        "id": "r1", "kind": "run_report", "subject_type": "pipeline_run", "subject_id": "p1",
        "workflow_type": "deep", "created_at": "2026-09-12T10:00:00Z", "ai_disclaimer": DISCLAIMER,
    }]
    tools, calls = _tools(monkeypatch, rows)
    text = asyncio.run(tools["list_pending_reviews"]("proj-1", limit=999))
    assert calls == [("/api/v1/projects/proj-1/reviews", {"state": "pending_review", "limit": 200})]
    assert "`r1`" in text and DISCLAIMER in text
    assert "not available through MCP" in text


def test_an_empty_queue_says_so():
    from tools import reviews

    assert "No AI reports are awaiting human review" in reviews.render_pending_reviews("proj-1", [])


def test_the_reviews_module_is_registered_in_the_server():
    content = (MCP_DIR / "server.py").read_text(encoding="utf-8")
    assert "reviews.register(mcp)" in content


# ── parity: no MCP tool can settle a review ──────────────────────────────────


def test_no_mcp_tool_reaches_review_accept_or_reject():
    offenders = []
    for path in sorted((MCP_DIR / "tools").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if re.search(r"reviews/[^\"'\s]*/(accept|reject)", src):
            offenders.append(path.name)
    assert not offenders, f"an MCP tool reaches /reviews/*/accept|reject: {offenders}"


def test_no_mcp_tool_is_named_for_settling_a_review():
    offenders = []
    for path in sorted((MCP_DIR / "tools").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                name = node.name.lower()
                if "review" in name and any(v in name for v in ("accept", "reject", "approve", "settle")):
                    offenders.append(f"{path.name}:{node.name}")
    assert not offenders, offenders
