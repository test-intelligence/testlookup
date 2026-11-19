"""Every MCP tool's name, parameter names and required parameters match a snapshot (GAP-E09).

Contract snapshots exist for six tools (``snapshots/*.tool.json``). For the
rest, removing or renaming a parameter failed nothing, while agent prompts and
saved MCP client configs are written against those names. This snapshot
covers the whole catalogue the real server publishes (``server.mcp``), but
only names: no descriptions or types, so a wording edit does not churn it.

To change it on purpose, rerun with ``UPDATE_SNAPSHOTS=1`` and review the diff
of ``snapshots/tool_catalogue.json`` in the pull request.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

SNAPSHOT = Path(__file__).parent / "snapshots" / "tool_catalogue.json"


def _catalogue() -> dict:
    import server

    tools = asyncio.run(server.mcp.list_tools())
    catalogue = {}
    for tool in tools:
        # The published (wire) form, as the per-tool contract snapshots read it.
        schema = tool.model_dump(mode="json", by_alias=True).get("inputSchema") or {}
        catalogue[tool.name] = {
            "params": sorted((schema.get("properties") or {}).keys()),
            "required": sorted(schema.get("required") or []),
        }
    return dict(sorted(catalogue.items()))


def test_the_tool_catalogue_matches_its_snapshot():
    actual = _catalogue()
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_bytes(
            (json.dumps(actual, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    changed = {
        name: {"snapshot": expected[name], "published": actual[name]}
        for name in sorted(set(actual) & set(expected))
        if actual[name] != expected[name]
    }
    assert not (added or removed or changed), (
        "The published MCP tool catalogue changed: "
        f"added tools {added}, removed tools {removed}, changed parameters {changed}. "
        "Agents call tools by these names. If the change is intended, rerun with "
        "UPDATE_SNAPSHOTS=1 and review the diff of mcp/tests/snapshots/tool_catalogue.json."
    )


def test_the_snapshot_is_well_formed():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert expected, "the snapshot is empty"
    assert list(expected) == sorted(expected), (
        "tool names must be sorted for a stable diff"
    )
    for name, entry in expected.items():
        assert entry["params"] == sorted(entry["params"]), name
        assert set(entry["required"]) <= set(entry["params"]), (
            f"{name}: a required parameter is not a parameter"
        )
