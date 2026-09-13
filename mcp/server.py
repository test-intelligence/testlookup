"""
TestLookup — MCP Server
==========================

Exposes tools, resources, and prompt templates to MCP clients
(AI Desktop Clients, IDEs, CI pipelines).

Tool Domains:
  - Auth & Health (login, health_check)
  - Projects (list, get, create, metrics)
  - Runs & Test Cases (list, get, filter)
  - Run Intelligence (show, refresh, summary)
  - Deep Investigation (trigger, status, clusters, findings)
  - Global Search (keyword + entity-type filtering)
  - Reports (release readiness, share links)
  - Metrics & Analytics (dashboard, trends, flaky, categories)
  - AI Analysis (trigger root-cause analysis)
  # Tier 2 item 7 additions — enterprise surface parity
  - Decision Trail (explain why the AI made every stage + per-test decision)
  - Compliance Packs (signed audit ZIPs for regulated releases)
  - Flaky Quarantine (review queue, approve, reject — QA Lead workflow)
  - LLM Cost Budget (workspace overview, per-project usage + quota)
  - Defects (list by project with severity + Jira link)
  - Governance (release gate policies, saved views, digest subs,
                ownership rules, feature flags + status check)
  # PMF US-14.1 — write path (agents close the triage loop)
  - Quarantine writes (propose, release, bulk-promote ready tests)
  - Defect creation (one-click Jira / webhook with dry-run preview)
  - Classification corrections (feedback → training signal)
  - Failure assignment (reassign + valid-assignee lookup)
  - Notification policy (transition-policy get/set)
  # Agentic plan AI-5 — MCP-first agent workflows
  - Investigator agent (start/poll/list shadow-mode investigations)
  - Fix-outcome learning loop (record merged/reverted fix outcomes)
  # Architecture E8.4 — human review gate
  - Review queue (list AI reports awaiting review; read-only, no accept/reject)

Transport: stdio (default) or SSE
Auth:      stdio uses configured credentials; SSE requires each caller's bearer token

Usage:
    python server.py                    # stdio (Desktop Client)
    python server.py --transport sse    # SSE on port 8002

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
"""

from __future__ import annotations

import sys
import os

# Ensure mcp/ directory is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # type: ignore[import]
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

import client as api
from config import settings
from token_verifier import TestLookupTokenVerifier, build_auth_settings

from tools import auth, projects, runs, metrics, analytics, analysis, release
from tools import intelligence, deep, search, reports
# Tier 2 item 7 — enterprise surface parity.
from tools import decision_trail, compliance_pack, quarantine, billing, defects, governance
# PMF US-14.1 — write-path tools so agents can close the triage loop.
from tools import assignments, feedback, notifications
# Agentic plan AI-5 — Investigator over MCP.
from tools import investigations
from tools import reviews
from resources import registry
from prompts import templates

mcp = FastMCP(
    name="TestLookup",
    host="0.0.0.0",
    port=8002,
    token_verifier=TestLookupTokenVerifier(),
    auth=build_auth_settings(),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            value.strip()
            for value in settings.mcp_allowed_hosts.split(",")
            if value.strip()
        ],
        allowed_origins=[
            value.strip()
            for value in settings.mcp_allowed_origins.split(",")
            if value.strip()
        ],
    ),
    instructions=(
        "You are connected to TestLookup, a 360° software testing intelligence platform. "
        "You can query test quality metrics, investigate failures, check release readiness, "
        "view run intelligence with AI summaries, trigger deep analysis pipelines, "
        "search across all entities, and export reports. "
        "Start with `health_check` to verify connectivity, then `list_projects` to discover "
        "available projects. Use `get_run_intelligence` for AI-powered run analysis, "
        "`trigger_deep_analysis` for multi-agent investigation, and `global_search` "
        "to find anything in the system.\n\n"
        "Enterprise-QA surface (Tier 2 item 7): "
        "`get_decision_trail` explains *why* the AI made every pipeline decision for a run. "
        "`list_compliance_packs` and `generate_compliance_pack` manage audit ZIPs for "
        "regulated releases. `list_quarantine_requests`, `approve_quarantine`, and "
        "`reject_quarantine` drive the flaky-test review queue. `get_billing_overview` "
        "shows LLM spend against quotas. `list_defects` + `list_release_gate_policies` + "
        "`list_ownership_rules` expose the governance surface. `check_feature_flag` "
        "resolves a flag for the current caller.\n\n"
        "Write path (US-14.1) — complete the triage loop end-to-end: "
        "`propose_quarantine` files a proposal for QA Lead review (never a direct "
        "quarantine), `release_quarantine` ends one early, and "
        "`promote_ready_quarantines` (dry_run first!) bulk-releases tests that hit "
        "their pass streak. `create_defect` files a Jira issue or webhook event for a "
        "failure signature — call it with dry_run=True and show the human the preview "
        "before creating. `correct_classification` fixes a wrong AI verdict and feeds "
        "the training loop. `assign_failure` (+ `get_assignment_options`) re-routes a "
        "failure to the right engineer. `get_transition_policy` / "
        "`set_transition_policy` manage per-project notification events. All writes "
        "run under YOUR login's server-side RBAC and are audit-logged with that "
        "identity — prefer dry_run variants and confirm destructive actions with the "
        "user first.\n\n"
        "Investigator agent (AI-5): `start_investigation` launches the shadow-mode "
        "hypothesis-loop Investigator for a run (it diagnoses, never acts — 403/409/429 "
        "come back as structured, actionable results), `get_investigation` polls the "
        "detail (hypothesis boards + verdict), `list_investigations` shows a project's "
        "recent ones, and the `testlookup://runs/{run_id}/investigation` resource "
        "carries the latest investigation for a run. Close the learning loop with "
        "`record_fix_outcome` once a fix informed by a diagnosis merges (or is "
        "reverted) — it lands as a human-indirect training signal for the classifier. "
        "The `fix_this_flaky_test` prompt packages the whole flaky-fix workflow.\n\n"
        "Human review (E8.4): AI reports are drafts until a person accepts them. "
        "Report tools end with `review_state` and the AI disclaimer; pass both on to "
        "the user and never present a `pending_review` or `unknown` report as settled. "
        "`list_pending_reviews` shows a project's open queue. Accepting or rejecting "
        "a review is deliberately not available through MCP."
    ),
)


@mcp.custom_route("/health/live", methods=["GET"])
async def live(_request):  # noqa: ANN001
    """Process liveness; intentionally does not contact a dependency."""
    return JSONResponse({"status": "alive"})


@mcp.custom_route("/health/ready", methods=["GET"])
async def ready(_request):  # noqa: ANN001
    """Readiness follows the backend auth authority used by every handshake."""
    if await api.backend_ready():
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "unready"}, status_code=503)

# ── Register Tools ────────────────────────────────────────────────────────────
auth.register(mcp)
projects.register(mcp)
runs.register(mcp)
metrics.register(mcp)
analytics.register(mcp)
analysis.register(mcp)
release.register(mcp)
# New domains
intelligence.register(mcp)
deep.register(mcp)
search.register(mcp)
reports.register(mcp)
# Tier 2 item 7 — enterprise surface parity with the web UI.
decision_trail.register(mcp)   # Tier 0B — explain why the AI did X
compliance_pack.register(mcp)  # Tier 1-4 — audit ZIP lifecycle
quarantine.register(mcp)       # Tier 1-3 — flaky review queue
billing.register(mcp)          # Tier 1-2 — LLM cost budget
defects.register(mcp)          # defect list reads + one-click Jira create (US-14.1)
governance.register(mcp)       # policies, saved views, digests, ownership, flags
# PMF US-14.1 — write-path tools (agents close the triage loop).
feedback.register(mcp)         # correct_classification + record_fix_outcome → training signals
assignments.register(mcp)      # failure reassignment (my-failures inbox)
notifications.register(mcp)    # transition-notification policy get/set
# Agentic plan AI-5 — Investigator agent over MCP (shadow-mode).
investigations.register(mcp)   # start/poll/list investigations
reviews.register(mcp)          # E8.4 review queue (read-only)

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
