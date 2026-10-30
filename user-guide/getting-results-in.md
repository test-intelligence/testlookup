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

### Timing captured per format

Besides each test's duration, TestLookup keeps what the report says about **when** each test ran: its start and finish, how long its fixtures took (setup and teardown) apart from the test body, and the parallel worker that ran it. It also keeps the suite and run timing the report states. Each format carries a different subset. Anything a format does not carry is stored as **not reported** (empty), never as zero or as the upload time.

- Clocks are stored in UTC. A report clock with no time zone (JUnit, Robot Framework, older xUnit runners) is read as UTC, and the test is flagged `timing_tz: assumed_utc` in its source extensions. The same applies to a TestNG zone abbreviation such as `IST`, which can name more than one offset.
- A finish computed as start + duration (Playwright, Cucumber, a JUnit `<testcase timestamp>`) is marked `timing_source: derived`. Values read straight from the report are `report`. Values from the live stream are `live`.
- Test counts do not change. TestNG configuration methods are timed but still not counted as tests.

| Format | Start / finish per test | Setup / test / teardown split | Worker | Suite and run timing |
|---|---|---|---|---|
| JUnit XML / surefire | Not in the schema. A non-standard `<testcase timestamp>` is used when a writer adds one (finish = start + `time`) | Not reported | Not reported | `<testsuite timestamp time>`, `<testsuites timestamp time>` |
| TestNG (`testng-results.xml`) | `started-at` / `finished-at` | `@BeforeMethod` becomes the setup of the next test in the class, `@AfterMethod` the teardown of the previous one; the method's own `duration-ms` is the test body | Not reported | `<suite started-at finished-at duration-ms>`; class-, test-, group- and suite-level configuration methods are the suite's setup / teardown |
| NUnit 3 | `start-time` / `end-time` | Not reported | Not reported | The fixture's and `<test-run>`'s `start-time` / `end-time` / `duration` |
| TRX (`dotnet test --logger trx`) | `startTime` / `endTime` | Not reported | Not reported (`computerName` is the machine, not a worker) | `<Times start finish>` |
| xUnit.net v2 | **Not in the format** | Not reported | Not reported | Each collection's `time`; the assembly's `start-rtf` / `finish-rtf`, or `run-date` + `run-time` + `time` |
| pytest (`--json-report`) | Not in the format | `setup` / `call` / `teardown` durations | Not reported | The report's `created` + `duration` |
| Playwright (`--reporter=json`) | The final attempt's `startTime` (finish derived from its duration) | "Before Hooks" / "After Hooks" steps, when the report lists them | `parallelIndex` (else `workerIndex`) | `stats.startTime` / `stats.duration` |
| Cypress (Mochawesome) | Not in the format | Not reported per test | Not reported | `stats.start` / `end` / `duration`; a suite's `beforeHooks` / `afterHooks` are its setup / teardown |
| Allure | `start` / `stop` | In a zip, the `befores` / `afters` of the `*-container.json` files whose `children` include the test | Not reported | The containers' fixtures |
| Robot Framework (`output.xml`) | `<status starttime endtime>` (RF 3-6) or `start` + `elapsed` (RF 7) | `<kw type="setup">` / `type="teardown"` (RF 6 and earlier), `<setup>` / `<teardown>` (RF 7) | Not reported | Each suite's `<status>` |
| Cucumber JSON | `start_timestamp` (cucumber-jvm 6 and later; finish derived) | The scenario's `before` / `after` hooks. A scenario's duration includes its hooks, as the framework reports it | Not reported | Not reported |
| Live-streaming SDKs | The `test_start` / `test_result` event times. A client clock far off from the server's is ignored, not trusted | Not reported | Not reported | `run_start` / `run_complete` |

## 1. Upload from the dashboard

**Runs → Upload** (or the deep link `/runs?upload=1`) opens the upload form under the page header: pick the project, the report file and a build number. It works in the **All Projects** view too, because the form asks for the project itself.

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

Optional JSON fields include `run_ai` (default `true`): `false` stores the run without staging the AI analysis, the JSON twin of the upload's `run_ai` form field and the CLI's `--no-ai`. Each result may also carry `http_exchanges` (see [Capturing HTTP exchanges](#capturing-http-exchanges)).

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

### Capturing HTTP exchanges

The Python pytest plugin can record the HTTP calls each test makes (method, URL, status, duration, both header sets and both bodies), so a failing API test shows what it sent and what came back, and the deep AI investigation can compare a response with earlier runs.

**Opt-in.** Capture is off by default. Turn it on per run with `pytest --testlookup-capture-http`, the environment variable `TESTLOOKUP_CAPTURE_HTTP=1` (also `true`, `yes`, `on`), or `testlookup.capture_http=true` in `testlookup.properties`. It needs Python reporter 1.3.0 or later and records calls made through `requests` and `httpx` (sync and async) while a test runs. The reporter's own traffic to TestLookup is never recorded, and a streamed response body is not read. The Java, JavaScript and Go SDKs do not capture yet.

**What is redacted, before it is stored.** The reporter applies these rules before anything leaves the CI machine, and the server applies them again before anything is written anywhere (the task queue, object storage, the exchange store):

- **Headers**: the values of `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`, `Api-Key`, `X-Auth-Token`, `X-Access-Token`, `X-CSRF-Token`, `X-XSRF-Token`, `X-Amz-Security-Token`, `X-Goog-Api-Key`, `X-Session-Token`, `Private-Token`, `X-GitLab-Token`, `X-Hub-Signature` and `X-Hub-Signature-256`, and of any header whose name ends in `token`, `secret`, `password`, `api-key`, `apikey`, `signature` or `credential`, become `[REDACTED]` (the name is kept).
- **URLs**: the query values of `token`, `access_token`, `id_token`, `refresh_token`, `api_key`, `apikey`, `key`, `code`, `password`, `secret`, `client_secret`, `sig`, `signature`, `auth` and the `X-Amz-Signature` / `X-Amz-Credential` / `X-Amz-Security-Token` signing parameters, and the password of a `user:password@host` URL.
- **Bodies**: the value of any JSON key named like a credential (`password`, `client_secret`, `apiKey`, `token` and the like), and anywhere in the text bearer tokens, e-mail addresses, provider tokens (`ghp_…`, `github_pat_…`, `glpat-…`, `xoxb-…`, `sk-…`, `AKIA…`) and any unbroken run of 40 or more key-like characters.

Review one captured run before you turn capture on in CI: a secret in a shape these rules do not know (a custom header name, a short opaque token under an innocent JSON key) would be kept.

**Caps.** At most 20 exchanges per test (the first 20; the rest are counted) and 1,000 per run; 50 headers per side; URLs up to 2,048 characters. A body is read up to 64 KB and stored up to 16 KB after redaction, ending with a `…[truncated: N bytes not stored]` marker when cut. A malformed exchange (no URL, an impossible method) is dropped and counted; it never costs the run its results.

**Where it shows.** The **HTTP exchanges** tab on a test's page (`/runs/<run>/tests/<test>`) lists them, with `[REDACTED]` where a value was removed. `GET /api/v1/runs/{run_id}/tests/{test_id}/http-exchanges` answers the same list to any member of the project. In a deep AI investigation with the `contract_validation` stage on, each JSON response's fields are compared with the project's latest earlier run that captured the same method and endpoint, and a missing field or a changed type is reported with the build it drifted from (for example ``field `price` missing from GET /api/products/{id}; drift since build 41``). Added fields are never reported, and a run with no earlier capture says there is nothing to compare with yet.

**Retention.** Exchanges follow the project's raw-events retention clock and are deleted with their run; a run under a retention hold keeps them. The `full` data export includes them, redacted, under each run.

**Over the API.** `http_exchanges` on each result of `POST /api/v1/ingest`: a list of `{"method", "url", "endpoint", "status", "duration_ms", "started_at", "error", "request": {"headers", "body"}, "response": {"headers", "body"}}` (only `method` and `url` are required). A live session posts one test's exchanges to `POST /api/v1/stream/http-exchanges` with `session_id`, `run_id`, `test_name`, `class_name` and `exchanges`; that is the route the pytest plugin uses. `endpoint` is the path template runs are compared by (`/api/products/{id}`); without it the server derives one from the URL, replacing numeric, UUID and long id-like path segments with `{id}`.

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

## Run context

A test that starts failing without a code change often failed because something around the code changed: a lockfile, the interpreter, a container image, the time zone. The **run context** records those facts with each run, so a run's **Changes** tab can list what differs from its baseline build (**Configuration drift**). It is collected **locally**: the SDK and the CLI read files and the interpreter, and nothing is fetched over the network.

**What is captured** (each part optional; an empty part is not sent):

- **Runtime versions**: the Python SDK records `python`, `implementation`, `os` and its own `testlookup-reporter` version, plus `pytest` when pytest is already loaded. The CLI records only its own `testlookup-cli` version (the CLI's Python is not the Python your tests ran on). Add what the tests really ran on with `TESTLOOKUP_RUNTIME=java=21.0.2,node=20.11.1` (or the CLI's `--runtime`); a value you set wins over a detected one.
- **Dependency fingerprints**: the SHA-256 of each lockfile found **directly in one folder** (no search below it): `poetry.lock`, `Pipfile.lock`, `uv.lock`, `pdm.lock`, `requirements.txt`, `requirements-dev.txt`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `npm-shrinkwrap.json`, `pom.xml`, `build.gradle`, `build.gradle.kts`, `gradle.lockfile`, `go.sum`, `Cargo.lock`, `Gemfile.lock`, `composer.lock`. Only regular files up to 50 MB; never their contents. The folder is the working directory unless you name another (a monorepo points it at the service's folder). The hash is of the raw bytes, so the same file checked out with Windows (CRLF) and Linux (LF) line endings hashes differently, by design.
- **Image digests**: `TESTLOOKUP_IMAGE_DIGESTS=app=sha256:<64 hex>,db=sha256:<64 hex>` (or the CLI's `--image-digest`). The SDK cannot see the image it runs in without a network call, so it never guesses.
- **Environment variables, from a fixed allowlist only**: `TZ`, `LANG`, `LC_ALL`, `PYTHONHASHSEED`, `NODE_ENV`, `ImageOS`, `ImageVersion`, `RUNNER_OS`, `RUNNER_ARCH`, `CI_RUNNER_VERSION`, `CI_SERVER_VERSION` and `BUILD_IMAGE`. No other variable is ever read for the context.
- The run's **environment** label (`environment` on the REST calls, `--environment` on the CLI) is compared too; it is a field of the run, not of the context.

**Redaction.** The allowlist names no secret, but a value can still hold one (a `BUILD_IMAGE` URL with a password, a token pasted into `LANG` by mistake). The SDK and the CLI therefore replace any value shaped like a token (`ghp_…`, `github_pat_…`, `glpat-…`, `xoxb-…`, `sk-…`, `AKIA…`, or any unbroken run of 40 or more key-like characters) with `[REDACTED]`, replace the password in a `scheme://user:password@host` value, and cut a value to 200 characters. The server applies the same rules again, plus its own credential and PII redaction, **before** the context is stored anywhere (the run row, object storage, the task queue). Versions, hashes, image names and digests are not redacted: they must match strict patterns, and a value that does not is refused rather than stored "cleaned". The run context is never given to the AI analysis.

**What each path carries**:

- **Python SDK 1.2.0** (the pytest plugin, the Robot listener, `TestLookupReporter`, `LiveStream`): sends it with the live session (`POST /api/v1/stream/sessions`, `run_context`) or, for `LiveStream`, in the `meta` it posts to `POST /api/v1/stream/ingest` (`meta.run_context`).
- **CLI 0.2.0** (`testlookup upload file` / `upload dir`): sends it as the `run_context` form field of `POST /api/v1/ingest/file` (JSON text).
- **REST**: `run_context` on `POST /api/v1/ingest`, `POST /api/v1/stream/sessions` and `meta.run_context` on `POST /api/v1/stream/ingest`; the `run_context` form field on `POST /api/v1/ingest/file`. The object is `{"v": 1, "runtime": {...}, "dependency_fingerprints": {...}, "image_digests": {...}, "env": {...}}`, at most 8,192 bytes.
- **Not carried**: an upload from the dashboard (a person uploading a file has no lockfile to hash), the S3 / MinIO drop folder, and the report file itself (no report format has such a field). The JavaScript, Java and Go SDKs send no context yet. A run without a context reads **Not recorded by this reporter** on its Changes tab, never "no drift".

**A malformed context is refused**, never stored: a key outside the allowlist, a file name that is not a listed lockfile, a hash that is not 64 hex characters, more than 16 runtime entries or 8 images, or more than 8,192 bytes is a `422` naming the rule (`run_context.env may only carry these keys: ...`; on `/ingest/file` the body is `{"code": "bad_json" | "invalid_run_context", "message"}`). The SDK and the CLI build the context under the same rules, so they do not trigger it; if a newer SDK meets an older server, both retry **once without the context** and log a warning, so results are never lost to metadata.

**Options**:

- **Python SDK**: `collect_run_context=False` and `run_context_root="services/payments"` on `TestLookupReporter` and `LiveStream`; the environment variables `TESTLOOKUP_RUN_CONTEXT=0` (also `false`, `no`), `TESTLOOKUP_RUN_CONTEXT_ROOT`, `TESTLOOKUP_RUNTIME` and `TESTLOOKUP_IMAGE_DIGESTS`; `testlookup.run_context` and `testlookup.run_context_root` in `testlookup.properties`.
- **CLI**: `--no-run-context`, `--run-context-root PATH`, `--runtime KEY=VERSION` and `--image-digest NAME=sha256:...` (both repeatable), `--environment TEXT`, and `--no-ai` (uploads without the AI analysis, for a CI step that wants no AI cost). `TESTLOOKUP_RUN_CONTEXT`, `TESTLOOKUP_RUN_CONTEXT_ROOT`, `TESTLOOKUP_RUNTIME` and `TESTLOOKUP_IMAGE_DIGESTS` work as for the SDK.

```bash
testlookup upload file results.xml --project "$PROJECT_ID" --build "$BUILD_ID" \
  --environment staging --runtime java=21.0.2 --run-context-root services/payments
```

Collecting the context **cannot fail your test run**: an unreadable file or any other problem leaves that part out (logged at debug level), and an over-large context drops the environment variables first, then the image digests, then the extra runtime entries, until it fits.

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
