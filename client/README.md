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
| `TestLookupReporter`, and the pytest plugin built on it | An API key, or a JWT access token (`/api/v1/auth/login`) | `X-API-Key` (key) or `Authorization: Bearer` (token) | `POST /api/v1/stream/sessions`, `/stream/events/batch`, `DELETE /stream/sessions/{id}` |

The token and the key resolve as a pair, the same way in every bundled reporter: one passed explicitly (argument or command-line option) wins; otherwise the first source holding either one wins, environment variables before the configuration file; within one source the API key wins. A value that starts with `qai_` is always sent as an API key, whichever option carries it.

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
    base_url="https://testlookup.example.com",
    api_key="qai_your_api_key_here",          # or token="<JWT access token>"
    project_id="<project UUID or name>",
)
async with reporter.session(build_number="build-42", branch="main") as session:
    await session.record("test_login", "PASSED", 120)
await reporter.aclose()
```

`session(...)` registers a live session, yields a `LiveSession` with the same `record` / `log` / `metric` methods, and closes the session on exit (`DELETE /stream/sessions/{id}`), which finalises the run on the server just as `LiveStream`'s `run_complete` does. It also accepts `commit_hash`, `total_tests`, `machine_id`, `launch_name`, `suite_name`, `release_name`, the CI-context fields and `commit_range`; any of build, branch, commit, launch, suite and release left out comes from configuration. Other constructor options: `framework` (default `testlookup.framework`, else `python`), `client_name`, `batch_size`, `batch_interval_ms`, `verify_ssl` (default from `testlookup.ca_cert_path` / `testlookup.insecure`), `commit_range_base`, `collect_commit_range`.

### pytest plugin

With the package installed, the plugin activates when it can resolve a server URL, a credential and a project, from command-line options or from configuration:

| Option | Environment variable | Meaning |
|---|---|---|
| `--testlookup-url` | `TESTLOOKUP_ENDPOINT` or `TESTLOOKUP_URL` | Server URL |
| `--testlookup-api-key` | `TESTLOOKUP_API_KEY` | API key (sent as `X-API-Key`) |
| `--testlookup-token` | `TESTLOOKUP_TOKEN` | JWT access token (sent as `Authorization: Bearer`; a `qai_` value is sent as an API key) |
| `--testlookup-project` | `TESTLOOKUP_PROJECT` or `TESTLOOKUP_PROJECT_ID` | Project UUID or name |
| `--testlookup-build` | `TESTLOOKUP_BUILD` | Build number (default `pytest-<unix time>`) |
| `--testlookup-branch` | `TESTLOOKUP_BRANCH` | Git branch |
| `--testlookup-launch` | `TESTLOOKUP_LAUNCH` | Launch label |

An option left unset falls back to its environment variable and then the configuration file, exactly as in the next section; commit, suite, release, framework (default `pytest`) and the TLS settings come from there too. The plugin reports each test as `PASSED`, `FAILED`, `SKIPPED` or `BROKEN` (a setup or teardown error), with the test file as the suite, and closes the session when pytest finishes.

### Robot Framework listener

`testlookup_robot_listener.py` (in the same ZIP) is a Listener API v3 listener on the same core:

```bash
robot --listener testlookup_robot_listener.TestLookupListener tests/
robot --listener "testlookup_robot_listener.TestLookupListener:project=<project-id>:url=https://testlookup.example.com" tests/
```

Arguments (`url`, `project`, `api_key`, `token`, `build`, `branch`, `launch`) win over `TESTLOOKUP_*` and the
config file. Each test is one result when it ends (PASS / FAIL / SKIP / NOT RUN -> PASSED / FAILED / SKIPPED),
its suite's long name as the suite. Robot Framework 5, 6 and 7.

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
| `testlookup.release` | `TESTLOOKUP_RELEASE` | Release of the run. Blank: the project's default release |
| `testlookup.build`, `testlookup.branch`, `testlookup.commit` | `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH`, `TESTLOOKUP_COMMIT` | CI metadata |
| `testlookup.framework` | `TESTLOOKUP_FRAMEWORK` | Framework label (default `python`; the pytest plugin's default is `pytest`) |
| `testlookup.ca_cert_path` | `TESTLOOKUP_CA_CERT` | CA bundle (file or directory) to verify the server's certificate with; only that CA is trusted |
| `testlookup.insecure` | `TESTLOOKUP_INSECURE` | `true` disables certificate verification (logs a warning; never in CI) |
| `testlookup.ci_provider`, `testlookup.ci_repo`, `testlookup.pr_number`, `testlookup.ci_actor`, `testlookup.ci_run_url` | `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR`, `TESTLOOKUP_CI_RUN_URL` | Override the detected CI context |
| `testlookup.commit_range`, `testlookup.commit_range_base` | `TESTLOOKUP_COMMIT_RANGE`, `TESTLOOKUP_COMMIT_RANGE_BASE` | Commit-range collection (see below) |

Every variable applies the same way to `TestLookupReporter`, the pytest plugin and `LiveStream` (except the token and project, which `LiveStream` does not use). An empty environment variable counts as unset. Keep keys and tokens in environment variables or CI secrets rather than in a committed file.

### CI context and commit range

The reporter detects the CI provider, repository, pull/merge request number, actor and run URL from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`ci_context.py`); configuration and `TESTLOOKUP_CI_*` variables override detection, and explicit arguments override both. Branch, build number and commit are not detected; set them yourself.

It also collects the commit range (base..`HEAD`, up to 100 commits with their changed files) from the local git checkout (`commit_range.py`) so the server can attribute failures to commits. Pin the base with `commit_range_base=` / `TESTLOOKUP_COMMIT_RANGE_BASE`, or turn collection off with `collect_commit_range=False` / `TESTLOOKUP_COMMIT_RANGE=0`. Both are best-effort and never fail a run; a shallow clone yields a head-only range. Details: [user-guide/getting-results-in.md](../user-guide/getting-results-in.md#commit-range-who-changed-what).

### Replay-safe live identity

All bundled reporters serialise a client-generated `batch_id` once before the first POST and reuse it verbatim for transport and back-pressure retries. The server derives each event's identity as SHA-256 of `session_id`, `batch_id` and the event's zero-based position in the batch, so event order is part of the wire contract and must not change on retry. `batch_id` is optional on the server so older reporters stay wire-compatible.

## Downloading reporters from a running server

`GET /api/v1/sdk` (no sign-in needed) lists the reporters a server can hand out, and `GET /api/v1/sdk/{python|java|js|go}` downloads one: a ZIP of the sources, or for Java the prebuilt `-all` JAR when the image contains one. The files come from the `client/` directory staged into the backend image (or mounted at `SDK_PATH`); images built by CI contain the sources only.

## Tests

```bash
python -m pytest client/tests    # from the repository root
```
