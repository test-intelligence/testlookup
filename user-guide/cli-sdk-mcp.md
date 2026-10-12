# CLI, SDKs & MCP server

Three programmatic surfaces wrap the same REST API the dashboard uses. Pick by consumer:

| Surface | For | Reference |
|---|---|---|
| [`testlookup` CLI](#the-cli) | Terminals and CI shell steps | [cli/README.md](../cli/README.md) |
| [Reporter SDKs](#the-sdks) | Test frameworks streaming results live (Python, Java, JS/TS, Go) | [client/README.md](../client/README.md) and the per-SDK READMEs below |
| [MCP server](#the-mcp-server) | AI assistants and agents (Claude Desktop, Claude Code, Cursor, agent frameworks) | [mcp/README.md](../mcp/README.md) |

None of them is published to a package registry: install each from a clone of the repository (`https://github.com/test-intelligence/testlookup`).

They do not authenticate the same way:

| Surface | Credential |
|---|---|
| CLI | A username/password login (`testlookup auth login`, stores a JWT profile) or an API key in `TESTLOOKUP_API_KEY` (sent as `X-API-Key`) |
| SDKs | An API key from **Settings → API Keys** or a JWT access token, depending on the SDK (see the [table below](#configuration-discovery-per-sdk)) |
| MCP server | A TestLookup user: `TESTLOOKUP_USERNAME` / `TESTLOOKUP_PASSWORD` on stdio, or each client's own bearer access token on SSE. It does not accept API keys. |

## The CLI

Install from the repository root with `pip install ./cli` (Python 3.11 or newer). The console script is `testlookup`. Every group has `--help`:

| Group | Subcommands | What it does |
|---|---|---|
| `auth` | `login`, `logout`, `whoami` | Log in with username and password and save a profile; clear its tokens; show the active profile |
| `doctor` | (run as `testlookup doctor`) | One verdict on profile, server reachability and build, and credentials; usable as a CI preflight |
| `health` | (run as `testlookup health`) | Server readiness (`GET /health/ready`) with per-dependency detail |
| `projects` | `list`, `get` | List and inspect projects |
| `runs` | `list`, `get` | List runs (filter by project and status) and inspect one |
| `tests` | `list <run-id>`, `get <run-id> <test-id>` | A run's test cases; one test case, with `--show-steps` for its step tree |
| `search` | (run as `testlookup search "<query>"`) | Global search across tests, runs, suites, defects and more |
| `intelligence` | `show`, `refresh` | Run-intelligence summary, including the release recommendation; force a refresh |
| `deep` | `start`, `status`, `clusters`, `findings` | Deep-investigation workflow for a run |
| `reports` | `pdf`, `share` | Download a run's PDF report; create a time-limited share link |
| `reviews` | `list`, `accept`, `reject` | Human review of AI-generated reports (accept and reject need a login, not an API key) |
| `analytics` | `trends`, `chart` | The chart data the report pages draw: a metric over time, optionally one line per suite or release, or any metric by one or two dimensions, as a table, CSV or JSON |
| `keys` | `create`, `list`, `revoke` | Manage your API keys |
| `upload` | `file <path>`, `dir <directory>` | Upload one report, or every `.xml`, `.json` and `.trx` file in a folder |
| `ci-verdict` | (top-level command) | Quarantine-aware CI gate |

Upload syntax: `testlookup upload file results.xml --project <project-uuid> --build <build-id>`. `--project` is the project's UUID; `--format` defaults to `auto` (detected from the content) and accepts `junit`, `testng`, `allure`, `cypress`, `playwright`, `pytest`, `robot`, `cucumber`, `nunit`, `trx` and `xunit`. Add `--wait` to wait for ingestion and fail the step when the server refuses the report.

`ci-verdict` does not read the release-gate verdict. `testlookup ci-verdict --run <run-id>` compares the run's `FAILED` and `BROKEN` tests with the project's quarantine manifest and exits **0** when every failure is quarantined (or there are none), **1** on any real (non-quarantined) failure, or on any failure with `--strict`, and **2** on an API or network error or when the run is still in progress after `--timeout-seconds`. It fails closed; `--fail-open` turns infrastructure errors into exit 0 with a warning. Details: [Flaky tests](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict).

The CLI has its own configuration and does not read `testlookup.properties` or `testlookup.yaml`. It keeps saved profiles in `profiles.json` in the per-user config directory. When the selected profile (`--profile`, else `TESTLOOKUP_PROFILE`, else the profile `auth login` made active, else `default`) is not saved there, it falls back to `TESTLOOKUP_URL` (default `http://localhost:8000`) and `TESTLOOKUP_API_KEY`. A saved profile wins over those variables. The full reference, including exit codes, CI-context detection and the analytics flags, is [cli/README.md](../cli/README.md).

The bread-and-butter CI pattern is in [Getting results in](getting-results-in.md#3-cli). For blocking a deploy on the release-gate decision (GO / NO_GO), see [Release gates](release-gates.md#gating-a-ci-pipeline); for letting known-flaky failures through, [`ci-verdict`](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict).

## The SDKs

The `client/` directory ships reporters for **Python** (a pytest plugin, `LiveStream` and `TestLookupReporter`), **Java** (a TestNG listener and a JUnit 5 extension), **JavaScript/TypeScript** (Jest and Mocha reporters) and **Go** (a reporter with a helper for Go's `testing` package). They stream results to the live-stream API, so a run appears on **Runs → Live** (`/live`) while it is still executing.

Per-SDK installation and usage:

- Python: [client/README.md](../client/README.md)
- JavaScript/TypeScript: [client/js/README.md](../client/js/README.md)
- Go: [client/go/README.md](../client/go/README.md)
- Java: [client/java/README.md](../client/java/README.md)

### Configuration discovery per SDK

The SDKs do **not** share one configuration mechanism. What each one actually reads:

| SDK | Config files, in search order (first found wins) | Endpoint env var | Credential env var | Project env var |
|---|---|---|---|---|
| Python | `./testlookup.properties`, `./.testlookup/testlookup.properties`, `./testlookup.yaml`, `./.testlookup/config.yaml`, `~/.testlookup/testlookup.properties`, `~/.testlookup/config.yaml`. YAML needs PyYAML. | `TESTLOOKUP_ENDPOINT` (canonical), `TESTLOOKUP_URL` (legacy); `TESTLOOKUP_ENDPOINT` wins when both are set | `TESTLOOKUP_API_KEY` (sent as `X-API-Key`), or `TESTLOOKUP_TOKEN` (JWT access token, sent as `Authorization: Bearer`) | `TESTLOOKUP_PROJECT` or `TESTLOOKUP_PROJECT_ID` |
| Java | A `testlookup.properties` on the test classpath (lowest), overridden by the first of `./testlookup.properties`, `./.testlookup/testlookup.properties`, `./testlookup.yaml`, `./.testlookup/config.yaml`, `~/.testlookup/testlookup.properties`, `~/.testlookup/config.yaml`. YAML needs `jackson-dataformat-yaml`. | `TESTLOOKUP_ENDPOINT` or `TESTLOOKUP_URL` (`TESTLOOKUP_ENDPOINT` wins); JVM system properties such as `-Dtestlookup.endpoint` beat both | `TESTLOOKUP_API_KEY` (sent as `X-API-Key`), else `TESTLOOKUP_TOKEN` (Bearer) | `TESTLOOKUP_PROJECT` or `TESTLOOKUP_PROJECT_ID` |
| JavaScript/TypeScript | The Jest and Mocha reporters: the same six files in the same order as Python. YAML needs the `yaml` package. (The programmatic `TestLookupReporter` takes values only from its config object.) | `TESTLOOKUP_ENDPOINT` or `TESTLOOKUP_URL` (`TESTLOOKUP_ENDPOINT` wins) | `TESTLOOKUP_API_KEY` (sent as `X-API-Key`), or `TESTLOOKUP_TOKEN` (JWT access token, Bearer) | `TESTLOOKUP_PROJECT` or `TESTLOOKUP_PROJECT_ID` |
| Go | None automatically: `New(Config{})` and `NewFromEnv()` read environment variables. The exported `LoadConfig()` reads `./testlookup.properties`, `./.testlookup/testlookup.properties`, `~/.testlookup/testlookup.properties` (no YAML) and returns values for you to copy into `Config`. | `TESTLOOKUP_URL` (`TESTLOOKUP_ENDPOINT` only through `LoadConfig()`) | `TESTLOOKUP_TOKEN` (JWT access token, Bearer) | `TESTLOOKUP_PROJECT_ID` |
| CLI (for comparison) | None; saved profiles in `profiles.json` (see [The CLI](#the-cli)) | `TESTLOOKUP_URL` | `TESTLOOKUP_API_KEY` (sent as `X-API-Key`) | none (`--project` per command) |

Precedence in the Python, Java and JavaScript SDKs, highest first: constructor, builder or reporter-option arguments, then (Java only) JVM system properties, then environment variables, then the config file, then built-in defaults. The API key and the token resolve as a pair: an explicit one wins, otherwise the first of those sources holding either one; within one source the API key wins, and a value starting with `qai_` is always sent as an API key. In a properties file the keys are `testlookup.*`: `testlookup.endpoint`, `testlookup.api.key`, `testlookup.token`, `testlookup.project`, `testlookup.launch`, `testlookup.suite`, `testlookup.release`, `testlookup.build`, `testlookup.branch`, `testlookup.commit`. The YAML form is the legacy nested schema (`server.url`, `auth.api_key`, `auth.token`, `project.id`, ...) annotated in [client/testlookup.yaml.example](../client/testlookup.yaml.example). Keep secrets in environment variables rather than in a committed file.

### Commit range from local git

The CLI and the Python SDK read the run's commit range (base…`HEAD`, with per-commit changed files) straight out of your git checkout and attach it to the results: no token, no network, no green baseline needed. It powers commit attribution. Base selection, the shallow-clone caveat, and how to opt out are documented in [Getting results in → Commit range](getting-results-in.md#commit-range-who-changed-what). The Java, JS/TS and Go SDKs do not collect it.

Quick reference:

| Setting | CLI | Python SDK | Env | `testlookup.properties` | Legacy `testlookup.yaml` |
|---|---|---|---|---|---|
| Pin the base | `--commit-range-base` | `commit_range_base=` | `TESTLOOKUP_COMMIT_RANGE_BASE` | `testlookup.commit_range_base=<ref>` | `ci.commit_range_base` |
| Turn it off | `--no-commit-range` | `collect_commit_range=False` | `TESTLOOKUP_COMMIT_RANGE=0` | `testlookup.commit_range=false` | `ci.collect_commit_range: false` |

The CLI honours the options and environment variables only.

### Framework notes

- **Java/TestNG**: register the listener in your suite XML. The run's suite name comes from `testlookup.suite`, else `testlookup.launch`, else the TestNG `<suite name="…">` value, so parallel classes group under one logical suite.
- **Python/pytest**: prefer the live reporter for streaming; `pytest --junitxml` plus `testlookup upload file` works when you only need post-run ingestion.
- Working examples for Python and Java live in [client/examples/](../client/examples/README.md).

## The MCP server

The `mcp/` directory runs a Model Context Protocol server exposing **68 tools**, **11 resources** and **7 prompts** over your TestLookup data, so an AI assistant can query runs, failures, flakiness, release readiness, compliance packs and more, with the same role and project checks as the API.

```bash
pip install -r mcp/requirements.txt
python mcp/server.py                    # stdio: for a local client that launches the server
python mcp/server.py --transport sse    # SSE on port 8002: for networked agent clients
```

Authentication: on stdio the server logs in as `TESTLOOKUP_USERNAME` / `TESTLOOKUP_PASSWORD` and holds the JWT; on SSE every request must carry the client's own `Authorization: Bearer <access token>`, validated against the API on each request. API keys are not accepted. `TESTLOOKUP_API_URL` (default `http://localhost:8000`) points it at the API.

Tool coverage mirrors the app's domains, one module each under `mcp/tools/`: `agents`, `analysis`, `analytics`, `assignments`, `auth`, `billing`, `compliance_pack`, `decision_trail`, `deep`, `defects`, `feedback`, `governance`, `intelligence`, `investigations`, `metrics`, `notifications`, `projects`, `quarantine`, `release`, `reports`, `reviews`, `runs` (including `get_test_step_flips` for step-level flakiness) and `search`. `health_check` reports the API's per-dependency health.

For charts, `get_chart_data` returns the same series the report pages draw: a metric (executions by default), up to two grouping dimensions, and optional project, releases, suites and window. A point with no data comes back as `y: null` with `measured: false`, never 0, and a rate-limited call returns `retry_after` (chart reads allow 120 requests a minute per user).

Beyond reads, the server ships **write-path tools** so an agent can close the triage loop: propose and release quarantines (approval stays with a QA Lead), bulk-promote recovered tests, file deduplicated Jira defects (with an optional `dry_run` preview the agent is told to show you first), correct AI classifications, reassign failures, and manage the notification transition policy. Stdio writes run under its configured login; network writes run under the presenting client's validated bearer token. Both are audit-logged with that identity.

Typical assistant workflows: "why did last night's run fail?" (runs and analysis tools), "is this build safe to ship?" (release tools), "which tests should we quarantine?" (quarantine and flakiness tools). The full tool, resource and prompt reference and the environment variables are in [mcp/README.md](../mcp/README.md); client setup (Claude Code, Claude Desktop, Cursor, SSE) and worked end-to-end recipes are in the **[Agent cookbook](agent-cookbook.md)**.

## Choosing a surface

- **CI pipeline step** → CLI (or plain `curl` against the REST API).
- **Want live progress and per-test streaming** → SDK reporter.
- **Human asking questions in natural language** → MCP through your AI assistant.
- **Custom integrations** → the REST API directly (interactive docs at `http://localhost:8000/api-docs`).
