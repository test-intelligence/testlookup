"""MCP global search preserves incomplete-source status even with no rows."""
from __future__ import annotations

import asyncio

import client
from tools import search


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


def test_empty_partial_search_does_not_claim_authoritative_no_results(monkeypatch):
    async def get(*_args, **_kwargs):
        return {
            "items": [],
            "total": 0,
            "entity_counts": {},
            "result_status": "partial",
            "counts_are_exact": False,
            "failed_entity_types": ["defect", "suite"],
        }

    monkeypatch.setattr(client, "get", get)
    mcp = _FakeMCP()
    search.register(mcp)

    text = asyncio.run(mcp.tools["global_search"]("checkout"))
    assert "search was incomplete" in text
    assert "Totals are lower bounds" in text
    assert "Unavailable sources: defect, suite" in text
    assert text != "No results for 'checkout'."
