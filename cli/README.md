# testlookup-cli

**TestLookup CLI: QA intelligence from your terminal.**

Upload test reports, inspect runs and failures, read AI analysis, and gate a CI
job on real (non-quarantined) failures without leaving the shell. The CLI talks
to a TestLookup server over its REST API. The only state it keeps is a saved
connection profile.

## Install (from source)

The CLI is not published to a package registry. Install it from a clone of the
repository (Python 3.11 or newer):

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
python -m venv .venv && . .venv/bin/activate   # optional, recommended
pip install ./cli
testlookup --help
testlookup --version
```

The package is `testlookup-cli`; it installs one console script, `testlookup`.

## Connect and authenticate

There are two ways to authenticate. Use a login on a workstation and an API key
in CI.

**Interactive login (JWT).** `auth login` posts your username and password to
`/api/v1/auth/login`, stores the returned access and refresh tokens in a
profile, and makes that profile active. When the server answers 401, the CLI
refreshes the access token once with the refresh token and saves the new one.

```bash
testlookup auth login --url http://localhost:8000      # prompts for username and password
testlookup auth whoami                                 # active profile, URL, auth type
testlookup doctor                                      # profile, reachability, auth in one verdict
```

**API key (CI).** Set `TESTLOOKUP_URL` and `TESTLOOKUP_API_KEY` and do not run
`auth login` on the runner. The key is sent as the `X-API-Key` header. Create a
key in the web UI under **Settings → API Keys**, or with
`testlookup keys create --name ci` while logged in (the raw key is shown once).

```bash
export TESTLOOKUP_URL=https://testlookup.example.com
export TESTLOOKUP_API_KEY=qai_your_api_key_here
testlookup doctor
```

Accepting or rejecting an AI report review needs a signed-in user: `reviews
accept` and `reviews reject` refuse to run with an API key.

`doctor` checks, in order, that a server URL is resolved, that the server
answers `GET /health/version` (and reports its version and build), and that the
credentials are accepted (an authenticated `GET /api/v1/projects`). It exits 1
only when a check fails outright; a reachable server with no credentials is a
warning, so it also works as a CI preflight. `health` reports the server's own
view of its dependencies (`GET /health/ready`) and exits non-zero unless the
status is `ready`.

## Configuration

| Setting | Where it comes from |
|---|---|
| Profile name | `--profile <name>` on a command, else `TESTLOOKUP_PROFILE`, else the `active_profile` file written by `auth login`, else `default` |
| Saved profiles | `profiles.json` in the per-user config directory: `~/.config/testlookup/` on Linux, `~/Library/Application Support/testlookup/` on macOS, `%LOCALAPPDATA%\testlookup\testlookup\` on Windows. Written owner-only (0600) on POSIX. |
| Server URL | The saved profile's `url`. If the selected profile is not saved: `TESTLOOKUP_URL`, default `http://localhost:8000` |
| Credentials | The saved profile's tokens (from `auth login`). If the selected profile is not saved: `TESTLOOKUP_API_KEY` (sent as `X-API-Key`); without it, requests are unauthenticated |

Resolution is all-or-nothing per profile: when the selected profile name exists
in `profiles.json`, the CLI uses it and ignores `TESTLOOKUP_URL` and
`TESTLOOKUP_API_KEY`. The environment variables are the fallback for a profile
name that has not been saved, which is the normal case on a CI runner. On a
machine that already has a saved `default` profile, select an unsaved name (for
example `TESTLOOKUP_PROFILE=env`) to use the environment variables instead.
There is no global `--url` option; `auth login --url` sets the URL of the
profile it saves.

`auth logout` removes the stored tokens and keeps the profile's URL.

## Command reference

Every group has `--help`, for example `testlookup upload file --help`.

| Group | Subcommands | Purpose |
|---|---|---|
| `auth` | `login`, `logout`, `whoami` | Log in with username and password (saves a JWT profile), clear stored tokens, show the active profile |
| `doctor` | (none; run `testlookup doctor`) | One-shot setup check: profile, server reachability and build, credentials |
| `health` | (none; run `testlookup health`) | Server readiness from `GET /health/ready`, with per-dependency detail |
| `projects` | `list`, `get <project-id>` | List accessible projects, show one project |
| `runs` | `list`, `get <run-id>`, `timing <run-id>` | List runs (`--project`, `--status`, `--page`, `--size`), show one run; how long a run took and where the time went (see [Execution time](#execution-time)) |
| `tests` | `list <run-id>`, `get <run-id> <test-id>` | List a run's test cases (`--status`, `--suite`, `--page`, `--size`); show one test case, with `--show-steps` to summarise its step tree |
| `search` | (none; run `testlookup search "<query>"`) | Global search across tests, runs, suites, defects and more (`--types`, `--project`) |
| `intelligence` | `show <run-id>`, `refresh <run-id>`, `history <run-id>` | Run intelligence summary (status, pass rate, release recommendation, executive summary); force a refresh; the run's stored AI versions (same as `ai-history`) |
| `ai-history` | (top-level command; `ai-history <run-id>`) | A run's stored AI analysis versions, and the diff of two (`--diff 1..latest`) (see [AI history](#ai-history)) |
| `compare-ai` | (top-level command; `compare-ai <run-id>`) | A run's AI analysis against the previous run's (same suite and branch, or `--baseline`); `--fail-on-verdict-regression` for CI (see [AI history](#ai-history)) |
| `deep` | `start`, `status`, `clusters`, `findings` (each takes `<run-id>`) | Queue a deep investigation, check its pipeline status, list failure clusters and findings |
| `reports` | `pdf <run-id>`, `share <run-id>` | Download a PDF report (`--out`, `--layout executive\|engineering`); create a time-limited share link (`--expires` 1-30 days) |
| `reviews` | `list <project-id>`, `accept <review-id>`, `reject <review-id>` | Human review of AI-generated reports: list the queue (`--state`, `--limit`), accept (`--notes`), reject (`--reason`, `--notes`) |
| `analytics` | `trends`, `chart`, `execution` | The chart data the UI draws, as a table, CSV or JSON (see [Chart data](#chart-data)); a project's execution time per suite and its duration regressions (see [Execution time](#execution-time)) |
| `keys` | `create`, `list`, `revoke <key-id>` | Create a personal API key (`--name`), list keys, revoke one (`--yes` skips the prompt) |
| `upload` | `file <path>`, `dir <directory>` | Upload one report file, or every `.xml`, `.json` and `.trx` file in a directory (see [Uploading results](#uploading-results)) |
| `ci-verdict` | (top-level command) | Quarantine-aware CI gate: exit 0 when every failure in a run is quarantined (see [Gating CI with ci-verdict](#gating-ci-with-ci-verdict)) |
| `compliance-pack` | `verify <zip>`, `list <release>`, `generate <release>`, `download <release> -o <zip>` | Verify a downloaded compliance pack offline: every file against its manifest and the Ed25519 signature (`--public-key`, `--manifest-sha256`, `--require-signature`, `--json`; see [Verifying a compliance pack](#verifying-a-compliance-pack)); list, build (QA lead) and download a release's packs (`--pack` for one id; default the newest) |
| `release` | `gate <release>`, `list` | The release's gate verdict as a CI exit code (`--evaluate`, `--fail-on`, `--fail-open`, `--json`; see [Gating a release](#gating-a-release)); releases with their standing verdict |
| `quarantine` | `list`, `approve <id>`, `reject <id>`, `release <id>` | The Flaky › Quarantine queue; approve (`--reason`, `--days`), reject, release early (`--reason`) — QA lead |
| `defects` | `jira preview`, `jira create` | Preview or file a failure signature (`--fingerprint` or `--cluster`) as a Jira issue (`--target webhook` emits the outbound event instead) |
| `investigations` | `start <run-id>`, `list`, `show <id>` | Deep investigations: start one for a run, a project's list, one investigation's cause and confidence |

## Common tasks

```bash
# Upload results after a test run (--project is the project UUID)
testlookup upload file results.xml --project "$PROJECT_ID" --build "$BUILD_ID" --wait

# Gate the job on real failures only
testlookup ci-verdict --run "$RUN_ID" --project "$PROJECT_ID"

# Look around
testlookup projects list
testlookup runs list --project "$PROJECT_ID" --status FAILED
testlookup tests list "$RUN_ID" --status FAILED
testlookup tests get "$RUN_ID" "$TEST_ID" --show-steps
testlookup search "connection reset"
testlookup intelligence show "$RUN_ID"

# Human review of AI reports (accept and reject need `auth login`)
testlookup reviews list "$PROJECT_ID"
testlookup reviews accept "$REVIEW_ID" --notes "checked against the logs"
testlookup reviews reject "$REVIEW_ID" --reason unsupported_claim

# Reports
testlookup reports pdf "$RUN_ID" --out run-report.pdf --layout engineering
testlookup reports share "$RUN_ID" --expires 14
```

## Uploading results

`testlookup upload file <path>` sends one report to `POST /api/v1/ingest/file`.
`testlookup upload dir <directory>` does the same for every `.xml`, `.json` and
`.trx` file directly in that directory (not recursive), one ingestion job per
file, and exits 1 if any file fails.

| Option | Meaning |
|---|---|
| `-p`, `--project` | Project UUID (required) |
| `-b`, `--build` | Build number or identifier (required) |
| `--branch`, `--commit`, `--release` | Git branch, commit SHA, release name |
| `-f`, `--format` | `auto` (default, detected from content), `junit`, `testng`, `allure`, `cypress`, `playwright`, `pytest`, `robot`, `cucumber`, `nunit`, `trx`, `xunit`. An unknown value is rejected before any request. |
| `--ci-provider`, `--repo`, `--pr-number`, `--ci-actor`, `--ci-run-url` | Override the auto-detected CI context |
| `--commit-range-base <ref>` | Pin the base of the collected commit range |
| `--no-commit-range` | Do not collect the commit range from local git |
| `--wait` / `--no-wait` | Wait until the server has ingested the report and exit non-zero if it refuses it (over the result cap, unparseable, empty). Default `--no-wait`: the command returns once the upload is accepted. |
| `--wait-timeout <seconds>` | How long `--wait` waits (default 600; for `upload dir` one budget covers every file) |
| `--profile`, `-o`/`--output` | Profile to use; `table`, `json` or `yaml` output |

An upload that meets a 429 or 503 is retried, honouring `Retry-After` (otherwise
backing off from 0.5 s), for up to five minutes in total. The accepted upload's
JSON carries `run_id` and `task_id`:

```bash
RUN_ID=$(testlookup upload file results.xml -p "$PROJECT_ID" -b "$BUILD_ID" --wait -o json | jq -r '.run_id')
```

## Gating CI with ci-verdict

`testlookup ci-verdict --run <run-id>` fetches the run's `FAILED` and `BROKEN`
test cases and the project's quarantine manifest
(`GET /api/v1/projects/{project_id}/quarantine/manifest`), then splits the
failures into real and quarantined. A failure counts as quarantined when its
fingerprint (`sha256(class_name::test_name)[:16]`, recomputed by the CLI)
matches a manifest entry, or, failing that, when its exact
`(test_name, suite_name)` pair does. It does not read the release-gate
verdict (GO / NO_GO); `testlookup intelligence show <run-id>` prints the run's
release recommendation, and the MCP `check_run_release_readiness` tool returns
the full decision. For a whole project, the MCP `check_release_readiness` tool
states the verdict `/overview` shows (GREEN / AMBER / RED, decided by the
server from the project's release-gate policy, with the hard caps that fired as
blockers, or "Pending" when the window has no executions); given a
`release_id`, it returns that release's stored gate decision
(`GET /api/v1/releases/{id}/gate`) and its blocking reasons.

| Option | Meaning |
|---|---|
| `--run <run-id>` | Run to judge (required) |
| `-p`, `--project <project-id>` | Project; inferred from the run when omitted |
| `--strict` | Exit 1 on any failure, quarantined ones included |
| `--fail-open` | On an API or network error, exit 0 with a warning instead of 2 |
| `--timeout-seconds N` | Wait up to N seconds (polling every 5 s) for an in-progress run to finish; default 0 judges the current state |
| `--profile`, `-o`/`--output` | Profile to use; `table` or `json` (the full partition) |

| Exit code | Meaning |
|---|---|
| `0` | No real failures; quarantined failures do not block (also: an infrastructure error with `--fail-open`) |
| `1` | At least one real (non-quarantined) failure, or any failure with `--strict` |
| `2` | API or network error, or the run was still in progress after `--timeout-seconds`. Fails closed by default. |

CI recipes for GitHub Actions, Jenkins and GitLab are in
[Flaky tests](../user-guide/flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict).

## Chart data

```bash
testlookup analytics trends --project "$PROJECT_ID" --release "$RELEASE_ID" --suite checkout --days 90
testlookup analytics trends --project "$PROJECT_ID" --by suite --format csv > pass-rate-by-suite.csv
testlookup analytics chart --metric duration_p95 --group-by week --group-by environment -o json
```

`analytics trends` is `--metric pass_rate` per UTC day by default; `--by
<dimension>` adds one series per value (the 7 largest plus "other", unless
`--top-n` says otherwise). `analytics chart` takes any `--metric` over one or
two `--group-by` dimensions. Both take `--project` (omit it for every project
you can read), `--days` (default 30, 1-365) and `--top-n`. `--release` and
`--suite` repeat (OR within each, AND across); a release is a UUID or
`unattributed`, and an unreadable release refuses the whole request (exit 4).
Both read `GET /api/v1/analytics/chart-data`, the endpoint MCP's
`get_chart_data` reads too.

`--format` (alias `--output`, `-o`) is `table`, `csv` or `json`. `csv` writes
the UI export's layout (scope as `# Label,value` lines, a gap as an empty cell,
never 0) as UTF-8 with `\n` line ends and no BOM; `table` draws a sparkline per
series. The UI's Trends page draws its catalogue charts from the same endpoint,
but its pass-rate trend card reads `/metrics/trends`, which counts every run
that touched a suite whole, so under `--suite` that card and these commands
differ. The server allows 120 chart-data requests a minute per user.

## Execution time

```bash
testlookup runs timing "$RUN_ID"                         # KPI line, suites, the 10 slowest tests, regressions
testlookup runs timing "$RUN_ID" --slowest 25 --json     # the REST body
testlookup runs timing "$RUN_ID" --fail-on-regression    # CI: exit 7 when the run got slower
testlookup analytics execution --project "$PROJECT_ID" --days 30 --branch main --suite checkout
```

`runs timing` reads `GET /api/v1/runs/{run_id}/timing` and prints a line such
as `Wall clock 9.0 s · Test time 17.4 s · Parallelism 1.9×`: the wall clock is
how long the run took on the clock, the test time is the sum of the test
durations, and parallelism is their ratio. Then the setup / test body /
teardown totals, the suites, the slowest `--slowest` tests (default 10, 1-100)
against their duration baselines, and the run's duration regressions (tests
slower than their baseline, suites slower than their history).

`analytics execution` reads `GET /api/v1/analytics/execution-metrics`, the
endpoint the UI's Trends > Execution time and MCP's `get_execution_metrics`
read. It needs `--project` and takes `--days` (default 30, compared with the
window of the same length before it), `--branch`, `--top` (1-100) and the
repeatable `--release` and `--suite`. It prints the totals, wall clock and
test time p50/p95 per suite with their change, the suite and test duration
regressions, and the slowest tests by p95.

A figure the test report did not carry is `not reported` (or `-` in a table),
never 0: a JUnit file has no timestamps, so a JUnit run has a test time but no
wall clock. A block the server could not judge prints its reason ("not enough
history (4 of 10)"). Both commands take `--json` (or `-o json`) for the REST
body. Slowness never changes the exit code unless you pass
`--fail-on-regression`, which exits `7` when the run has at least one
duration regression and `0` otherwise, including when the run could not be
judged (that is printed as a warning). The per-test CSV is the API's
`GET /api/v1/runs/{run_id}/timing.csv` (the Timing tab's Export CSV).

## AI history

Every AI analysis of a run is kept as a version (a re-analysis no longer
overwrites the last one), and a run's analysis can be compared with the
previous run's:

```bash
testlookup ai-history "$RUN_ID"                       # one row per stored version
testlookup ai-history "$RUN_ID" --diff 1..latest      # what changed between two versions
testlookup compare-ai "$RUN_ID" --fail-on-verdict-regression   # CI: exit 8 when the AI verdict got worse
```

`compare-ai` picks the previous run of the same primary suite on the same
branch (`--baseline <run-id>` overrides it) and prints the verdict change, new
and resolved root causes and the tests whose AI category changed. Without a
reachable LLM the summary is the deterministic one, and it says so.
`--fail-on-verdict-regression` exits `8` only when the verdict got worse; a
comparison it cannot judge prints a warning and exits `0`. Both commands take
`--json` (or `-o json`) for the REST body.

## Verifying a compliance pack

A compliance pack generated since W11 carries `signature.json`: an Ed25519
signature by the deployment's key over the SHA-256 of `manifest.json`, the
pack id and the key id. `compliance-pack verify` checks a downloaded pack with
no server and no network: it reads the ZIP and the deployment's public key
(the PEM from `GET /api/v1/compliance-packs/signing-keys`, the release panel's
**Public key** button, or one a reviewer was given out of band).

```bash
testlookup compliance-pack verify pack.zip --public-key testlookup-compliance-k-1a2b3c4d5e6f7a8b.pem
testlookup compliance-pack verify pack.zip --public-key key.pem --manifest-sha256 "$RECORDED_SHA256"
testlookup compliance-pack verify pack.zip --public-key key.pem --require-signature   # an unsigned pack fails
testlookup compliance-pack verify pack.zip --json                                      # the verification as JSON
```

It checks every file the manifest lists against its SHA-256, refuses any file
the manifest does not list (except `signature.json`), compares the manifest
with `--manifest-sha256` when given (the digest recorded when the pack was
generated, shown in the app and in the download's `X-Manifest-Sha256`
header), and verifies the signature with `--public-key`. It prints one line,
for example `OK: signature valid (key k-1a2b3c4d5e6f7a8b), 12/12 files match`
or `TAMPERED: decision.json does not match the manifest` (one line per file
that does not match).

| Option | Meaning |
|---|---|
| `--public-key <pem>` | The deployment's Ed25519 public key. Without it the files are checked and the signature is not (`signature not checked`, exit 0) |
| `--manifest-sha256 <hex>` | The manifest digest recorded at generation; a different manifest is tampered |
| `--require-signature` | Offline the CLI cannot know a pack WAS signed: with this flag an unsigned pack exits 1. Needs `--public-key` |
| `--json` | Print the verification as JSON |

| Exit code | Meaning |
|---|---|
| `0` | Verified: every file matches and the signature is valid; or the pack is unsigned (tamper-evident only, a pack generated before signing existed); or no `--public-key` was given |
| `1` | Tampered: a file does not match or is not listed, the manifest does not match `--manifest-sha256`, the signature is invalid, or the pack is signed by another key than `--public-key` (both key ids are printed); with `--require-signature`, also an unsigned pack |
| `2` | The input could not be read (not a ZIP, a missing file, a key that is not an Ed25519 PEM), or a usage error |

What a valid signature proves: the deployment's key signed this exact
manifest. The private key is stored encrypted beside the deployment's data, so
it defends against storage-only tampering, not against an actor holding both
the database and `APP_SECRET_KEY`; keep the public key and the recorded digests
off-host for that. The command needs the `cryptography` package (a dependency
of the CLI since W11).

## Release names

Every `--release` and `<release>` takes a release id, `unattributed` (analytics), or the release's exact
**name**, then its exact **version**, case-insensitively; every `--project` takes an id or the project's exact
name. Names are not unique, so a name that matches several releases (or none) exits `4` and lists the
candidates with their ids (or the closest names) — the CLI never picks one.

## Gating a release

`testlookup release gate <release> [--project <name>]` prints the release's stored verdict (the one on its
release page: verdict, pass rate, evidence, blockers) and exits with a stable code, so a pipeline can block on it:

| Code | Meaning |
|---|---|
| `0` | `GO`, or `CONDITIONAL_GO` (unless `--fail-on CONDITIONAL_GO`) |
| `1` | `NO_GO` (or `CONDITIONAL_GO` with `--fail-on CONDITIONAL_GO`) |
| `3` | No verdict to act on: `NOT_EVALUATED` (not enough evidence) or never evaluated |
| `2` | Infrastructure error: network, 5xx, authentication, permission, an unknown or ambiguous release name. Fails closed; `--fail-open` exits `0` with a warning instead |

`--evaluate` records a fresh verdict first (QA lead or a project API key); it then appears in the release's
gate history. `--json` prints `{release_id, gate}`.

```yaml
# GitHub Actions
- name: Release gate
  run: testlookup release gate "2026.10" --project "Checkout Service" --evaluate --fail-on NO_GO
  env:
    TESTLOOKUP_URL: ${{ secrets.TESTLOOKUP_URL }}
    TESTLOOKUP_API_KEY: ${{ secrets.TESTLOOKUP_API_KEY }}
```

```yaml
# GitLab CI
release-gate:
  script:
    - testlookup release gate "2026.10" --project "Checkout Service" --fail-on NO_GO
```

## Output and exit codes

Commands that print data take `-o`/`--output`: `table` (default) or `json`, and
most also accept `yaml`. Exceptions: `reports pdf` uses `-o` for the output
file path, `analytics trends` and `analytics chart` take `table|csv|json`,
`runs timing`, `analytics execution`, `ai-history` and `compare-ai` take `table|json` (or `--json`), and `intelligence refresh`,
`deep start` and `keys revoke` print only a confirmation. Errors and warnings
go to stderr, so `-o json | jq` sees only the JSON document.

Commands that show AI-generated reports (`intelligence show`) print the
report's review state and the AI disclaimer to stderr. `unknown` means the
server sent no review status: treat the report as an unreviewed draft.

Apart from `ci-verdict`, `release gate` and `compliance-pack verify` (above), failures map to stable exit codes:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | General error, server error (including 429), unreachable server, or a failed check (`doctor`, `health`, `upload dir`) |
| `2` | Validation error (HTTP 400/422) or a command-line usage error |
| `3` | Authentication failed (HTTP 401) |
| `4` | Permission denied (HTTP 403) |
| `5` | Not found (HTTP 404) |
| `6` | Timed out (HTTP 408/504, or a client-side timeout) |
| `7` | `runs timing --fail-on-regression` only: the run has duration regressions |
| `8` | `compare-ai --fail-on-verdict-regression` only: the AI verdict got worse than the baseline's |

## CI context and commit ranges

`upload` detects CI context from the standard environment variables of GitHub
Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI: the provider,
repository, pull or merge request number, actor and run URL. Branch, build
number and commit SHA are not detected; pass `--build` (required), `--branch`
and `--commit` yourself. Precedence, lowest first: detection, then the
`TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`,
`TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL` environment variables, then
the matching options.

`upload` also collects the commit range (base..`HEAD`, up to 100 commits with
their changed files) from local git, so failures can be attributed to the
commits that plausibly caused them. Pin the base with `--commit-range-base` or
`TESTLOOKUP_COMMIT_RANGE_BASE`; turn collection off with `--no-commit-range` or
`TESTLOOKUP_COMMIT_RANGE=0`. Both detection and collection are best-effort and
never fail an upload. See
[Getting results in](../user-guide/getting-results-in.md#commit-range-who-changed-what)
for base selection and the shallow-clone caveat.

## Further reading

- [CLI, SDKs and MCP server](../user-guide/cli-sdk-mcp.md): choosing between the CLI, the reporter SDKs and the MCP server
- [Getting results in](../user-guide/getting-results-in.md#3-cli): the CLI in CI pipelines

## Tests

```bash
python -m pytest cli/tests    # from the repository root
```
