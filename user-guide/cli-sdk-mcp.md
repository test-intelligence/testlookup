# CLI, SDKs & MCP server

Three programmatic surfaces wrap the same REST API the dashboard uses. Pick by consumer:

| Surface | For |
|---|---|
| [`testlookup` CLI](#the-cli) | Terminals and CI shell steps |
| [Reporter SDKs](#the-sdks) | Test frameworks streaming results live (Python, Java, JS, Go) |
| [MCP server](#the-mcp-server) | AI assistants and agents (Claude Desktop, agent frameworks) |

All three authenticate with an API key from **Settings → API Keys** (the CLI can also log in with credentials).

## The CLI

Install from the repo: `pip install ./cli`. Command groups (each has `--help`):

| Group | What it does |
|---|---|
| `auth` | Log in / manage credentials |
| `upload` | Upload a report file (JUnit/TestNG/Allure/Cypress/Playwright, `--format auto`) |
| `ci-verdict` | Quarantine-aware CI gate: exit 0 when a run's only failures are quarantined/known-flaky tests (see [Flaky tests](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict)) |
| `projects` | List/inspect projects |
| `runs` | List runs, inspect a run's tests and status |
| `tests` | Query individual test results and history |
| `search` | Global search from the terminal |
| `intelligence` | Run-intelligence reports |
| `deep` | Deep-investigation workflows |
| `reports` | Summary reports |
| `keys` | Manage API keys |
| `health` | Check backend reachability/health |

The bread-and-butter CI pattern is in [Getting results in](getting-results-in.md#3-cli); for blocking a pipeline on the gate verdict see [Release gates](release-gates.md#gating-a-ci-pipeline); for letting known-flaky failures through, [`ci-verdict`](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict).

## The SDKs

The `client/` directory ships reporters for **Python** (pytest plugin / `testlookup_reporter`), **Java** (TestNG listener), **JS**, and **Go**. They live-stream results (session → per-test events → `run_complete`) so runs appear on **Live** (`/live`) while still executing, with heartbeat-based recovery if the process dies mid-run.

### Configuration — `testlookup.yaml`

All SDKs share one config discovery, so you configure once per repo (see `client/testlookup.yaml.example` for the full annotated file):

- **Discovery order** (first found wins): `./testlookup.yaml` → `./.testlookup/config.yaml` → `~/.testlookup/config.yaml`.
- **Precedence** (highest wins): constructor args > environment variables > config file > built-in defaults.
- **Secrets stay in env vars** (`TESTLOOKUP_API_KEY`); the file is for non-secret config (`server.url` / `TESTLOOKUP_URL`, project ID) and is safe to commit.

### Commit range from local git

The CLI and the Python SDK read the run's commit range (base…`HEAD`, with per-commit changed files) straight out of your git checkout and attach it to the results — no token, no network, no green baseline needed. It powers commit attribution and is the input future test-impact analysis trains on. Base selection, the shallow-clone caveat, and how to opt out are documented in [Getting results in → Commit range](getting-results-in.md#commit-range-who-changed-what).

Quick reference:

| Setting | CLI | Python SDK | Env | `testlookup.yaml` |
|---|---|---|---|---|
| Pin the base | `--commit-range-base` | `commit_range_base=` | `TESTLOOKUP_COMMIT_RANGE_BASE` | `testlookup.commit_range_base` |
| Turn it off | `--no-commit-range` | `collect_commit_range=False` | `TESTLOOKUP_COMMIT_RANGE=0` | `testlookup.commit_range: false` |

### Framework notes

- **Java/TestNG**: register the listener in your suite XML; the suite name is inherited from `<suite name="…">`, so parallel classes group under one logical suite.
- **Python/pytest**: prefer the live reporter for streaming; `pytest --junitxml` + `testlookup upload` works fine when you only need post-run ingestion.
- Working examples for each language live in `client/examples/`.

## The MCP server

The `mcp/` directory runs a Model Context Protocol server exposing **58 tools** over your TestLookup data, so an AI assistant can query runs, failures, flakiness, release gates, compliance packs, and more — with the same project scoping and auth as the API.

```bash
python mcp/server.py                    # stdio — for desktop clients (e.g. Claude Desktop)
python mcp/server.py --transport sse    # SSE on port 8002 — for networked agent clients
```

Tool coverage mirrors the app's domains: `runs` (including `get_test_step_flips` for step-level flakiness), `search`, `analysis`, `intelligence`, `deep`, `defects`, `quarantine`, `release`, `reports`, `metrics`, `analytics`, `projects`, `governance`, `compliance_pack`, `decision_trail`, `billing`, and a `health_check` that reports the backend's per-dependency health.

Beyond reads, the server ships **write-path tools** so an agent can close the triage loop: propose/release quarantines (approval stays with a QA Lead), bulk-promote recovered tests, file deduplicated Jira defects (with a mandatory dry-run preview), correct AI classifications, reassign failures, and manage the notification transition policy. All writes run under the configured login's server-side RBAC and are audit-logged with that identity.

Typical assistant workflows: "why did last night's run fail?" (runs + analysis tools), "is this build safe to ship?" (release tools), "which tests should we quarantine?" (quarantine + flakiness tools). For client setup (Claude Code / Claude Desktop / Cursor / SSE) and five worked end-to-end recipes, see the **[Agent cookbook](agent-cookbook.md)**.

## Choosing a surface

- **CI pipeline step** → CLI (or plain `curl` against the REST API).
- **Want live progress and per-test streaming** → SDK reporter.
- **Human asking questions in natural language** → MCP through your AI assistant.
- **Custom integrations** → the REST API directly (interactive docs at `http://localhost:8000/api-docs`).
