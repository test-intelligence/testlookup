"""MCP resources drop the VIZ-204 ``meta`` envelope; tools keep it.

A resource is read passively into an assistant's context, whole, so the
envelope (~1 KB of scope echo, totals and timestamps per read) was paid for in
tokens on every read of the three analytics resources.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

registry = pytest.importorskip("resources.registry")

META = {"schema_version": 2, "scope": {"projects": [{"id": "p", "name": "x" * 400}]},
        "totals": {"matched_runs": 1, "total_runs": 1}}

ANALYTICS_RESOURCES = {
    "testlookup://projects/{project_id}/metrics": (
        "/api/v1/metrics/summary", {"total_executions_7d": {"value": 3}}),
    "testlookup://projects/{project_id}/flaky-tests": (
        "/api/v1/analytics/flaky-tests", {"items": [{"test_name": "t"}], "total": 1}),
    "testlookup://projects/{project_id}/defects/open": (
        "/api/v1/analytics/defects", {"items": [], "total": 0}),
}


class _FakeMCP:
    def __init__(self):
        self.resources = {}

    def resource(self, uri):
        def decorate(fn):
            self.resources[uri] = fn
            return fn
        return decorate


@pytest.mark.parametrize("uri", list(ANALYTICS_RESOURCES))
def test_analytics_resources_drop_meta_and_keep_the_rest(monkeypatch, uri):
    path, body = ANALYTICS_RESOURCES[uri]
    seen = []

    async def fake_get(url, params=None):
        seen.append(url)
        return {**body, "meta": META}

    monkeypatch.setattr(registry.api, "get", fake_get)
    mcp = _FakeMCP()
    registry.register(mcp)
    out = asyncio.run(mcp.resources[uri](project_id="p1"))
    assert seen == [path]
    assert json.loads(out) == body, "every other key is kept exactly"
    assert "schema_version" not in out and "matched_runs" not in out


def test_without_meta_leaves_other_shapes_alone():
    assert registry.without_meta([1, {"meta": 1}]) == [1, {"meta": 1}]
    assert registry.without_meta({"a": 1}) == {"a": 1}
    assert registry.without_meta(None) is None


def test_tools_are_unchanged():
    """Only resources strip ``meta``: no tool module calls the helper."""
    for module in (MCP_DIR / "tools").glob("*.py"):
        assert "without_meta" not in module.read_text(encoding="utf-8"), module.name
