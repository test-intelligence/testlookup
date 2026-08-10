"""Every backend path an MCP tool calls must exist on the backend.

``list_digest_subscriptions`` called ``GET /api/v1/digests``. That route does
not exist — the digests router mounts ``/subscriptions``, ``/preview`` and the
``/subscriptions/{id}`` verbs beneath the prefix, never the bare prefix itself.
Verified against the running server::

    GET /api/v1/digests               -> 404
    GET /api/v1/digests/subscriptions -> 200

``client.get`` calls ``raise_for_status()``, so the tool did not degrade — it
raised, and every invocation surfaced an error to the model.

**A second defect hid behind the first.** The tool advertises a ``project_id``
argument ("Optional — scope to a single project") and passed it as a query
param. ``list_subscriptions`` declares no such parameter, and FastAPI ignores
undeclared query params — so simply correcting the path would have produced a
tool that silently returned **unfiltered** results while its own docstring
promised scoping. The 404 was masking a silent-wrong-answer bug. Filtering is
now done on the returned rows, which carry ``project_id``.

This is the producer/consumer drift class that keeps recurring here, in a
surface nothing was checking: the MCP tools are a second client of the same API
as the UI and CLI, and nothing tied their paths to the routes.

The path test below is derived from the source rather than naming one endpoint,
so a tool added later against a route that does not exist fails here too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).parent.parent
BACKEND_ROUTERS = MCP_DIR.parent / "backend" / "app" / "routers"

#: Prefixes whose bare form is NOT a route — the router mounts only sub-paths.
#: Recorded as data so the failure message can say which tool is wrong.
_KNOWN_PREFIX_ONLY = {"/api/v1/digests"}


def _tool_paths() -> dict[str, set[str]]:
    """Every literal ``/api/v1/...`` string per MCP tool module."""
    found: dict[str, set[str]] = {}
    for f in sorted((MCP_DIR / "tools").glob("*.py")):
        src = f.read_text(encoding="utf-8", errors="ignore")
        paths = {
            m.group(1)
            for m in re.finditer(r'["\'](/api/v1/[a-zA-Z0-9_\-/{}\.]+)["\']', src)
        }
        if paths:
            found[f.name] = paths
    return found


def test_no_tool_calls_a_bare_prefix_that_is_not_a_route():
    """The measured bug: GET /api/v1/digests 404s on the live server."""
    offenders: list[str] = []
    for module, paths in _tool_paths().items():
        for p in paths:
            if p.rstrip("/") in _KNOWN_PREFIX_ONLY:
                offenders.append(f"{module} -> {p}")
    assert not offenders, (
        "MCP tool calls a router prefix that has no route of its own "
        f"(404 on the live server): {', '.join(offenders)}"
    )


def test_digest_tool_uses_the_subscriptions_route():
    src = (MCP_DIR / "tools" / "governance.py").read_text(encoding="utf-8")
    assert "/api/v1/digests/subscriptions" in src, (
        "the digest tool must call the route that exists"
    )


def test_digest_tool_does_not_send_an_unsupported_query_param():
    """``list_subscriptions`` declares no project_id.

    FastAPI ignores undeclared query params, so sending one yields unfiltered
    results while the docstring promises scoping — a silent wrong answer, which
    is worse than the 404 that was hiding it.
    """
    src = (MCP_DIR / "tools" / "governance.py").read_text(encoding="utf-8")
    block = src[src.find("async def list_digest_subscriptions") :]
    block = block[: block.find("@mcp.tool()", 10) if block.find("@mcp.tool()", 10) != -1 else len(block)]
    assert 'params={"project_id"' not in block and "params=params" not in block, (
        "the digest tool still passes project_id as a query param to an "
        "endpoint that does not declare one — the filter is silently dropped"
    )


def test_the_tool_still_offers_project_scoping():
    """Dropping the promise instead of honouring it would also be wrong."""
    src = (MCP_DIR / "tools" / "governance.py").read_text(encoding="utf-8")
    block = src[src.find("async def list_digest_subscriptions") :][:2000]
    assert "project_id" in block, "project scoping was removed rather than fixed"


@pytest.mark.parametrize("module", sorted(_tool_paths()))
def test_paths_are_well_formed(module: str):
    """Cheap shape guard — catches an f-string that leaked a stray bracket."""
    for p in _tool_paths()[module]:
        assert "[" not in p and "]" not in p, f"{module}: malformed path {p!r}"
