# TestLookup operator and administrator guide

This folder is for the people who **run** a TestLookup instance and **configure** it: platform administrators, QA leads who own projects and policies, and the engineers who wire CI into it.

> **Using TestLookup day to day?** The end-user guide is built into the app: open **? → Documentation** in the top bar, or go to `/docs` on your instance (for example `http://localhost:3000/docs`). Its source is [frontend/src/content/guide/](../frontend/src/content/guide/introduction.md) in this repository. It covers signing in, sending results, runs, the inbox, failures, flaky tests, dashboards, reports, releases, Ask AI and every settings page.

Installing the stack is covered elsewhere: start with [GETTING_STARTED.md](../GETTING_STARTED.md), then the deployment guides under [docs/operations/](../docs/operations/deployment.md). Internals are in [architecture/](../architecture/README.md).

## Guides in this folder

| Guide | What it covers |
|---|---|
| [Administration & settings](administration.md) | Who can open which settings page, projects and people, SSO and SCIM, MFA and lockout, integrations and notifications, GitHub and GitLab, AI agents, data and storage, retention, backup, restore and upgrades, and the admin checklists |
| [Connecting CI and test frameworks](getting-results-in.md) | Supported report formats, the REST ingest calls, the CLI in a pipeline, live-streaming SDKs, CI context for pull requests, and commit ranges |
| [CLI, SDKs & MCP](cli-sdk-mcp.md) | The `testlookup` CLI, the reporter SDKs and their shared configuration, and the MCP server |
| [Agent cookbook](agent-cookbook.md) | Connecting an AI agent (Claude Code, Claude Desktop, Cursor) to the MCP server, the write-tool safety model, and worked triage recipes |
| [AI configuration](ai-features.md) | Analysis modes and providers, the confidence gate, Ask AI server settings, the LLM cost budget, AI evaluation, the human review gate, and how the ML classifier learns |
| [Quarantine policy and CI gating](flaky-tests.md) | Turning on quarantine proposals, the quarantine manifest, and failing CI only on real (non-quarantined) failures with `testlookup ci-verdict` |
| [Gate policies and CI gating](release-gates.md) | Writing release-gate policies, overrides, release phase enforcement, and blocking a pipeline on the verdict |
| [Issue trackers and defect automation](defects.md) | Jira one-click issues, recurrence de-duplication and status sync, the outbound-webhook fallback, and automatic defect promotion |
| [Test catalog governance and ownership](test-management.md) | The authored test-case lifecycle and its flag, ownership rules, the auto-assignment chain, and the default QA lead |
| [Knowledge base & test generation](knowledge-base.md) | Registering knowledge sources and generating grounded, cited test cases |
| [Fixer](fixer.md) | The agent that proposes validated fixes for flaky tests as draft pull requests |
| [Compliance & governance](compliance.md) | Compliance packs and the SOC 2 / GDPR / HIPAA control-family mapping, with its limits |
| [Sizing & capacity](sizing.md) | Reference hardware profiles, per-service CPU and memory, and disk-growth math for retention planning |
| [Air-gapped install](air-gapped-install.md) | Building and importing the offline bundle for Compose, Kubernetes and OpenShift |
| [Offline model pack](offline-model-pack.md) | Side-loading the local LLM and embedding models into an air-gapped deployment |
| [Troubleshooting](troubleshooting.md) | Operator-side symptoms: workers and queues, integrations that do not fire, AI features that are unavailable, stale bundles |

Four older pages moved into the in-app guide and now only point there: [dashboards.md](dashboards.md), [working-with-runs.md](working-with-runs.md), [triaging-failures.md](triaging-failures.md) and [value-metrics.md](value-metrics.md).

## Feature flags you will meet first

Several workflows are switched by feature flags (**Settings → System → Feature flags**, administrators only). These ship **off** on a new install, so the pages behind them say so until you turn them on:

| Flag | Turns on |
|---|---|
| `flaky_auto_quarantine` | Quarantine proposals and the review workflow on **Flaky tests** |
| `outbound_webhooks` | Outbound webhook subscriptions (**Settings → Integrations → Outbound webhooks**) |
| `llm_cost_budget` | Enforcement of the per-project LLM cost budget |
| `release_phase_gate_enforcement` | Refusing to complete a release phase without a GO verdict |
| `release_compliance_pack` | Compliance pack generation |
| `github_checks` | GitHub check runs and pull-request comments |
| `gitlab` | GitLab merge-request notes and commit statuses |

`manual_upload` (the **Upload** button on Runs) and `ask_ai_chat` (the Ask AI page) ship **on**. The full list, with what each flag gates and how flags are evaluated, is the [feature-flag reference](../docs/operations/feature-flags.md).

## Conventions in these guides

- The dashboard runs at `http://localhost:3000` and the API at `http://localhost:8000` in a default Compose install. Substitute your own URLs, for example `https://testlookup.example.com`.
- API examples use `curl` with an API key sent in the `X-API-Key` header. Create keys under **Settings → Project → Streaming API keys** (for CI) or **Settings → Account → My API keys** (your own). Examples use the placeholder `qai_your_api_key_here`.
- `AI_OFFLINE_MODE=true` is the default. In that mode no outbound integration (Jira, Slack, Teams, webhooks, GitHub, GitLab, a hosted LLM) is called, whatever its own setting says. Guides that depend on outbound calls say so.
- Roles, from least to most: `VIEWER`, `TESTER`, `QA_ENGINEER`, `QA_LEAD`, `ADMIN`. Each includes the abilities of the ones below it. "QA lead or above" means `QA_LEAD` and `ADMIN`.
