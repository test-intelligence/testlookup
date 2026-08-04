# Getting test results into TestLookup

Everything in TestLookup — clustering, flakiness, release gates — starts with your test results arriving. There are four paths; pick by how your tests run:

| You have… | Use |
|---|---|
| A report file (JUnit XML, TestNG, Allure, Cypress, Playwright, pytest, Robot Framework, Cucumber, NUnit, TRX, xUnit) | [Dashboard upload](#1-upload-from-the-dashboard) or [REST file ingest](#2-rest-api) |
| A CI job that produces report files | [CLI upload](#3-cli) or [REST file ingest](#2-rest-api) in a pipeline step |
| Tests you want streamed **live**, test-by-test, as they run | [SDK live streaming](#4-live-streaming-sdks) |
| Structured results you already parse yourself | [REST JSON batch ingest](#2-rest-api) |

All four paths land in the same place: a **Run** under your project, with per-test rows, automatic failure clustering, and analysis queued in the background. Results appear on `/runs` within seconds of ingest being accepted (processing is asynchronous — the API returns `202 Accepted` and Celery workers do the rest).

## Supported formats

`junit` (also what pytest emits with `--junitxml`), `testng`, `allure`, `cypress` (Mochawesome JSON), `playwright` (`--reporter=json`), `pytest` (`--json-report`), `robot` (Robot Framework `output.xml`), `cucumber` (`--format json` — cucumber-jvm/js, behave, SpecFlow), `nunit` (NUnit3 XML), `trx` (Visual Studio / `dotnet test --logger trx`), `xunit` (xUnit.net v2 XML). Everywhere a format is accepted, `auto` (the default) detects the format from the file content — you rarely need to specify it.

## 1. Upload from the dashboard

1. Open **Runs** (`/runs`) and click **Upload report** (or deep-link `/runs?upload=1`).
2. Pick the report file and confirm the project.
3. The run appears in the list; click it for per-test detail.

> Upload is gated by the `manual_upload` feature flag (on by default) and requires a specific project to be selected — it's disabled in the admin "All Projects" view.

## 2. REST API

Create an API key first under **Settings → API Keys**, then send it as a Bearer token.

**File ingest** — `POST /api/v1/ingest/file` (multipart):

```bash
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -H "Authorization: Bearer $TESTLOOKUP_API_KEY" \
  -F "file=@test-results.xml" \
  -F "project_id=$PROJECT_ID" \
  -F "format=auto"
```

**JSON batch ingest** — `POST /api/v1/ingest` with a structured payload, for when you already have parsed results (custom frameworks, homegrown runners):

```bash
curl -X POST "http://localhost:8000/api/v1/ingest" \
  -H "Authorization: Bearer $TESTLOOKUP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "'"$PROJECT_ID"'",
    "run": {"build_number": "ci-1234", "environment": "staging"},
    "tests": [
      {"name": "test_checkout_total", "suite_name": "payments", "status": "failed",
       "duration_ms": 812, "error_message": "AssertionError: expected 100 got 99"}
    ]
  }'
```

Both endpoints return `202 Accepted` with an ingest/run reference — processing continues asynchronously.

## 3. CLI

The `testlookup` CLI (in `cli/`, installable with `pip install ./cli`) wraps the API for terminals and CI scripts:

```bash
testlookup auth login                       # or set an API key in the config
testlookup upload results.xml --project $PROJECT_ID --format auto
testlookup runs list --project $PROJECT_ID  # confirm it landed
```

Other useful command groups: `runs`, `tests`, `search`, `intelligence`, `deep`, `reports`, `keys`, `projects`, `health` — each has `--help`.

Typical CI step (after the test command, even when tests fail — use your CI's "always run" step option so failures still get analyzed):

```bash
pytest --junitxml=results.xml || true
testlookup upload results.xml --project "$PROJECT_ID"
```

## 4. Live-streaming SDKs

For test-by-test streaming while the suite is still running — watch progress on **Live** (`/live`) and get per-test rows without waiting for a report file. SDKs live in `client/`: **Python** (a pytest plugin / reporter), **Java** (a TestNG listener), plus **JS** and **Go** clients.

The flow every SDK follows: open a session → stream `test_started` / `test_finished` events → send `run_complete`, which closes the session and finalizes the run. Sessions carry a heartbeat, so runs that die mid-flight are detected and recovered rather than stuck "in progress" forever.

- **Python**: install `client/` (`testlookup-reporter`) and point it at your API URL + key via `testlookup.yaml` (see `client/testlookup.yaml.example`) or environment variables.
- **Java/TestNG**: register the TestLookup listener in your suite XML; it inherits the suite name from `<suite name="…">`.

While a live run is in progress, the Runs and report pages read from the live buffer, so counts stay consistent with what **Live** shows.

## CI context (PRs)

When results come from a CI job, the SDKs and CLI automatically detect and attach **CI context** — provider, repository (`org/name`), PR/MR number, triggering user, and a deep link back to the CI run. Detection reads the standard environment variables of **GitHub Actions, GitLab CI, Jenkins (multibranch), Azure DevOps, and CircleCI**; outside CI nothing is sent, and nothing is ever guessed.

Explicit values always win over detection:

- **CLI**: `testlookup upload file … --ci-provider … --repo org/name --pr-number 421 --ci-actor … --ci-run-url …` (same flags on `upload dir`).
- **Any SDK / CLI**: set `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR`, `TESTLOOKUP_CI_RUN_URL` env vars, or (Python SDK) the `testlookup.ci_*` config-file keys.
- **SDK session options**: each SDK's session-create options accept the five fields directly (e.g. `prNumber` in JS/Java, `PRNumber` in Go, `pr_number` in Python).

The fields land on the run (`ci_provider` / `ci_repo` / `pr_number` / `ci_actor` / `ci_run_url`) and anchor PR-level features: with the [GitHub integration](administration.md#github-settingsgithub) configured, runs carrying a matching repo + PR number get a **sticky PR summary comment** (newly-failed / known-flaky / fixed tests), and commit attribution builds on the same fields. GitLab CI runs work the same way — `CI_PROJECT_PATH` → `ci_repo`, `CI_MERGE_REQUEST_IID` → `pr_number` — and with the [GitLab integration](administration.md#gitlab-settingsgitlab) configured get the equivalent **sticky MR note** plus a **commit status** ([recipe](reference/testlookup-gitlab-ci.yml)).

## Commit range (who changed what)

Alongside CI context, the **CLI and the Python SDK collect the commit range for the run from your local git checkout** and send it with the results. This is what lets TestLookup say *which commits are suspect* for a failure — and it is the training input for future test-impact analysis (running only the tests a change can affect).

It works **fully offline**: no personal access token, no VCS API call, no network. `git` is already there in any CI checkout. The alternative connector path needs a token, a matching repo, outbound network, and a fully-green baseline run — so it yields nothing on air-gapped installs.

**What's collected** — for each commit between the base and `HEAD`, oldest first: the SHA, author name, the first line of the message, the ISO commit date, and the list of changed file paths. Nothing else — no diffs, no file contents, no author email. Bounded at 100 commits (the newest 100 if the range is longer), 500 files per commit, and 512 characters per path.

**How the base commit is chosen** (first one that resolves wins):

1. **You said so** — `--commit-range-base` on the CLI, `commit_range_base=` on the Python SDK.
2. **`TESTLOOKUP_COMMIT_RANGE_BASE`** environment variable.
3. **`testlookup.commit_range_base`** in `testlookup.yaml`.
4. **Your CI system's diff base**, read from its own documented variables:
   - *GitHub Actions* — the PR event payload's `base.sha`, else `GITHUB_BASE_REF`; for push events, the payload's `before`.
   - *GitLab CI* — `CI_MERGE_REQUEST_DIFF_BASE_SHA`, else `CI_MERGE_REQUEST_TARGET_BRANCH_SHA`, `CI_COMMIT_BEFORE_SHA`, or the target branch name.
   - *Jenkins* — `CHANGE_TARGET` (multibranch PR builds), else `GIT_PREVIOUS_SUCCESSFUL_COMMIT` / `GIT_PREVIOUS_COMMIT`.
   - *Azure DevOps* — `SYSTEM_PULLREQUEST_TARGETBRANCH` / `…TARGETBRANCHNAME`.
   - *CircleCI* — **not detected.** CircleCI has no built-in *environment variable* carrying a diff base (`pipeline.git.base_revision` is a pipeline value you must map into the job yourself). Rather than guess, CircleCI falls through to step 5. To use the pipeline value, map it yourself: `TESTLOOKUP_COMMIT_RANGE_BASE: << pipeline.git.base_revision >>`.
5. **Local git** — `git merge-base` against the default branch; if you're already on it, the previous commit.

If none of these resolve, **nothing is sent**. An absent range is honest; a wrong one poisons the analysis. A base *you* supplied (steps 1–3) that doesn't resolve is never silently replaced with a guess.

> **Shallow clones.** CI checkouts are frequently `--depth 1`, where the base commit simply isn't in history. This is detected (`git rev-parse --is-shallow-repository`) and degrades to a **head-only** range — the one commit that provably is present — never a fabricated one. For a full range, deepen the checkout: `fetch-depth: 0` on `actions/checkout`, `GIT_DEPTH: 0` on GitLab.

**Turning it off** — any of:

- `testlookup upload file … --no-commit-range`
- `TESTLOOKUP_COMMIT_RANGE=0` (also accepts `false` / `no` / `off`)
- `testlookup.commit_range: false` in `testlookup.yaml`
- `TestLookupReporter(…, collect_commit_range=False)` / `LiveStream(…, collect_commit_range=False)`

Collection **can never fail your test run**. Any git problem — no git binary, not a repo, timeout, unreadable history — is logged at debug level and the range is simply omitted. It also never writes to stdout, so `testlookup upload … --output json | jq` stays clean.

Collection currently ships in the **CLI** and the **Python SDK**. The JS, Java, and Go SDKs still send CI context but not the commit range; set `TESTLOOKUP_COMMIT_RANGE_BASE` there if you need it, or upload via the CLI.

## After ingest: what happens automatically

1. Per-test rows are persisted and each test gets its cross-run **fingerprint**.
2. Failures are **clustered** by error-signature similarity.
3. The AI analysis pipeline (rules first; ML/LLM if enabled) explains failures and updates **flaky confidence**.
4. Failures are **auto-assigned** to suite owners / QA engineers (see `/my-failures`).
5. The run shows up in trends, coverage, the summary report, and the **release gate** evidence.

## Troubleshooting

- **Run appears but has no per-test rows** — ingest was accepted but processing is still running; give it a few seconds. If it persists, an operator should check that Celery workers are subscribed to the ingestion shard queues.
- **`/suites` or `/coverage` look empty while `/runs` is populated** — usually a worker/queue issue on the operator side, not your upload.
- **Upload button missing on Runs** — you're in "All Projects" mode (pick a project) or the `manual_upload` feature flag is off.
- **422 response on ingest** — the payload shape didn't validate; the response `detail` lists the exact fields. The dashboard surfaces these as error toasts too.
