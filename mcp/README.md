# TestLookup MCP Server

The MCP (Model Context Protocol) server exposes TestLookup's full API surface to AI assistants, IDEs, and CI agents. It ships **36 tools**, **9 resources**, and **6 prompt workflows** across two transports (stdio for local clients, SSE for remote/CI).

## Quick start

```bash
# Install dependencies
make mcp-install

# Start in stdio mode (for Claude Desktop / Cursor / local IDE)
make mcp-start

# Start in SSE mode (for web clients / CI agents)
make mcp-sse              # or: make mcp-sse-docker
```

The MCP server talks to the TestLookup backend at `http://localhost:8000` by default. Auth is via username/password (JWT auto-login) or can be pre-configured with a token.

## Client configuration

### Claude Desktop (stdio)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "testlookup": {
      "command": "python",
      "args": ["/absolute/path/to/testlookup/mcp/server.py"],
      "env": {
        "TESTLOOKUP_API_URL": "http://localhost:8000",
        "TESTLOOKUP_USERNAME": "admin",
        "TESTLOOKUP_PASSWORD": "your-password"
      }
    }
  }
}
```

### VS Code (Cline / Continue)

For Cline, add to your `.vscode/mcp.json`:

```json
{
  "servers": {
    "testlookup": {
      "command": "python",
      "args": ["/absolute/path/to/testlookup/mcp/server.py"],
      "env": {
        "TESTLOOKUP_API_URL": "http://localhost:8000",
        "TESTLOOKUP_USERNAME": "admin",
        "TESTLOOKUP_PASSWORD": "your-password"
      }
    }
  }
}
```

For Continue, add to `~/.continue/config.json` under `mcpServers`.

### CI agents (SSE)

Point any MCP-capable CI agent at the SSE endpoint:

```json
{
  "mcpServers": {
    "testlookup": {
      "url": "http://your-host:8002/sse"
    }
  }
}
```

The SSE transport runs on port 8002 by default. In production, put it behind a reverse proxy with TLS.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TESTLOOKUP_API_URL` | `http://localhost:8000` | Backend URL |
| `TESTLOOKUP_USERNAME` | -- | Auto-login username |
| `TESTLOOKUP_PASSWORD` | -- | Auto-login password |
| `TESTLOOKUP_REQUEST_TIMEOUT` | `120.0` | HTTP timeout (seconds) |

## Demo prompts

Copy-paste these into Claude Desktop or your MCP client to verify the integration works:

```
List all QA projects in TestLookup
```

```
Show me the dashboard metrics for the first project over the last 7 days
```

```
What are the top 5 flaky tests across all projects?
```

```
Show the failure category distribution for project <name> this week
```

```
Check release readiness for project <name>
```

```
Get the AI decision trail for the most recent failed run
```

```
Search for all tests with "payment" in the name that failed in the last 30 days
```

```
Run a deep investigation on the latest run for project <name>
```

```
Show the LLM cost budget overview -- which projects are closest to their cap?
```

```
List open quarantine proposals and approve the one with the highest flip rate
```

## Tool reference (36 tools)

### Auth and health

| Tool | Parameters | Description |
|------|-----------|-------------|
| `login` | `username`, `password` | Authenticate and cache JWT token |
| `health_check` | -- | Backend connectivity, version, LLM provider, dependency status |

### Projects

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_projects` | -- | All active projects with integration config |
| `get_project` | `project_id` | Full project config with Jira/OCP/Splunk details |
| `create_project` | `name`, `slug`, ... | Register new project |

### Test runs and cases

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_test_runs` | `project_id`, `status?`, `days?`, `page?`, `size?` | Runs newest-first with pass rate |
| `get_run_details` | `run_id` | Aggregated stats, timeline, OCP context |
| `list_test_cases` | `run_id`, `status?`, `suite?`, `page?`, `size?` | Filter by status/suite with error snippets |
| `get_test_case` | `run_id`, `test_id` | Full test record with Allure labels |

### Metrics and quality KPIs

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_dashboard_metrics` | `project_id`, `days?` | Pass rate, defects, flakiness, release readiness signal |
| `get_test_trends` | `project_id`, `days?` | Daily pass/fail/skip trend with direction |

### Advanced analytics

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_flaky_tests` | `project_id`, `days?`, `limit?` | Flakiness leaderboard (5-95% failure rate) |
| `get_failure_categories` | `project_id`, `days?` | Distribution by root cause category |
| `get_top_failing_tests` | `project_id`, `days?`, `limit?` | Highest raw failure count |
| `get_coverage_report` | `project_id`, `days?` | Suite coverage: unique tests, per-suite pass rates |
| `get_defects` | `project_id`, `resolution_status?`, `page?`, `size?` | Defects with Jira links |
| `get_ai_analysis_summary` | `project_id`, `days?` | AI triage coverage and confidence distribution |

### AI root-cause analysis

| Tool | Parameters | Description |
|------|-----------|-------------|
| `trigger_ai_analysis` | `test_case_id`, `service_name?`, `ocp_pod_name?` | LangChain ReAct agent (10-60s) |
| `search_tests` | `query`, `project_id?`, `status?`, `days?` | Full-text search across tests |

### Run intelligence and deep investigation

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_run_intelligence` | `run_id` | Unified snapshot: summary, clusters, release decision, risk score |
| `refresh_intelligence` | `run_id` | Force-refresh cached snapshot |
| `get_run_summary` | `run_id`, `mode?` | Narrative summary (executive/developer/manager) |
| `trigger_deep_analysis` | `run_id` | Queue multi-agent deep investigation; returns task ID |
| `get_pipeline_status` | `run_id`, `workflow_type?` | Pipeline execution status and stage summary |
| `get_failure_clusters` | `run_id` | Failure clusters with representative errors |
| `get_deep_findings` | `run_id` | Root-cause findings per cluster |

### Global search

| Tool | Parameters | Description |
|------|-----------|-------------|
| `global_search` | `query`, `entity_types?`, `project_id?` | Search all entities: tests, runs, suites, defects, releases |

### Release and reports

| Tool | Parameters | Description |
|------|-----------|-------------|
| `check_release_readiness` | `project_id`, `days?` | GO/CONDITIONAL_GO/NO_GO with risk score and blocking issues |
| `create_share_link` | `run_id`, `expires_days?`, `layout?` | Time-limited share URL for run reports |

### Decision trail

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_decision_trail` | `run_id` | Full AI decision audit: engine selection, fallbacks, cost, per-test routing |

### Compliance packs

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_compliance_packs` | `release_id` | Generated audit ZIPs with manifest SHA-256 |
| `generate_compliance_pack` | `release_id`, `notes?`, `retention_days?` | Generate signed audit ZIP (QA_LEAD+ required) |

### Flaky quarantine

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_quarantine_requests` | `project_id`, `status?`, `live_only?`, `limit?` | Review queue with flip rates |
| `get_quarantine_stats` | `project_id` | Count per status |
| `approve_quarantine` | `request_id`, `notes?`, `quarantine_duration_days?` | Approve and activate quarantine |
| `reject_quarantine` | `request_id`, `notes?` | Reject proposal |

### LLM cost budget

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_billing_overview` | -- | Workspace LLM spend this month |
| `get_project_llm_usage` | `project_id` | Current-period cost, tokens, cap utilization |
| `get_project_llm_quota` | `project_id` | Budget config: cap, action, enabled |

### Governance

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_release_gate_policies` | `project_id?` | Active GO/NO_GO rules |
| `list_saved_views` | `project_id?`, `page?` | Saved filter views |
| `list_digest_subscriptions` | `project_id?` | Scheduled quality report subscriptions |
| `list_ownership_rules` | `project_id?` | Service ownership glob patterns |
| `list_feature_flags` | -- | All feature flags (admin only) |
| `check_feature_flag` | `key`, `project_id?` | Evaluate flag for caller's context |

## Resource reference (9 resources)

Resources are passive, read-only context URIs that AI clients can fetch as background knowledge.

| URI | Description |
|-----|-------------|
| `testlookup://projects` | Index of all active projects |
| `testlookup://projects/{project_id}` | Full project record + integrations |
| `testlookup://projects/{project_id}/metrics` | Live dashboard KPIs (last 7 days) |
| `testlookup://projects/{project_id}/runs/latest` | 10 most recent runs |
| `testlookup://projects/{project_id}/flaky-tests` | Flakiness leaderboard (30 days, top 20) |
| `testlookup://projects/{project_id}/defects/open` | Open defects |
| `testlookup://runs/{run_id}` | Full run record + aggregated stats |
| `testlookup://runs/{run_id}/failures` | Failed/broken test cases (up to 200) |
| `testlookup://tests/{run_id}/{test_id}` | Full test case + error + Allure labels |

## Prompt reference (6 prompts)

Prompts are reusable investigation workflows that expand into sequences of tool calls.

| Prompt | Parameters | Description |
|--------|-----------|-------------|
| `investigate_failure` | `test_case_id`, `project_id` | Full RCA: context gathering, AI analysis, structured findings report |
| `release_readiness_report` | `project_id`, `release_version?` | Executive go/no-go: metrics, trends, categories, blockers, flakiness risk |
| `weekly_quality_digest` | `project_id` | Team digest: pass rate trend, top failures, flakiness watch, action items |
| `flakiness_investigation` | `project_id`, `threshold_pct?` | Prioritised remediation: quick wins vs strategic improvements |
| `defect_triage_session` | `project_id` | Structured triage: review defects, identify duplicates, produce P1/P2/P3 list |
| `suite_health_check` | `project_id`, `suite_name` | Suite analysis: coverage, worst tests, failure patterns, health score |

## Hardening notes (known gaps)

These are documented for transparency and tracked as follow-up work:

- **Input validation**: tools accept any string for enum fields (status, resolution_status); backend validates but MCP tools don't pre-validate
- **Token lifecycle**: session token is module-level mutable state; no explicit logout; relies on 401 detection for re-auth
- **Output format**: tools return markdown strings, not structured JSON -- adequate for AI clients but less ideal for programmatic consumers
- **Concurrency**: the stdio transport is single-threaded; SSE can serve multiple clients but the auth token cache is process-global
- **Rate limiting**: no per-connection rate limit on the SSE transport -- deploy behind a reverse proxy in production

## Further reading

- [README.md](../README.md) -- product overview
- [ARCHITECTURE.md](../ARCHITECTURE.md) -- system architecture
- [THREAT_MODEL.md](../THREAT_MODEL.md) -- data flow and security boundaries (includes MCP)
