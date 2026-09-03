"""Regression: MCP surfaces say they are project-wide (S4c).

Why a test rather than a comment
--------------------------------
S4a gave five REST endpoints an optional ``release_id``. The MCP tools call
those same endpoints and were deliberately NOT given the parameter — release
scoping is a decided non-goal for MCP.

The consequence is that the same question now returns different numbers
depending on which surface asked it: a release-filtered dashboard and an MCP
tool reading the same endpoint disagree, legitimately. Nothing is wrong, but
nothing says so either — and the person most likely to hit it is an agent
reading a tool description, or someone comparing the two and concluding one is
broken.

A docstring is the only place an MCP client shows this, so the docstring IS the
feature here. This test exists because a docs-only guarantee has nothing else
holding it in place: the next person to edit these descriptions has no reason
to know the sentence is load-bearing.
"""
from __future__ import annotations

import re
from pathlib import Path

_MCP = Path(__file__).resolve().parents[3] / "mcp"

#: Endpoints that gained an optional release_id in S4a/S4b. An MCP surface
#: calling one of these can be release-narrowed in the UI but not here.
RELEASE_SCOPEABLE = (
    "/api/v1/analytics/flaky-tests",
    "/api/v1/analytics/failure-categories",
    "/api/v1/analytics/top-failing",
    "/api/v1/analytics/coverage",
)


def _callers(path: Path):
    """Yield (function_name, docstring) for functions calling a scopeable endpoint."""
    src = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"async def (\w+)\((?:.|\n)*?\"\"\"((?:.|\n)*?)\"\"\"((?:.|\n)*?)(?=\n    @|\n\ndef |\Z)", src):
        name, doc, body = m.group(1), m.group(2), m.group(3)
        if any(ep in body for ep in RELEASE_SCOPEABLE):
            yield path.name, name, doc


def test_every_mcp_surface_on_a_scopeable_endpoint_states_its_scope():
    """The divergence must be discoverable from the tool description itself.

    An agent choosing between tools sees only the description. If it does not
    say the answer is project-wide, the agent has no way to know its number is
    not comparable to a release-filtered one.
    """
    checked = 0
    for fname, func, doc in list(_callers(_MCP / "tools" / "analytics.py")) + list(
        _callers(_MCP / "resources" / "registry.py")
    ):
        checked += 1
        low = doc.lower()
        assert "project" in low and (
            "release" in low
        ), (
            f"{fname}:{func} calls a release-scopeable endpoint but its "
            f"docstring does not say the answer is project-wide and that the "
            f"UI can narrow it — so nothing tells a caller the two surfaces "
            f"legitimately disagree"
        )
    assert checked >= 4, (
        f"expected to find the MCP surfaces on release-scopeable endpoints, "
        f"found {checked} — if the parser stopped matching, this test is "
        f"passing without checking anything"
    )


def test_the_cli_has_no_divergence_to_document():
    """The epic said "MCP and CLI". Only MCP is affected.

    The CLI calls no analytics endpoint at all, so it has no release-scopeable
    surface and nothing to caveat. Pinned so nobody adds a CLI analytics
    command without noticing it inherits this decision.
    """
    cli = Path(__file__).resolve().parents[3] / "cli"
    hits = [
        p.name
        for p in cli.rglob("*.py")
        if "/api/v1/analytics" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert not hits, (
        f"the CLI now calls analytics endpoints ({hits}) — it has inherited "
        f"the release-scope divergence and needs the same caveat MCP carries"
    )
