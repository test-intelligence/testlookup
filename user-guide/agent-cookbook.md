# Agent cookbook

Recipes for driving TestLookup from an AI agent — Claude Code, Claude Desktop, Cursor, or any MCP-capable client — through the [MCP server](cli-sdk-mcp.md#the-mcp-server). The server exposes **58 tools** covering both the read path (runs, failures, flakiness, release gates) and, new in US-14.1, the **write path**: an agent can now complete a triage loop end-to-end — investigate a failure, correct a wrong AI classification, propose a quarantine, file the Jira ticket, and re-route the failure to the right engineer.

Everything here is **local-first**: the MCP server talks to *your* TestLookup backend on *your* network. Your test data never leaves your infrastructure — the recipes work fully offline against a default `AI_OFFLINE_MODE=true` deployment with no LLM configured at all (the agent on your side does the reasoning; TestLookup supplies the data and the actions).

## Connecting your agent

The server lives in the repo's `mcp/` directory and supports two transports:

- **stdio** — the client launches `python mcp/server.py` as a subprocess. Use for desktop clients and IDEs on the same machine.
- **SSE** — `python mcp/server.py --transport sse` listens on port **8002** (`http://your-host:8002/sse`). Use for networked agents and CI. Put it behind a reverse proxy with TLS outside a lab.

It authenticates to the backend as a real TestLookup user via `TESTLOOKUP_USERNAME` / `TESTLOOKUP_PASSWORD` (JWT auto-login, transparent re-auth on expiry). **Create a dedicated user for your agent** and give it exactly the role its job needs — see [Security model](#security-model-rbac-and-audit) below.

### Claude Code

Add to your project's `.mcp.json` (or run `claude mcp add`):

```json
{
  "mcpServers": {
    "testlookup": {
      "command": "python",
      "args": ["/absolute/path/to/testlookup/mcp/server.py"],
      "env": {
        "TESTLOOKUP_API_URL": "http://localhost:8000",
        "TESTLOOKUP_USERNAME": "agent-bot",
        "TESTLOOKUP_PASSWORD": "your-pass"
      }
    }
  }
}
```

### Claude Desktop

Same block under `mcpServers` in `claude_desktop_config.json` (**Settings → Developer → Edit Config**).

### Cursor

Same block in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global).

### Networked / CI agents (SSE)

```json
{
  "mcpServers": {
    "testlookup": { "url": "http://your-host:8002/sse" }
  }
}
```

Credentials are configured on the server process for SSE (env vars where it runs), not per-client.

| Variable | Default | Purpose |
|---|---|---|
| `TESTLOOKUP_API_URL` | `http://localhost:8000` | Backend URL |
| `TESTLOOKUP_USERNAME` / `TESTLOOKUP_PASSWORD` | — | The agent's TestLookup login |
| `TESTLOOKUP_REQUEST_TIMEOUT` | `120.0` | HTTP timeout (seconds) |

## Security model: RBAC and audit

The MCP server holds **no privileges of its own** — every tool call becomes a REST call authenticated as the configured user, and the backend enforces role and project membership server-side exactly as it does for the web UI:

- Quarantine writes (propose / approve / reject / release / promote) require **QA_LEAD+**; defect creation requires **QA_ENGINEER+**; reassignment requires **QA_LEAD/ADMIN on the failure's project**. An agent logged in as a VIEWER simply gets a 403 — the tools surface the backend's verdict rather than working around it.
- Every write lands in the audit trail under the agent's user id (quarantine transitions write settings-audit entries; corrections write `ai_feedback` rows; defects record their creator), so "what did the agent do last night?" is answerable from **Settings → Audit**.
- Humans stay in the loop by design: `propose_quarantine` files a *proposal* for QA Lead review (it cannot quarantine directly), `create_defect` and `promote_ready_quarantines` default to previews/dry-runs, and tool descriptions instruct the agent to show you the preview before acting.

---

## Recipe 1 — Triage the latest run

> **Prompt:** "Last night's CI run for *payments-service* is red. Triage it: what actually broke, what's just flaky, and who should look at each real failure?"

**What fires:**

1. `list_projects` → resolve the project id.
2. `list_test_runs` → find the latest run.
3. `get_run_intelligence` — AI summary, clusters, risk score in one call.
4. `get_failure_clusters` — group the failures by root cause.
5. `get_flaky_tests` / `list_quarantine_requests` — separate known-flaky from real.
6. For each real failure: `get_assignment_options` → `assign_failure` to route it to the suite owner or the engineer you name.

**Outcome:** a cluster-by-cluster verdict ("1 product bug, 2 infra failures, 3 known-flaky") and the real failures sitting in the right people's [My Failures](triaging-failures.md#your-inbox-my-failures) inboxes.

## Recipe 2 — Investigate a cluster and correct a bad classification

> **Prompt:** "Dig into the `ConnectionResetError` cluster in run #482. The AI labelled it INFRASTRUCTURE but I think the new connection-pool change is a product bug — verify and fix the classification."

**What fires:**

1. `get_failure_clusters` / `get_deep_findings` on the run — the cluster's representative errors.
2. `get_test_case` (with `include_steps=True`) — the exact failing assertion and stack trace.
3. `trigger_ai_analysis` — fresh root-cause pass if the evidence is stale.
4. `correct_classification(project_id, fingerprint, corrected_category="PRODUCT_BUG", comment=...)` — overrides the verdict.

**Outcome:** the analysis row now says PRODUCT_BUG, the stale verdict is evicted from the analysis cache so similar failures aren't mislabelled, and the correction is stored as a training signal for the next fine-tune cycle ([correcting the AI](triaging-failures.md#correcting-the-ai-and-why-its-worth-doing)).

## Recipe 3 — Quarantine a flaky test and file the Jira ticket

> **Prompt:** "`test_checkout_retries` has flipped 6 times this week. Propose it for quarantine with the flip history as rationale, and file a Jira bug so it doesn't get lost."

**What fires:**

1. `get_flaky_tests` / `get_test_step_flips` — gather the flip evidence.
2. `propose_quarantine(project_id, fingerprint, reason="flipped 6x over 40 runs this week, step 'submit order' is the flicker point")` — files a **proposal**; the test stays in rotation until a QA Lead approves.
3. `approve_quarantine(request_id)` — *if* your agent user is a QA Lead and you tell it to approve; otherwise the proposal waits in the [review queue](flaky-tests.md#the-quarantine-workflow).
4. `create_defect(project_id, fingerprint=..., dry_run=True)` — shows you the prefilled Jira summary/description and whether an open ticket already exists (dedup).
5. After you confirm: `create_defect(..., dry_run=False)` — files the issue, or posts a "recurred in build X" comment on the existing one.

**Outcome:** the test is out of your release signal (once approved), and a deduplicated Jira ticket carries the full failure context.

## Recipe 4 — Release-readiness Q&A before a deploy

> **Prompt:** "We want to ship 2.9.0 at 3pm. Is run #519 safe? If it's a conditional go, what exactly is the condition — and is anything in the failure list quarantined?"

**What fires:**

1. `check_run_release_readiness(run_id)` — GO / CONDITIONAL_GO / NO_GO with risk score and blocking issues.
2. `check_release_readiness(project_id)` — the wider project-window rollup.
3. `get_decision_trail(run_id)` — *why* the AI reached each verdict, engine by engine.
4. The `testlookup://projects/{id}/quarantine-manifest` resource — which failures are quarantine-excused.
5. `get_dashboard_metrics` / `get_test_trends` — trend context ("worse than yesterday?").

**Outcome:** a sourced go/no-go answer you can paste into the release channel — this recipe is read-only by design. See [Release gates](release-gates.md) for how the verdict is computed.

## Recipe 5 — Weekly flaky-debt review

> **Prompt:** "It's Friday. Review our flaky debt: what's quarantined and stale past its SLA, what's ready to come back, and release the ones that have proven themselves. Then make sure quarantine-lifecycle notifications are on."

**What fires:**

1. `get_quarantine_stats` + `list_quarantine_requests(live_only=True)` — the current debt, including `stale` (past the SLA) and `ready_to_promote` (hit the consecutive-pass threshold) flags.
2. The `testlookup://projects/{id}/quarantine-manifest` resource — the same lifecycle flags as CI sees them.
3. `promote_ready_quarantines(project_id, dry_run=True)` — lists the promotion candidates with their pass streaks. **Review this with the human.**
4. `promote_ready_quarantines(project_id, dry_run=False)` — releases them (max 10 per call, each reported individually); or `release_quarantine(request_id, reason)` for hand-picked ones.
5. For stale entries: `list_defects` / `create_defect` to make sure each has an owner and a ticket.
6. `get_transition_policy` → `set_transition_policy(project_id, enabled_events=[..., "test.quarantine_stale", "test.ready_to_unquarantine"])` — so next week's review starts from notifications instead of a manual sweep.

**Outcome:** recovered tests back in rotation, stale quarantines escalated with tickets, and the team subscribed to the lifecycle events that keep the debt visible. Background: [Flaky tests & quarantine](flaky-tests.md).

---

## Tips

- **Start every session with `health_check`** — it verifies connectivity and reports per-dependency backend health.
- **Fingerprints are the lingua franca.** Failure, flaky, and quarantine tools all identify tests by the stable fingerprint (`sha256(class::test)[:16]`) shown in their output — pass it between tools verbatim.
- **Prefer dry runs in prompts.** Phrasing like "show me what you *would* release" maps onto the tools' `dry_run` parameters and keeps the human-approval step meaningful.
- **Write-tool results are structured.** Failures come back as `{ok: false, status_code, detail}` with the backend's real reason (role missing, state conflict, validation) — an agent can read `detail` and tell you exactly what it wasn't allowed to do.
