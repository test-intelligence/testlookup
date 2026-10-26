# TestLookup MCP Server

The MCP (Model Context Protocol) server exposes TestLookup to AI assistants, IDEs and CI agents. It ships **74 tools**, **11 resources** and **7 prompts**, over two transports: stdio for a local client and SSE for networked clients. Every tool call becomes a REST call to the TestLookup API, authenticated as a TestLookup user, so the backend's role and project checks apply unchanged.

Beyond reads, the server carries **write-path tools** so an agent can close the triage loop: propose and release quarantines, bulk-promote recovered tests, file deduplicated Jira defects (with an optional dry-run preview), correct AI classifications, reassign failures, and manage notification policy. Stdio writes run under the configured login; SSE writes run under each caller's own bearer token. Both are audit-logged with that identity. See the [agent cookbook](../user-guide/agent-cookbook.md) for worked recipes.

`start_investigation` / `get_investigation` / `list_investigations` drive the shadow-mode **Investigator agent** (it diagnoses; the calling agent acts through the write tools), the `testlookup://runs/{run_id}/investigation` resource carries a run's latest verdict, `record_fix_outcome` feeds merged or reverted fixes back to the classifier as `human_indirect` training signals, and the `fix_this_flaky_test` prompt packages the whole flaky-fix loop.

## Install (from source)

The server is not published to a package or MCP registry. Run it from a clone of the repository with Python 3.11 (the version the container image uses):

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
pip install -r mcp/requirements.txt      # or: make mcp-install
```

| Command | What it starts |
|---|---|
| `python mcp/server.py` or `make mcp-start` | stdio transport (the default), for a local client that launches the server as a subprocess |
| `python mcp/server.py --transport sse --port 8002` or `make mcp-sse` | SSE transport on `0.0.0.0:8002` (`--port` defaults to 8002) |
| `make mcp-sse-docker` (`docker compose up -d mcp`) | The `mcp` Compose service: SSE on port 8002, published on `127.0.0.1:8002`, talking to the `backend` service |

The server reads its settings from `TESTLOOKUP_*` environment variables and from a `.env` file in the working directory; [mcp/.env.example](.env.example) is a template.

## Authentication

The MCP server authenticates to the TestLookup API as a user, with a JWT access token. It does **not** accept or use API keys.

**stdio.** Set `TESTLOOKUP_USERNAME` and `TESTLOOKUP_PASSWORD`. On the first tool call the server logs in (`POST /api/v1/auth/login`), keeps the access token in memory, and logs in again once if the API answers 401. Without credentials, requests go out unauthenticated and fail until the client calls the `login` tool, which logs in for the rest of the stdio session.

**SSE.** There is no server-wide identity. Every request (the `/sse` stream and each `/messages/` post) must carry `Authorization: Bearer <access token>`, where the token is a TestLookup JWT access token. The server checks it against `GET /api/v1/auth/me` on every request, with no caching, so revocation and deactivation take effect at once. The token's `jti` owns the SSE session: a refreshed token has to reconnect. `TESTLOOKUP_USERNAME` / `TESTLOOKUP_PASSWORD` are never used as a network identity, and the `login` tool refuses to run on a network session.

To get an access token for an SSE client, log in as the user the agent should act as (access tokens last 12 hours unless the server's `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` says otherwise):

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=${TESTLOOKUP_USERNAME}" -d "password=${TESTLOOKUP_PASSWORD}" | jq -r .access_token
```

Create a dedicated user per agent and give it only the role its job needs; see [Security model](../user-guide/agent-cookbook.md#security-model-rbac-and-audit).

## Client configuration

### stdio clients (Claude Desktop, Claude Code, Cursor, VS Code and others)

Most MCP clients take a server entry of this shape. Use the Python interpreter that has `mcp/requirements.txt` installed and an absolute path to `server.py`:

```json
{
  "mcpServers": {
    "testlookup": {
      "command": "python",
      "args": ["/absolute/path/to/testlookup/mcp/server.py"],
      "env": {
        "TESTLOOKUP_API_URL": "http://localhost:8000",
        "TESTLOOKUP_USERNAME": "agent-bot",
        "TESTLOOKUP_PASSWORD": "${TESTLOOKUP_PASSWORD}"
      }
    }
  }
}
```

Where the block goes depends on the client: `claude_desktop_config.json` for Claude Desktop, `.mcp.json` for Claude Code, `.cursor/mcp.json` for Cursor. Some clients (VS Code's `.vscode/mcp.json`, for example) name the top-level key `servers` instead of `mcpServers`; check your client's documentation. Not every client expands `${...}` in `env`; if yours does not, set the variable in the environment the client starts from.

### SSE clients

```json
{
  "mcpServers": {
    "testlookup": {
      "url": "https://testlookup-mcp.example.com/sse",
      "headers": {
        "Authorization": "Bearer ${TESTLOOKUP_ACCESS_TOKEN}"
      }
    }
  }
}
```

Expose SSE only through TLS in production. The server enforces DNS-rebinding protection: the `Host` header must match `TESTLOOKUP_MCP_ALLOWED_HOSTS` (loopback only by default), so set it to your public host name, and set `TESTLOOKUP_MCP_ALLOWED_ORIGINS` if browsers connect. `GET /health/live` reports process liveness; `GET /health/ready` returns 503 while the TestLookup API is not ready.

### Environment variables

All settings use the `TESTLOOKUP_` prefix (`mcp/config.py`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `TESTLOOKUP_API_URL` | `http://localhost:8000` | TestLookup API base URL |
| `TESTLOOKUP_USERNAME` | (empty) | stdio auto-login username; never used as a network identity |
| `TESTLOOKUP_PASSWORD` | (empty) | stdio auto-login password; never used as a network identity |
| `TESTLOOKUP_REQUEST_TIMEOUT` | `120.0` | HTTP timeout for API calls, in seconds (AI analysis can take a minute) |
| `TESTLOOKUP_AUTH_TIMEOUT` | `5.0` | Timeout for SSE token validation and the readiness check, in seconds |
| `TESTLOOKUP_AUTH_MAX_CONNECTIONS` | `50` | Connection pool size for token validation |
| `TESTLOOKUP_MCP_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*,[::1]:*` | Comma-separated `Host` allowlist for SSE; `:*` allows any port |
| `TESTLOOKUP_MCP_ALLOWED_ORIGINS` | (empty) | Comma-separated browser `Origin` allowlist for SSE |

## Demo prompts

Paste these into your MCP client to check the integration:

```
List all QA projects in TestLookup
```

```
Show me the dashboard metrics for the first project over the last 7 days
```

```
What are the top 5 flaky tests in project <name>?
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
Show the LLM cost budget overview: which projects are closest to their cap?
```

```
List open quarantine proposals and approve the one with the highest flip rate
```

## Tool reference (77 tools)

A `?` marks an optional parameter.

### Auth and health

| Tool | Parameters | Description |
|------|-----------|-------------|
| `login` | `username`, `password` | Log in a local stdio session and cache its JWT; refused on network (SSE) sessions |
| `health_check` | none | API status, version, environment, uptime and per-dependency health (`/health/details`) |

### Projects

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_projects` | none | Active projects with IDs and integration keys |
| `get_project` | `project_id` | Full project record with integration configuration |
| `create_project` | `name`, `slug`, `description?`, `jira_project_key?`, `splunk_index?`, `ocp_namespace?`, `jenkins_job_pattern?` | Register a new project |

### Test runs and cases

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_test_runs` | `project_id`, `status?`, `days?`, `page?`, `size?` | Runs newest-first with pass rate |
| `get_run_details` | `run_id` | Aggregated stats, timeline, OCP context |
| `list_test_cases` | `run_id`, `status?`, `suite?`, `page?`, `size?` | Filter by status/suite with error snippets |
| `get_test_case` | `run_id`, `test_id`, `include_steps?` | Full test record with Allure labels; `include_steps=true` adds the latest run's step tree |
| `get_test_step_flips` | `run_id`, `test_id` | Step-level flakiness across retained runs: which step flipped between PASSED and FAILED, how often, in how many runs, and its current status. Read-only. |
| `get_run_timing` | `run_id`, `slowest?` | How long one run took and where the time went: wall clock, test time (summed) and parallelism, setup/test body/teardown totals, per-suite times, the slowest tests (default 10, max 100) against their baselines, and the run's duration regressions. Compact markdown plus the REST body (`/runs/{run_id}/timing`, waterfall bars left out). A null figure is "not reported", never 0. Read-only. |

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
| `get_defects` | `project_id`, `resolution_status?`, `page?`, `size?` | Defects with Jira links (paginated) |
| `list_defects` | `project_id`, `resolution_status?`, `limit?` | Defect list (lightweight, limit-based) |
| `create_defect` | `project_id`, `fingerprint?` or `cluster_id?`, `target?`, `issue_type?`, `jira_project_key?`, `assignee?`, `extra_comment?`, `dry_run?` | **Write.** One-click Jira issue (or `target="webhook"` event) for a failure signature; `dry_run=true` returns the server preview without creating anything (default `false`); dedup-first (QA_ENGINEER+) |
| `get_ai_analysis_summary` | `project_id`, `days?` | AI triage coverage and confidence distribution |
| `get_chart_data` | `group_by`, `metric?`, `project_id?`, `release_id?`, `suite_name?`, `days?`, `top_n?` | A metric over 1-2 dimensions, the same series the REST API and UI charts draw, returned verbatim with `meta`. Release- and suite-scoped; without `project_id` it covers every project you can read. `wall_clock_total`/`_p50`/`_p95` are run-level only (refused under a suite filter or a row-level `group_by`). Errors carry `code`/`param`/`allowed` |
| `get_execution_metrics` | `project_id`, `days?`, `suite_name?`, `release_id?`, `branch?`, `top?` | How long a project's runs and suites take and what got slower: wall clock and test time p50/p95 per suite against the previous window, suite and test duration regressions, the slowest tests, a duration histogram. Compact markdown plus the REST body (`/analytics/execution-metrics`). Release- and suite-scoped. A null figure is "not reported", never 0 |

The leaderboard tools above answer project-wide: the UI can narrow them to a
release and they cannot, so their numbers and a release-filtered dashboard's
legitimately differ. Use `get_chart_data` for a release- or suite-scoped number.
The UI's charts read the same endpoint, except the Trends page's pass-rate
trend card, which reads `/api/v1/metrics/trends`: it counts every run that
touched a suite whole, so under a suite filter it differs from
`get_chart_data` (which counts only the suite's own tests).

### AI root-cause analysis and test search

| Tool | Parameters | Description |
|------|-----------|-------------|
| `trigger_ai_analysis` | `test_case_id`, `service_name?`, `ocp_pod_name?`, `ocp_namespace?`, `run_id?` | Root-cause analysis for one test case (10-60 s) |
| `search_tests` | `query`, `project_id?`, `status?`, `days?`, `page?`, `size?` | Full-text search across tests with status and date filters, paginated |
| `search_test_cases` | `query`, `project_id?` | Legacy test-case-only keyword search |

### Run intelligence and deep investigation

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_run_intelligence` | `run_id` | Unified snapshot: summary, clusters, release decision, risk score |
| `refresh_intelligence` | `run_id` | Force-refresh the cached snapshot |
| `get_run_summary` | `run_id`, `mode?` | Narrative summary (`executive`, `developer` or `manager`) |
| `get_ai_history` | `run_id`, `limit?` | The stored versions of a run's AI analysis, newest first: engine and model, fallback, verdict, risk, failing tests. Returns `{ok, summary, history}` |
| `diff_ai_versions` | `run_id`, `from_version`, `to_version?` (number, `latest` or `live`) | What changed between two versions of a run's AI analysis, provenance first (an engine change, rules vs LLM, is not a product change). Returns `{ok, summary, diff}`; an unstored version number is answered without a diff |
| `compare_ai_with_previous` | `run_id`, `baseline_run_id?` | The run's AI analysis against the previous run of the same suite and branch: verdict, new and resolved root causes, tests that changed category. The summary says `deterministic summary` when no LLM wrote it. Returns `{ok, summary, verdict_regressed, compare}`; "not analysed" is not "no change" |
| `trigger_deep_analysis` | `run_id` | Queue a multi-agent deep investigation; returns a task ID |
| `get_pipeline_status` | `run_id`, `workflow_type?` | Pipeline execution status and stage summary |
| `get_failure_clusters` | `run_id` | Failure clusters with representative errors |
| `get_deep_findings` | `run_id` | Root-cause findings per cluster |

### Investigator agent

| Tool | Parameters | Description |
|------|-----------|-------------|
| `start_investigation` | `run_id` | **Write (safe).** Launch the shadow-mode hypothesis-loop Investigator for a run; 403 (policy disabled) / 409 (already running, carries the active id) / 429 (daily budget) come back as structured results (QA_ENGINEER+) |
| `get_investigation` | `investigation_id` | Full detail: status, budget vs spend, five hypothesis boards, verdict (primary cause, confidence, narrative, recommended actions) |
| `list_investigations` | `project_id`, `limit?` | Recent investigations newest-first with primary cause and confidence |

### Agents

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_agents` | none | The agent catalog: permission, whether each agent is invocable on its own, sync-eligible, report-producing |
| `get_agent` | `agent_id` | One catalog entry: input/output contracts, dependencies, how to invoke it |
| `invoke_agent` | `agent_id`, `project_id`, `test_run_id`, `mode?`, `config_overrides?`, `idempotency_key?` | **Write (side effect).** Runs one invocable agent on a stored test run and spends the project's AI budget. Always sends an `Idempotency-Key` (yours, or a new one it returns), so a retry with the same key cannot run the agent twice |
| `get_agent_invocation` | `invocation_id` | Status, attempts, output, and `review_state` of an invocation |

`INVOKABLE_AGENT_IDS` in `tools/agents.py` mirrors the backend registry; `backend/tests/test_mcp_agent_catalog_parity.py` fails when they drift.

### Human review

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_pending_reviews` | `project_id`, `limit?` | **Read-only.** AI reports in a project still awaiting human review. There is deliberately no accept or reject tool: the server forwards the caller's token, so an agent could otherwise approve its own output. |

`get_run_intelligence`, `get_run_summary` and `check_run_release_readiness` end with `review_state` and the AI disclaimer. `review_state: unknown` means the backend sent no review status; treat the content as an unreviewed draft.

### Global search

| Tool | Parameters | Description |
|------|-----------|-------------|
| `global_search` | `query`, `entity_types?`, `project_id?` | Search all entities: tests, runs, suites, defects, flaky tests, releases |

### Release and reports

| Tool | Parameters | Description |
|------|-----------|-------------|
| `check_release_readiness` | `project_id`, `days?`, `release_id?` | Project GREEN/AMBER/RED from the server's policy verdict over a time window, or a release's stored gate decision when `release_id` is given |
| `get_release_gate` | `release`, `project?`, `evaluate?` | A release's gate verdict as data (verdict, pass rate, evidence, blockers, decided_at); `release` is an id or a name / version (an ambiguous name lists the candidates); `evaluate=true` records a fresh verdict (QA lead); never evaluated is `evaluated: false` |
| `check_run_release_readiness` | `run_id` | Single-run GO/CONDITIONAL_GO/NO_GO with risk score and blocking issues |
| `create_share_link` | `run_id`, `expires_days?`, `layout?` | Time-limited share URL for a run report |

### Activity ledger

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_project_activity` | `project_id`, `category?`, `since?`, `limit?`, `cursor?` | A project's activity, newest first, as on its Activity tab (`project_id` may be the project name; `since` like `7d`); `more` + `next_cursor` when there are older events |
| `get_activity_event` | `project_id`, `event_id` | One event with its before / after diff |

### Decision trail

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_decision_trail` | `run_id` | Full AI decision audit: engine selection, fallbacks, cost, per-test routing |

### Compliance packs

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_compliance_packs` | `release_id` | Generated audit ZIPs with manifest SHA-256 |
| `generate_compliance_pack` | `release_id`, `notes?`, `retention_days?` | Generate a tamper-evident audit ZIP: SHA-256 checksum chain, signed since W11 with the deployment's Ed25519 key (`signature.json`) (QA_LEAD+) |
| `verify_compliance_pack` | `pack_id` | **Read-only** (a POST that writes nothing). Check a stored pack: every file against its manifest, the manifest against the digest recorded at generation, and the signature. Answers `verified`, `tampered` (naming each file that does not match) or `unreadable` (storage could not be read: not a verdict) |

### Flaky quarantine

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_quarantine_requests` | `project_id?`, `status?`, `live_only?`, `limit?` | Review queue with flip rates, across the projects you can read unless `project_id` is given |
| `get_quarantine_stats` | `project_id?` | Count per status |
| `approve_quarantine` | `request_id`, `notes?`, `quarantine_duration_days?` | Approve and activate a quarantine |
| `reject_quarantine` | `request_id`, `notes?` | Reject a proposal |
| `propose_quarantine` | `project_id`, `fingerprint`, `reason`, `test_name?`, `suite_name?`, `quarantine_duration_days?` | **Write.** File a PROPOSED request for QA Lead review; never quarantines directly (QA_LEAD+) |
| `release_quarantine` | `request_id`, `reason` | **Write.** End an active quarantine early; the test counts against gates again (QA_LEAD+) |
| `promote_ready_quarantines` | `project_id`, `dry_run?` | **Write (bulk).** `dry_run=true` (the default) lists `ready_to_promote` entries; `dry_run=false` releases them, at most 10 per call, each reported (QA_LEAD+) |

### LLM cost budget

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_billing_overview` | none | Workspace LLM spend this month |
| `get_project_llm_usage` | `project_id` | Current-period cost, tokens, cap utilization |
| `get_project_llm_quota` | `project_id` | Budget config: cap, action, enabled |

### Governance

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_release_gate_policies` | `project_id?` | Active GO/NO_GO rules |
| `list_saved_views` | `project_id?`, `page?` | Saved filter views |
| `list_digest_subscriptions` | `project_id?` | Scheduled quality report subscriptions |
| `list_ownership_rules` | `project_id` | Service ownership glob patterns for a project |
| `list_feature_flags` | none | All feature flags (admin only) |
| `check_feature_flag` | `key`, `project_id?` | Evaluate a flag for the caller's context |

### Classification feedback (write path)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `correct_classification` | `project_id`, `fingerprint`, `corrected_category`, `comment?`, `corrected_root_cause?` | **Write.** Override a wrong AI failure category; updates the analysis, evicts the cached verdict, and stores an `ai_feedback` training signal under the caller's identity |
| `record_fix_outcome` | `project_id`, `fingerprint`, `outcome`, `reference?`, `comment?` | **Write.** Record a fix's fate (`fixed` / `not_fixed` / `reverted`) against the latest analysis; lands as a `human_indirect` training signal (source `fix_outcome`) |

### Failure assignment (write path)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_assignment_options` | `test_case_id` | Valid assignees (resolved suite owner and QA_ENGINEER members) |
| `assign_failure` | `test_case_id`, `new_assignee_user_id` | **Write.** Reassign a FAILED/BROKEN test case to a new owner's my-failures inbox (QA_LEAD/ADMIN on the project) |

### Notification policy

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_transition_policy` | `project_id` | Per-project transition-notification policy (which events fire) |
| `set_transition_policy` | `project_id`, `transitions_enabled?`, `per_run_events_enabled?`, `enabled_events?`, `consecutive_failure_threshold?` | **Write.** Replace the whole policy; affects what every project member is notified about |

## Resource reference (11 resources)

Resources are read-only context URIs that clients can fetch as background knowledge.

| URI | Description |
|-----|-------------|
| `testlookup://projects` | Index of all active projects |
| `testlookup://projects/{project_id}` | Full project record and integrations |
| `testlookup://projects/{project_id}/metrics` | Dashboard KPIs (last 7 days) |
| `testlookup://projects/{project_id}/runs/latest` | 10 most recent runs |
| `testlookup://projects/{project_id}/quarantine-manifest` | CI quarantine manifest: currently effective quarantines with lifecycle flags (what `testlookup ci-verdict` matches against) |
| `testlookup://projects/{project_id}/flaky-tests` | Flakiness leaderboard (30 days, top 20) |
| `testlookup://projects/{project_id}/defects/open` | Open defects (up to 100) |
| `testlookup://runs/{run_id}` | Full run record and aggregated stats |
| `testlookup://runs/{run_id}/failures` | Test cases with status `FAILED` (up to 200) |
| `testlookup://runs/{run_id}/investigation` | Latest Investigator-agent investigation for the run (hypotheses and verdict), or a hint when there is none |
| `testlookup://tests/{run_id}/{test_id}` | Full test case, error and Allure labels |

## Prompt reference (7 prompts)

Prompts are reusable investigation workflows that expand into sequences of tool calls.

| Prompt | Parameters | Description |
|--------|-----------|-------------|
| `investigate_failure` | `test_case_id`, `project_id` | Full RCA: context gathering, AI analysis, structured findings report |
| `release_readiness_report` | `project_id`, `release_version?` | Executive go/no-go: metrics, trends, categories, blockers, flakiness risk |
| `weekly_quality_digest` | `project_id` | Team digest: pass rate trend, top failures, flakiness watch, action items |
| `flakiness_investigation` | `project_id`, `threshold_pct?` | Prioritised remediation: quick wins vs strategic improvements |
| `defect_triage_session` | `project_id` | Structured triage: review defects, identify duplicates, produce a P1/P2/P3 list |
| `suite_health_check` | `project_id`, `suite_name` | Suite analysis: coverage, worst tests, failure patterns, health score |
| `fix_this_flaky_test` | `project_id`, `fingerprint` | Packaged flaky-fix workflow: evidence and Investigator verdict, history recall, quarantine proposal, local reproduce and fix, `record_fix_outcome` on merge |

## Hardening notes (known gaps)

- **Input validation**: tools accept any string for enum fields (status, resolution_status); the API validates them, the MCP tools do not pre-validate.
- **Stdio token lifecycle**: the stdio token is process-local and is renewed only when the API answers 401.
- **Output format**: read tools return Markdown strings, not structured JSON, which suits AI clients but not programmatic consumers. The write tools return structured dicts (`ok`, `action`, `status_code`/`detail` on failure).
- **Concurrency**: stdio serves one client. SSE validates every request and binds each connection to its access token's `jti` and user.
- **Rate limiting**: the server itself does not rate-limit. The packaged Kubernetes ingress and OpenShift route for MCP (`k8s/base/ingress.yaml`, `k8s/overlays/openshift/route.yaml`) set connection and request-rate limits; apply equivalent controls at any other public gateway.

## Tests

```bash
cd mcp && python -m pytest tests -q
```

## Further reading

- [CLI, SDKs and MCP server](../user-guide/cli-sdk-mcp.md#the-mcp-server): how the MCP server fits next to the CLI and the SDKs
- [Agent cookbook](../user-guide/agent-cookbook.md): client setup and worked agent recipes
- [README.md](../README.md): product overview
- [ARCHITECTURE.md](../ARCHITECTURE.md): system architecture
- [THREAT_MODEL.md](../THREAT_MODEL.md): data flow and security boundaries (includes MCP)
