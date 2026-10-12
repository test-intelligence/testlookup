# Connecting CI and test frameworks

Everything in TestLookup starts with test results arriving. This page is for whoever wires a CI pipeline or a test framework into it. End users who only want to upload one report file can follow **Getting results in** in the in-app guide (`/docs/ingestion`, source [ingestion.md](../frontend/src/content/guide/ingestion.md)).

| You have... | Use |
|---|---|
| A report file (JUnit XML, TestNG, Allure, Cypress, Playwright, pytest, Robot Framework, Cucumber, NUnit, TRX, xUnit) | [Dashboard upload](#1-upload-from-the-dashboard) for a one-off, the [REST file ingest](#2-rest-api) or the [CLI](#3-cli) in a pipeline |
| Tests you want streamed **live**, test by test, while they run | A [live-streaming SDK](#4-live-streaming-sdks) |
| Structured results you already parse yourself | [REST JSON batch ingest](#2-rest-api) |

Every path lands in the same place: a **run** in your project, with per-test rows, failure clustering and analysis queued in the background. Ingestion is asynchronous: the API answers `202 Accepted` and the workers do the rest, so a run appears on **Runs** a few seconds later.

**Who can send results.** Writing results needs the `QA_ENGINEER` role or above, whatever the credential, and the caller must be a member of the project. An API key carries its owner's role, so a key created by a QA engineer works; a key cannot exist for a viewer or tester, because only QA engineers and above can create keys.

## Supported formats

`junit` (also what pytest emits with `--junitxml`), `testng`, `allure`, `cypress` (Mochawesome JSON), `playwright` (`--reporter=json`), `pytest` (`--json-report`), `robot` (Robot Framework `output.xml`), `cucumber` (`--format json`: cucumber-jvm, cucumber-js, behave, SpecFlow), `nunit` (NUnit 3 XML), `trx` (Visual Studio, `dotnet test --logger trx`), `xunit` (xUnit.net v2 XML). A zip archive of any of these is accepted too. Everywhere a format is accepted, `auto` (the default) detects the format from the file content, so you rarely need to name it.

### Format switches

Cypress and Playwright parsing each sit behind a feature flag (`cypress_ingest`, `playwright_ingest`). Both ship on; with one turned off, that format is refused with `503`.

## 1. Upload from the dashboard

**Runs → Upload** (or the deep link `/runs?upload=1`) opens the upload dialog: pick the project, the report file and a build number. It works in the **All Projects** view too, because the dialog asks for the project itself.

The button is controlled by the `manual_upload` feature flag. On a new install it is **on**. An administrator can turn it off under **Settings → System → Feature flags**, for example to force every result through CI; the button then disappears for everyone. Installs upgraded from a version before the flag defaulted on keep whatever value they had, so if the button is missing on an older instance, check that flag.

## 2. REST API

Create an API key first: **Settings → Project → Streaming API keys** for a CI key bound to one project, or **Settings → Account → My API keys** for your own. Send it in the `X-API-Key` header. The key is shown once; store it in your CI secret store.

**File ingest**: `POST /api/v1/ingest/file` (multipart form). `file`, `project_id` and `build_number` are required.

```bash
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -H "X-API-Key: $TESTLOOKUP_API_KEY" \
  -F "file=@test-results.xml" \
  -F "project_id=$PROJECT_ID" \
  -F "build_number=$BUILD_ID" \
  -F "branch=$BRANCH" \
  -F "format=auto"
```

Optional form fields: `format`, `branch`, `commit_hash`, `release_name` (attaches the run to that release), `environment`, the CI-context fields `ci_provider`, `ci_repo`, `pr_number`, `ci_actor`, `ci_run_url`, and `run_ai` (default `true`).

**JSON batch ingest**: `POST /api/v1/ingest`, for results you already have in structured form (a custom runner, a script). `project_id`, `build_number` and `results` are required; each result needs `test_name` and a `status` of `PASSED`, `FAILED`, `SKIPPED` or `BROKEN`.

```bash
curl -X POST "http://localhost:8000/api/v1/ingest" \
  -H "X-API-Key: $TESTLOOKUP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "'"$PROJECT_ID"'",
    "build_number": "ci-1234",
    "branch": "main",
    "environment": "staging",
    "results": [
      {"test_name": "test_checkout_total", "suite_name": "payments", "status": "FAILED",
       "duration_ms": 812, "error_message": "AssertionError: expected 100 got 99"}
    ]
  }'
```

Both endpoints answer `202 Accepted` with `run_id`, `task_id` and `total_results`. `GET /api/v1/ingest/uploads/{task_id}` reports how processing of an uploaded file ended (`pending`, `parsing`, `ingesting`, `succeeded` or `failed`). Sending the same `project_id` and `build_number` again updates that run instead of creating a second one.

The full request schema is in the live API reference at `/api-docs` on your instance.

## 3. CLI

The `testlookup` CLI wraps the same API for terminals and CI scripts. It is installed from the repository (`pip install ./cli`); it is not published to PyPI. Point it at your instance with `TESTLOOKUP_URL` and `TESTLOOKUP_API_KEY`, or sign in once with `testlookup auth login`.

```bash
testlookup upload file results.xml --project "$PROJECT_ID" --build "$BUILD_ID"
testlookup runs list --project "$PROJECT_ID"     # confirm it landed
```

`--project` takes the project's UUID. `upload dir` uploads every report in a folder. The full command reference is [CLI, SDKs & MCP](cli-sdk-mcp.md).

In CI, run the upload step **even when the tests fail** (that is the run you most want recorded): use your CI's "always run" condition, or let the test command exit zero and gate on the result afterwards:

```bash
pytest --junitxml=results.xml || true
testlookup upload file results.xml --project "$PROJECT_ID" --build "$BUILD_ID"
```

To fail the build only on real (non-quarantined) failures after uploading, see [Gating CI on real failures only](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict). A GitLab CI recipe covering both the report upload and the live-streaming path is in [reference/testlookup-gitlab-ci.yml](reference/testlookup-gitlab-ci.yml).

## 4. Live-streaming SDKs

For test-by-test streaming while the suite is still running, so the run shows up on **Runs → Live** as it happens. SDKs live under `client/` in the repository: **Python** (a pytest plugin and a `LiveStream` reporter), **Java** (a TestNG listener and a JUnit 5 extension), **JavaScript/TypeScript** (Jest and Mocha reporters) and **Go**.

Every SDK follows the same lifecycle: it opens a live session, streams each test's result in batches while the tests run, and finalises the run when they finish. The session-based reporters (the pytest plugin, Java, JavaScript, Go) do that by closing the session; Python's `LiveStream`, which has no explicit session, sends a `run_complete` event. Either one finalises the run the same way. Sessions carry a heartbeat, so a run whose runner dies mid-flight is detected and recovered instead of staying "in progress" forever. While a live run is in progress, the Runs and report pages read from the live buffer, so their counts match what **Live** shows.

Installation and configuration for each SDK (URL, API key, project, build, release and suite settings, and the shared configuration file) are in [CLI, SDKs & MCP](cli-sdk-mcp.md).

## Attaching runs to a release

A run joins a release when it arrives with a release name: `release_name` on the REST calls, `--release` on `testlookup upload file`, or the SDK's release setting. Runs can also be attached afterwards from the release itself; see **Releases** in the in-app guide (`/docs/releases`).

## CI context (PRs)

When results come from a CI job, the SDKs and the CLI detect and attach **CI context**: provider, repository (`org/name`), pull/merge request number, triggering user, and a link back to the CI run. Detection reads the standard environment variables of **GitHub Actions, GitLab CI, Jenkins (multibranch), Azure DevOps and CircleCI**. Outside CI nothing is sent, and nothing is guessed.

Explicit values always win over detection:

- **CLI**: `testlookup upload file ... --ci-provider ... --repo org/name --pr-number 421 --ci-actor ... --ci-run-url ...` (the same flags exist on `upload dir`).
- **Any SDK or the CLI**: the environment variables `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL`.
- **REST**: the `ci_provider`, `ci_repo`, `pr_number`, `ci_actor` and `ci_run_url` fields.

The fields land on the run and anchor the pull-request features: with the [GitHub integration](administration.md#github-settingsgithub) configured, runs carrying a matching repository and PR number get a **sticky PR summary comment** (newly failed, known flaky and fixed tests). GitLab CI runs work the same way (`CI_PROJECT_PATH` becomes the repository, `CI_MERGE_REQUEST_IID` the PR number) and, with the [GitLab integration](administration.md#gitlab-settingsgitlab) configured, get the equivalent **sticky MR note** plus a **commit status**.

## Commit range (who changed what)

Alongside CI context, the **CLI and the Python SDK collect the commit range for the run from the local git checkout** and send it with the results. This is what lets TestLookup say which commits are suspect for a failure, and it is the raw material for future test-impact analysis.

It works **fully offline**: no access token, no VCS API call, no network. The alternative, fetching the range through the GitHub integration, needs a token, outbound network and a matching repository, so it yields nothing on an air-gapped install.

**What is collected**: for each commit between the base and `HEAD`, oldest first, the SHA, author name, first line of the message, commit date and the list of changed file paths. No diffs, no file contents, no author email. At most 100 commits (the newest 100 if the range is longer), 500 files per commit and 512 characters per path.

**How the base commit is chosen** (the first that resolves wins):

1. **You said so**: `--commit-range-base` on the CLI, `commit_range_base=` on the Python SDK.
2. The **`TESTLOOKUP_COMMIT_RANGE_BASE`** environment variable.
3. The SDK configuration file: `testlookup.commit_range_base` in `testlookup.properties`, or `ci.commit_range_base` in the YAML configuration.
4. **Your CI system's diff base**, from its own documented variables:
   - *GitHub Actions*: the PR event payload's `base.sha`, else `GITHUB_BASE_REF`; for push events, the payload's `before`.
   - *GitLab CI*: `CI_MERGE_REQUEST_DIFF_BASE_SHA`, else `CI_MERGE_REQUEST_TARGET_BRANCH_SHA`, `CI_COMMIT_BEFORE_SHA`, or the target branch name.
   - *Jenkins*: `CHANGE_TARGET` (multibranch PR builds), else `GIT_PREVIOUS_SUCCESSFUL_COMMIT` / `GIT_PREVIOUS_COMMIT`.
   - *Azure DevOps*: `SYSTEM_PULLREQUEST_TARGETBRANCH` / `SYSTEM_PULLREQUEST_TARGETBRANCHNAME`.
   - *CircleCI*: **not detected**. CircleCI has no built-in environment variable carrying a diff base, so it falls through to step 5. To use the pipeline value, map it yourself: `TESTLOOKUP_COMMIT_RANGE_BASE: << pipeline.git.base_revision >>`.
5. **Local git**: `git merge-base` against the default branch; on the default branch itself, the previous commit.

If none of these resolve, **nothing is sent**. An absent range is honest; a wrong one would mislead the analysis. A base you supplied (steps 1 to 3) that does not resolve is never silently replaced with a guess.

> **Shallow clones.** CI checkouts are often `--depth 1`, where the base commit is not in the history. This is detected (`git rev-parse --is-shallow-repository`) and degrades to a **head-only** range, never a fabricated one. For a full range, deepen the checkout: `fetch-depth: 0` on `actions/checkout`, `GIT_DEPTH: 0` on GitLab.

**Turning it off** (any of):

- `testlookup upload file ... --no-commit-range`
- `TESTLOOKUP_COMMIT_RANGE=0` (also `false`, `no`, `off`)
- `testlookup.commit_range=false` in `testlookup.properties` (or `ci.collect_commit_range: false` in the YAML configuration)
- `collect_commit_range=False` on the Python SDK's reporter or `LiveStream`

Collection **cannot fail your test run**. Any git problem (no git binary, not a repository, a timeout, unreadable history) omits the range rather than raising, and nothing is written to stdout, so `testlookup upload ... --output json | jq` stays clean. Ordinary "nothing to send" cases are logged at debug level; a git that answered and then failed (a timeout, a `fatal:`) is logged as a warning with git's own message. `commit_range.diagnose_commit_range(...)` returns the range with a `reason` and `detail` explaining it.

Collection ships in the **CLI** and the **Python SDK**. The JavaScript, Java and Go SDKs send CI context but not the commit range; upload through the CLI if you need it.

How the server uses a range (the `source` and `base_source` it records, and the GitHub connector fallback) is described in [Administration → Commit attribution & TIA readiness](administration.md#commit-attribution--tia-readiness).

## After ingest: what happens automatically

1. Per-test rows are stored and each test gets its cross-run **fingerprint**.
2. Failures are **clustered** by error-signature similarity.
3. The analysis pipeline categorises failures (rules always; ML or a language model when configured) and updates flaky scores.
4. Failures are **auto-assigned** to suite owners (see [Test catalog governance and ownership](test-management.md#ownership-who-gets-the-failure)) and appear in each owner's **Inbox**.
5. The run feeds trends, coverage, the summary report and the release gate.

## Troubleshooting

- **`401`**: the key is missing, revoked or expired, or it was sent as `Authorization: Bearer` instead of `X-API-Key`. A bearer token must be a signed-in session token, not an API key.
- **`403` "Requires at least QA_ENGINEER role"**: the key's owner is a viewer or tester. Use a key owned by a QA engineer or above.
- **`403` on a project**: the key's owner is not a member of that project, or the key is bound to a different project.
- **`422`**: the payload did not validate; the response `detail` names the field. A common one is a lower-case `status` in a JSON batch.
- **`429`** with `Retry-After`: the project's ingest budget for the minute is spent. **`503`** while the cache is under memory pressure, or for a Cypress/Playwright report whose flag is off. See [Sizing & capacity](sizing.md) for the limits.
- **The run appears but has no per-test rows**: processing is still running; give it a few seconds. If it persists, check that the workers subscribe to every ingestion shard queue ([Troubleshooting](troubleshooting.md#a-run-shows-totals-but-no-per-test-rows)).
- **No Upload button on Runs**: the `manual_upload` flag is off on this instance.
