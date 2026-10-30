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
| `intelligence` | `show`, `refresh`, `history` | Run-intelligence summary, including the release recommendation; force a refresh; `history` is `ai-history` |
| `ai-history` | (top-level command) | The stored versions of a run's AI analysis, and with `--diff 1..latest` what changed between two of them |
| `compare-ai` | (top-level command) | What changed in a run's AI analysis since the previous run of its suite and branch |
| `deep` | `start`, `status`, `clusters`, `findings` | Deep-investigation workflow for a run |
| `reports` | `pdf`, `share` | Download a run's PDF report; create a time-limited share link |
| `reviews` | `list`, `accept`, `reject` | Human review of AI-generated reports (accept and reject need a login, not an API key) |
| `analytics` | `trends`, `chart` | The chart data the report pages draw: a metric over time, optionally one line per suite or release, or any metric by one or two dimensions, as a table, CSV or JSON |
| `keys` | `create`, `list`, `revoke` | Manage your API keys |
| `upload` | `file <path>`, `dir <directory>` | Upload one report, or every `.xml`, `.json` and `.trx` file in a folder |
| `ci-verdict` | (top-level command) | Quarantine-aware CI gate |
| `compliance-pack` | `verify <pack.zip>` | Verify a downloaded compliance pack offline: every file against its manifest and, with `--public-key`, its Ed25519 signature |

Upload syntax: `testlookup upload file results.xml --project <project-uuid> --build <build-id>`. `--project` is the project's UUID; `--format` defaults to `auto` (detected from the content) and accepts `junit`, `testng`, `allure`, `cypress`, `playwright`, `pytest`, `robot`, `cucumber`, `nunit`, `trx` and `xunit`. Add `--wait` to wait for ingestion and fail the step when the server refuses the report.

`ci-verdict` does not read the release-gate verdict. `testlookup ci-verdict --run <run-id>` compares the run's `FAILED` and `BROKEN` tests with the project's quarantine manifest and exits **0** when every failure is quarantined (or there are none), **1** on any real (non-quarantined) failure, or on any failure with `--strict`, and **2** on an API or network error or when the run is still in progress after `--timeout-seconds`. It fails closed; `--fail-open` turns infrastructure errors into exit 0 with a warning. Details: [Flaky tests](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict).

AI history: `testlookup ai-history <run-id>` lists one row per stored version of the run's AI analysis (`v3  2026-10-14 10:00  llm llama3.1  NO_GO risk 72  failing 7`), newest first; `--kind summary` lists the run-summary versions; `--diff A..B` (A a version number; B a number, `latest` or `live` for the current data) prints what changed, the analysis engine first, because a category change between a rules version and an LLM version may reflect the engine rather than the product. `testlookup compare-ai <run-id>` compares the run with the newest completed run of the same suite on the same branch (`--baseline <run-id>` picks another) and prints, for example, `vs #1233 (checkout · main): verdict GO → NO_GO · new root causes 1 · resolved 1 · 3 tests changed category`, then the summary, labelled `deterministic summary` when no LLM wrote it. `--fail-on-verdict-regression` exits **8** when the AI verdict got worse; when there is no previous run, or a side has no AI analysis, it warns and exits 0, because it cannot judge. Both take `--json` for the REST body.

Compliance packs: `testlookup compliance-pack verify pack.zip --public-key testlookup-compliance-k-1a2b3c4d5e6f7a8b.pem --manifest-sha256 <digest>` checks a pack you downloaded without contacting any server: each file against `manifest.json`, the manifest against the digest the server recorded (`--manifest-sha256`, the pack row's `manifest_sha256`; skipped when not given), and `signature.json` against the public key (the PEM from **Public key** on the pack, or `GET /api/v1/compliance-packs/signing-keys/{key id}/pem`). It prints `OK: signature valid (key k-1a2b3c4d5e6f7a8b), 12/12 files match`, `OK: 12/12 files match; the pack is unsigned (tamper-evident only)`, `OK: 12/12 files match; signature not checked (no --public-key)`, or one `TAMPERED: …` line per problem (`TAMPERED: decision.json does not match the manifest`, `TAMPERED: the pack is signed by key k-…, but --public-key is k-…`). Exit **0** verified, **1** tampered, an invalid signature or the wrong key, **2** a file it cannot read. Offline it cannot know a pack was meant to be signed, so `--require-signature` turns an unsigned pack into exit 1; `--json` prints the whole verification. What the signature does and does not prove: [Compliance & governance](compliance.md#how-a-pack-proves-it-hasnt-been-altered).

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

The `mcp/` directory runs a Model Context Protocol server exposing **74 tools**, **11 resources** and **7 prompts** over your TestLookup data, so an AI assistant can query runs, failures, flakiness, release readiness, compliance packs and more, with the same role and project checks as the API.

```bash
pip install -r mcp/requirements.txt
python mcp/server.py                    # stdio: for a local client that launches the server
python mcp/server.py --transport sse    # SSE on port 8002: for networked agent clients
```

Authentication: on stdio the server logs in as `TESTLOOKUP_USERNAME` / `TESTLOOKUP_PASSWORD` and holds the JWT; on SSE every request must carry the client's own `Authorization: Bearer <access token>`, validated against the API on each request. API keys are not accepted. `TESTLOOKUP_API_URL` (default `http://localhost:8000`) points it at the API.

Tool coverage mirrors the app's domains, one module each under `mcp/tools/`: `agents`, `analysis`, `analytics`, `assignments`, `auth`, `billing`, `compliance_pack`, `decision_trail`, `deep`, `defects`, `feedback`, `governance`, `intelligence`, `investigations`, `metrics`, `notifications`, `projects`, `quarantine`, `release`, `reports`, `reviews`, `runs` (including `get_test_step_flips` for step-level flakiness) and `search`. `health_check` reports the API's per-dependency health.

For AI history, `get_ai_history` lists a run's stored AI versions, `diff_ai_versions` shows what changed between two of them, and `compare_ai_with_previous` answers "what changed in the AI analysis since the previous checkout run?": the verdict, new and resolved root causes, tests whose category changed, and `verdict_regressed`. Each returns compact markdown beside the REST body; the summary says `deterministic summary` when no LLM wrote it, and a side that was never analysed is reported as not compared, not as "no change".

For compliance packs, `list_compliance_packs` and `generate_compliance_pack` list and build a release's evidence pack, and `verify_compliance_pack` checks a stored pack on the server (every file against its manifest, the manifest against the recorded digest, the Ed25519 signature) and answers its one-line summary, `Verified · 12 files · signed by key k-1a2b3c4d5e6f7a8b` or the files that do not match. It writes nothing.

For charts, `get_chart_data` returns the same series the report pages draw: a metric (executions by default), up to two grouping dimensions, and optional project, releases, suites and window. A point with no data comes back as `y: null` with `measured: false`, never 0, and a rate-limited call returns `retry_after` (chart reads allow 120 requests a minute per user).

Beyond reads, the server ships **write-path tools** so an agent can close the triage loop: propose and release quarantines (approval stays with a QA Lead), bulk-promote recovered tests, file deduplicated Jira defects (with an optional `dry_run` preview the agent is told to show you first), correct AI classifications, reassign failures, and manage the notification transition policy. Stdio writes run under its configured login; network writes run under the presenting client's validated bearer token. Both are audit-logged with that identity.

Typical assistant workflows: "why did last night's run fail?" (runs and analysis tools), "is this build safe to ship?" (release tools), "which tests should we quarantine?" (quarantine and flakiness tools). The full tool, resource and prompt reference and the environment variables are in [mcp/README.md](../mcp/README.md); client setup (Claude Code, Claude Desktop, Cursor, SSE) and worked end-to-end recipes are in the **[Agent cookbook](agent-cookbook.md)**.

## Invoke an agent with inline input

Normally an agent invocation (`POST /api/v1/agents/{agent_id}/invoke`, or MCP `invoke_agent`) runs on what TestLookup already stored for a test run. The summary agent, `agent.summary.v1`, can also run on failures you paste in: the catalog marks it with `inline_input_supported: true`, and `GET /api/v1/agents/catalog/agent.summary.v1` publishes the accepted shape as `inline_input_json_schema`.

```json
{
  "project_id": "<project uuid>",
  "input": {"agent_id": "agent.summary.v1", "payload": {"test_run_id": "<uuid of a stored run of that project>"}},
  "inline_input": {
    "schema_version": 1,
    "failures": [{
      "test_name": "checkout pays with a saved card",
      "suite_name": "payments",
      "failure_category": "PRODUCT_BUG",
      "confidence_score": 70,
      "root_cause": "NullPointerException in CardVault.lookup",
      "log_excerpt": "at CardVault.lookup(CardVault.java:42)"
    }]
  },
  "mode": "async"
}
```

From MCP, pass the same object as `payload`: `invoke_agent(agent_id="agent.summary.v1", project_id=..., test_run_id=..., payload={...})`. The Idempotency-Key works as for any invocation (the same key with a different `inline_input` is refused with 422), and `get_agent_invocation` prints `Input: inline (sha256 …)` with the stored input.

The rules:

- **A stored run anchors it.** `input.payload.test_run_id` must name a test run of `project_id` that you can see; it decides the project, the review and the retention (the invocation is deleted with that run). Any wrong anchor (an unknown run, another project's run, a run you cannot see, a mismatching `project_id`, an API key bound to another project) answers the same `404 {"code": "anchor_run_not_found"}`.
- **Caps.** The whole `inline_input` is at most 262 144 bytes as compact JSON (larger is `413 inline_input_too_large`); 1 to 10 failures; a test or suite name at most 300 characters, a root cause 4 000, a log excerpt 8 000; `failure_category` is one of `PRODUCT_BUG`, `INFRASTRUCTURE`, `TEST_DATA`, `AUTOMATION_DEFECT`, `FLAKY`, `UNKNOWN`; `confidence_score` 0 to 100 (your own claim; the summary leaves out failures below 50 as it does for any analysis). A field TestLookup does not know, or a control character, is `422 inline_input_invalid` with an `errors` list (the offending value is never echoed back). Only `agent.summary.v1` accepts inline input in this release; any other agent answers `422 inline_input_unsupported`.
- **What is redacted.** Before the input is stored or hashed, every text field goes through the same redaction as AI prompts: provider tokens (`ghp_…`, `github_pat_…`, `glpat-…`, `xox?-…`, `sk-…`, `AKIA…`, and any unbroken run of 40 or more key-like characters), `Authorization` / `Bearer` / `Cookie` values, `token=` / `password=` / `api_key=` values, passwords in connection strings, e-mail addresses, phone numbers and IP addresses all become `[REDACTED…]`. The invocation detail shows only the redacted copy, and `input_sha256` is the hash of that copy.
- **It reaches the model as data.** Each pasted value is put between `<<data>>` and `<</data>>` markers and cannot open or close a marker itself; the prompts are not changed.
- **The output is a draft that counts nowhere.** It is reviewed as the invocation (Inbox › Approvals lists it as an agent invocation), never as the run's report review, and accepting it changes only the invocation. It is kept out of the run's report, PDF and share links, release gates and decisions, run intelligence and AI history, notifications and digests, and it sends nothing: no e-mail, no digest, no PR comment, no action proposal.

## Choosing a surface

- **CI pipeline step** → CLI (or plain `curl` against the REST API).
- **Want live progress and per-test streaming** → SDK reporter.
- **Human asking questions in natural language** → MCP through your AI assistant.
- **Custom integrations** → the REST API directly (interactive docs at `http://localhost:8000/api-docs`).
