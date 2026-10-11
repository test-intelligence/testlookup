# TestLookup reporters (client SDKs)

Reporters stream test results to a TestLookup server while the tests run, so a run shows up on **Runs → Live** (`/live`) as it executes and lands on Runs, Failures and Trends when it finishes. If you only need to upload a report file after the run, use the CLI (`testlookup upload file`, see [cli/README.md](../cli/README.md)) or `POST /api/v1/ingest/file` instead.

| Reporter | Directory | Hooks into | Docs |
|---|---|---|---|
| Python | `client/` (this directory) | pytest plugin, `LiveStream`, `TestLookupReporter` | this page |
| Java | [client/java](java/README.md) | TestNG listener, JUnit 5 extension | [client/java/README.md](java/README.md) |
| JavaScript / TypeScript | [client/js](js/README.md) | Jest reporter, Mocha reporter | [client/js/README.md](js/README.md) |
| Go | [client/go](go/README.md) | Go `testing` helpers | [client/go/README.md](go/README.md) |

None of the reporters is published to a package registry yet (PyPI, Maven Central, npm, the Go module proxy): install them from a clone of [github.com/test-intelligence/testlookup](https://github.com/test-intelligence/testlookup). Runnable sample projects are in [client/examples](examples/README.md). The reporters do not share one configuration mechanism; [user-guide/cli-sdk-mcp.md](../user-guide/cli-sdk-mcp.md#configuration-discovery-per-sdk) compares them.

## Credentials

Two kinds of credential reach the streaming API, and the Python reporter has a class for each:

| Class | Credential | Header | Server endpoints |
|---|---|---|---|
| `LiveStream` (recommended) | A **project-scoped API key** with the `stream:write` scope (or no scopes) | `X-API-Key` | `POST /api/v1/stream/ingest`; the server derives the project from the key and creates the session on the first batch |
| `TestLookupReporter`, and the pytest plugin built on it | A JWT access token (`/api/v1/auth/login`), or an API key passed as `api_key` in configuration with no `token` | `Authorization: Bearer` (token) or `X-API-Key` (key) | `POST /api/v1/stream/sessions`, `/stream/events/batch`, `DELETE /stream/sessions/{id}` |

An ADMIN creates a project-scoped streaming key under **Settings → Streaming API keys** (`/settings/api-keys`; the dialog that shows the new key also offers a ready-made `testlookup.properties` block), or with `POST /api/v1/keys` and a `project_id`. Keys start with `qai_` and are shown once. See [docs/api/README.md](../docs/api/README.md#credential-types) for scopes and expiry.

## Python

### Install (from source)

```bash
git clone https://github.com/test-intelligence/testlookup.git
pip install ./testlookup/client            # or: pip install -e ./testlookup/client while developing
pip install "./testlookup/client[yaml]"    # adds PyYAML, needed only for the legacy testlookup.yaml
```

The distribution is `testlookup-reporter` (Python 3.9 or newer, depends on `httpx`). It installs three modules, `testlookup_reporter`, `ci_context` and `commit_range`, and registers a pytest plugin through the `pytest11` entry point, so installing it is enough to make the plugin available.

### `LiveStream` (API key)

```python
import asyncio
from testlookup_reporter import LiveStream

async def main():
    async with LiveStream(
        base_url="https://testlookup.example.com",   # or testlookup.endpoint / TESTLOOKUP_ENDPOINT
        api_key="qai_your_api_key_here",              # or testlookup.api.key / TESTLOOKUP_API_KEY
        run_id="ci-build-42",                         # required: any stable id for this run
        launch_name="Smoke suite",
        build_number="42",
        branch="main",
    ) as stream:
        await stream.record("test_login", "PASSED", 120)
        await stream.record("test_logout", "FAILED", 340,
                            error="AssertionError: expected 200",
                            suite_name="auth", class_name="tests.test_auth")

asyncio.run(main())
```

- `record(test_name, status, duration_ms=0, *, suite_name, class_name, error, stack_trace, tags, metadata)`; status is `PASSED`, `FAILED`, `SKIPPED` or `BROKEN`. `log(message, level)` and `metric(name, value, unit)` send log lines and numeric metrics.
- Events are batched (50 events or 100 ms, whichever comes first; at most 1,000 per request) and retried with back-off; a 429 or 503 honours `Retry-After`. Each batch carries a client-generated `batch_id` reused on every retry, so the server de-duplicates a batch delivered twice.
- A heartbeat is sent after 30 s without events so a slow test does not look like a dead session.
- Leaving the `async with` block flushes the queue and posts a `run_complete` event, which finalises the run on the server. Call `await stream.close()` yourself if you do not use the context manager.

Other constructor options: `commit_hash`, `framework` (default `python`), `total_tests`, `machine_id`, `release_name`, `metadata`, the CI-context overrides (`ci_provider`, `ci_repo`, `pr_number`, `ci_actor`, `ci_run_url`), `commit_range_base`, `collect_commit_range`, `batch_size`, `batch_interval_ms`, `verify_ssl` (`True`, `False` or a CA bundle path).

### `TestLookupReporter` (session API)

```python
from testlookup_reporter import TestLookupReporter

reporter = TestLookupReporter(
    base_url="http://localhost:8000",
    token="<JWT access token>",
    project_id="<project UUID or name>",
)
async with reporter.session(build_number="build-42", branch="main") as session:
    await session.record("test_login", "PASSED", 120)
await reporter.aclose()
```

`session(...)` registers a live session, yields a `LiveSession` with the same `record` / `log` / `metric` methods, and closes the session on exit. It also accepts `commit_hash`, `total_tests`, `machine_id`, `launch_name`, `suite_name`, `release_name`, the CI-context fields and `commit_range`.

### pytest plugin

With the package installed, the plugin activates when it can resolve a server URL, a credential and a project, from command-line options or from configuration:

| Option | Environment variable | Meaning |
|---|---|---|
| `--testlookup-url` | `TESTLOOKUP_URL` | Server URL |
| `--testlookup-token` | `TESTLOOKUP_TOKEN` | Credential (sent as `Authorization: Bearer`) |
| `--testlookup-project` | `TESTLOOKUP_PROJECT_ID` | Project UUID or name |
| `--testlookup-build` | `TESTLOOKUP_BUILD` | Build number (default `pytest-<unix time>`) |
| `--testlookup-branch` | `TESTLOOKUP_BRANCH` | Git branch |
| `--testlookup-launch` | `TESTLOOKUP_LAUNCH` | Launch label |

If the options leave the URL, credential or project unset, the plugin falls back to the configuration file and the variables in the next section. It reports each test as `PASSED`, `FAILED`, `SKIPPED` or `BROKEN` (a setup or teardown error), with the test file as the suite.

**Known issue:** the plugin passes whatever credential it resolves as a bearer token, including an API key from `testlookup.api.key` / `TESTLOOKUP_API_KEY`. The server accepts API keys only in the `X-API-Key` header, so with an API key the plugin's session creation fails with 401. Until this is fixed, give the plugin a JWT access token (`--testlookup-token` or `TESTLOOKUP_TOKEN`), or use `LiveStream` from a `conftest.py` for API-key streaming.

### Configuration

Precedence, highest first: constructor arguments (or plugin options), then environment variables, then **one** configuration file, then built-in defaults.

**Configuration file.** The first file found in this order is read; the others are ignored:

1. `./testlookup.properties`
2. `./.testlookup/testlookup.properties`
3. `./testlookup.yaml`
4. `./.testlookup/config.yaml`
5. `~/.testlookup/testlookup.properties`
6. `~/.testlookup/config.yaml`

`testlookup.properties` is the preferred format (`key=value` or `key: value`, `#` and `!` comments). The YAML form is the legacy nested schema shown in [testlookup.yaml.example](testlookup.yaml.example) and is skipped unless PyYAML is installed.

```properties
testlookup.endpoint=https://testlookup.example.com
testlookup.api.key=qai_your_api_key_here
testlookup.project=<project UUID or name>
testlookup.launch=Smoke suite
```

**Keys and variables**

| Property | Environment variable | Used for |
|---|---|---|
| `testlookup.endpoint` (alias `testlookup.url`) | `TESTLOOKUP_ENDPOINT` (canonical) or `TESTLOOKUP_URL` (legacy); `TESTLOOKUP_ENDPOINT` wins if both are set | Server URL |
| `testlookup.api.key` (alias `testlookup.api_key`) | `TESTLOOKUP_API_KEY` | API key (`X-API-Key`) |
| `testlookup.token` | `TESTLOOKUP_TOKEN` | JWT access token (`Authorization: Bearer`) |
| `testlookup.project` | `TESTLOOKUP_PROJECT` (canonical) or `TESTLOOKUP_PROJECT_ID` (legacy) | Project UUID or name (`TestLookupReporter` and the plugin; `LiveStream` takes the project from the key) |
| `testlookup.launch` | `TESTLOOKUP_LAUNCH` | Launch label; also the suite fallback |
| `testlookup.suite` | `TESTLOOKUP_SUITE` | Default suite for records that name none |
| `testlookup.release` | `TESTLOOKUP_RELEASE` | Release of the run (`TestLookupReporter.session()` only; `LiveStream` takes `release_name=`). Blank: the project's default release |
| `testlookup.build`, `testlookup.branch`, `testlookup.commit` | `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT` | CI metadata |
| `testlookup.framework` | `TESTLOOKUP_FRAMEWORK` | Framework label (`LiveStream`) |
| `testlookup.ca_cert_path` | `TESTLOOKUP_CA_CERT` | CA bundle to verify the server's certificate with (`LiveStream` only) |
| `testlookup.insecure` | `TESTLOOKUP_INSECURE` | `true` disables certificate verification (`LiveStream` only; logs a warning; never in CI) |
| `testlookup.ci_provider`, `testlookup.ci_repo`, `testlookup.pr_number`, `testlookup.ci_actor`, `testlookup.ci_run_url` | `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR`, `TESTLOOKUP_CI_RUN_URL` | Override the detected CI context |
| `testlookup.commit_range`, `testlookup.commit_range_base` | `TESTLOOKUP_COMMIT_RANGE`, `TESTLOOKUP_COMMIT_RANGE_BASE` | Commit-range collection (see below) |
| none | `TESTLOOKUP_UPLOAD_MODE` | Mapped by the loader but not used by the reporter |

An empty environment variable counts as unset. Keep keys and tokens in environment variables or CI secrets rather than in a committed file.

### CI context and commit range

The reporter detects the CI provider, repository, pull/merge request number, actor and run URL from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`ci_context.py`); configuration and `TESTLOOKUP_CI_*` variables override detection, and explicit arguments override both. Branch, build number and commit are not detected; set them yourself.

It also collects the commit range (base..`HEAD`, up to 100 commits with their changed files) from the local git checkout (`commit_range.py`) so the server can attribute failures to commits. Pin the base with `commit_range_base=` / `TESTLOOKUP_COMMIT_RANGE_BASE`, or turn collection off with `collect_commit_range=False` / `TESTLOOKUP_COMMIT_RANGE=0`. Both are best-effort and never fail a run; a shallow clone yields a head-only range. Details: [user-guide/getting-results-in.md](../user-guide/getting-results-in.md#commit-range-who-changed-what).

### Replay-safe live identity

All bundled reporters serialise a client-generated `batch_id` once before the first POST and reuse it verbatim for transport and back-pressure retries. The server derives each event's identity as SHA-256 of `session_id`, `batch_id` and the event's zero-based position in the batch, so event order is part of the wire contract and must not change on retry. `batch_id` is optional on the server so older reporters stay wire-compatible.

### Known limitations (Python)

Today's behaviour, to be removed from this list as each is fixed:

- **pytest plugin + API key:** the plugin sends any credential as
  `Authorization: Bearer`, and the server accepts API keys only in
  `X-API-Key`, so the plugin needs a **JWT access token**
  (`--testlookup-token` / `TESTLOOKUP_TOKEN`). API-key streaming goes through
  `LiveStream`.
- The pytest plugin does not apply the configured suite or release defaults
  (`TESTLOOKUP_SUITE`, `TESTLOOKUP_RELEASE`); each test's suite is its file.
- `TESTLOOKUP_UPLOAD_MODE` is read into the configuration but changes nothing.
- `TESTLOOKUP_CA_CERT` / `TESTLOOKUP_INSECURE` (and the matching properties)
  are honoured by `LiveStream` only.

## Downloading reporters from a running server

`GET /api/v1/sdk` (no sign-in needed) lists the reporters a server can hand out, and `GET /api/v1/sdk/{python|java|js|go}` downloads one: a ZIP of the sources, or for Java the prebuilt `-all` JAR when the image contains one. The files come from the `client/` directory staged into the backend image (or mounted at `SDK_PATH`); images built by CI contain the sources only.

## Tests

```bash
python -m pytest client/tests    # from the repository root
```
