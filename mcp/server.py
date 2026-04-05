"""
TestLookup — MCP Server
==========================

Exposes 20 tools, 10 resources, and 6 prompt templates to MCP clients
(AI Desktop Clients, IDEs, CI pipelines).

Transport: stdio (default) or SSE
Auth:      JWT via TESTLOOKUP_USERNAME / TESTLOOKUP_PASSWORD env vars

Usage:
    python server.py                    # stdio (Desktop Client)
    python server.py --transport sse    # SSE on port 8001

MCP Client config:
    {
      "mcpServers": {
        "testlookup": {
          "command": "python",
          "args": ["/path/to/mcp/server.py"],
          "env": {
            "TESTLOOKUP_API_URL": "http://localhost:8000",
            "TESTLOOKUP_USERNAME": "your-user",
            "TESTLOOKUP_PASSWORD": "your-pass"
          }
        }
      }
    }

SSE transport (CI / web clients):
    python server.py --transport sse --port 8002
    Client connects to: http://localhost:8002/sse
"""

from __future__ import annotations

import sys
import os

# Ensure mcp/ directory is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # type: ignore[import]

from tools import auth, projects, runs, metrics, analytics, analysis, release
from resources import registry
from prompts import templates

mcp = FastMCP(
    name="TestLookup",
    instructions=(
        "You are connected to TestLookup, a 360° software testing intelligence platform. "
        "You can query test quality metrics, investigate failures, check release readiness, "
        "and trigger AI root-cause analysis for failing tests. "
        "Start with `list_projects` to discover available projects, then use the project_id "
        "in subsequent tool calls. Use `health_check` to verify connectivity."
    ),
)

# ── Register Tools ────────────────────────────────────────────────────────────
auth.register(mcp)
projects.register(mcp)
runs.register(mcp)
metrics.register(mcp)
analytics.register(mcp)
analysis.register(mcp)
release.register(mcp)

# ── Register Resources ────────────────────────────────────────────────────────
registry.register(mcp)

# ── Register Prompts ──────────────────────────────────────────────────────────
templates.register(mcp)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TestLookup MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8002,
        help="Port for SSE transport (default: 8002)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        # Configure host/port on the instance before running.
        # FastMCP.run() no longer accepts port directly — it reads from settings.
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
