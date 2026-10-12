# TestLookup reporter for Go

Streams Go test results to a TestLookup server while the tests run. Results appear on **Runs → Live** (`/live`) during the run and on Runs, Failures and Trends when the session closes. Pure standard library, no third-party dependencies.

Other reporters and the shared concepts (credentials, live identity) are in [client/README.md](../README.md).

## Module and install (from source)

`go.mod` declares the module as:

```
module github.com/testlookup/testlookup-go
```

with the package in the `testlookup` subdirectory, so the import path is `github.com/testlookup/testlookup-go/testlookup` (Go 1.21 or newer). That module path is not where the code lives (this directory, `client/go`, in [github.com/test-intelligence/testlookup](https://github.com/test-intelligence/testlookup)), and nothing is published under it, so `go get` cannot fetch it. Use the module from a clone with a `replace` directive in your own `go.mod`:

```
require github.com/testlookup/testlookup-go v0.0.0

replace github.com/testlookup/testlookup-go => /path/to/testlookup/client/go
```

```go
import "github.com/testlookup/testlookup-go/testlookup"
```

Run the package's own tests with `go test ./...` in `client/go`.

## Credentials

The Go client authenticates with a **JWT access token**, sent as `Authorization: Bearer <token>`; there is no API-key option. Get one with `testlookup.Login(ctx, baseURL, username, password)` or `POST /api/v1/auth/login` at the start of the job. Access tokens last 12 hours by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` on the server). The user needs the QA_ENGINEER role or higher on the target project.

## Usage

### With `go test`: `TestMain` and a `TestReporter`

```go
package mypkg_test

import (
    "context"
    "os"
    "testing"
    "time"

    "github.com/testlookup/testlookup-go/testlookup"
)

var tr *testlookup.TestReporter

func TestMain(m *testing.M) {
    reporter, err := testlookup.NewFromEnv() // TESTLOOKUP_URL, TESTLOOKUP_TOKEN, TESTLOOKUP_PROJECT_ID
    if err != nil {
        os.Exit(m.Run()) // not configured: run without reporting
    }
    session, err := reporter.StartSession(context.Background(), testlookup.SessionOptionsFromEnv())
    if err != nil {
        os.Exit(m.Run())
    }
    tr = testlookup.NewTestReporter(session, "mypkg")
    code := m.Run()
    tr.Flush(context.Background()) // flush and close the session
    os.Exit(code)
}

func TestLogin(t *testing.T) {
    defer tr.RecordTest(t, "TestLogin", time.Now())
    // ... test body ...
}
```

`TestReporter` helpers:

| Helper | What it does |
|---|---|
| `RecordTest(t, name, start)` | Records `PASSED`, `FAILED` or `SKIPPED` from `t.Failed()` / `t.Skipped()` and the elapsed time. Use it with `defer` at the top of a test. A failure is recorded with the message `test <name> failed`, not the assertion text. |
| `RunTest(t, name, fn)` | Runs `fn` and records it; convenient for table-driven subtests. |
| `T(t)` then `defer qt.Done()` | The same, as a wrapper around `*testing.T`. |
| `RecordBenchmark(b, name, start)` | Records the elapsed time as a metric event (milliseconds). |
| `Flush(ctx)` | Flushes buffered events and closes the session on the server. Call it once after `m.Run()`. |
| `CallerPackage()` | The last two path components of the caller's directory, handy as a suite name. |

`SessionOptionsFromEnv()` fills `BuildNumber` from `CI_BUILD_NUMBER`, `BUILD_NUMBER` or `GITHUB_RUN_NUMBER`, `Branch` from `CI_COMMIT_BRANCH`, `GIT_BRANCH` or `GITHUB_REF_NAME`, and `CommitHash` from `CI_COMMIT_SHA`, `GIT_COMMIT` or `GITHUB_SHA` (first one set wins).

`testlookup.RunSuite(m, suiteName)` is a shortcut that creates a reporter and session from the environment, runs `m.Run()` and closes the session, but it does not hand you a `TestReporter`, so tests cannot record results through it; with a non-empty `suiteName` it also replaces the build number with `<last two path components>-<unix time>`. Prefer the explicit `TestMain` above.

### Direct client

```go
reporter, err := testlookup.New(testlookup.Config{
    BaseURL:   "https://testlookup.example.com",
    Token:     token,
    ProjectID: "<project UUID or name>",
})
if err != nil { log.Fatal(err) }

session, err := reporter.StartSession(ctx, testlookup.SessionOptions{BuildNumber: "build-42", Branch: "main"})
if err != nil { log.Fatal(err) }
defer session.Close(ctx) // flushes, then closes the session on the server

session.Record(ctx, "TestLogin", testlookup.Passed, 120, testlookup.RecordOptions{})
session.Record(ctx, "TestCheckout", testlookup.Failed, 340, testlookup.RecordOptions{
    Error: "expected status 200, got 500", SuiteName: "checkout",
})
```

- `Config`: `BaseURL`, `Token`, `ProjectID` (required; empty values fall back to `TESTLOOKUP_URL`, `TESTLOOKUP_TOKEN`, `TESTLOOKUP_PROJECT_ID`), `ClientName` (default the host name), `Framework` (default `go`), `SuiteName` (default `TESTLOOKUP_SUITE`, else `TESTLOOKUP_LAUNCH`), `ReleaseName` (default `TESTLOOKUP_RELEASE`; blank means the project's default release), `BatchSize` (default 50, at most 1,000), `BatchInterval` (default 100 ms), `HTTPClient` (your own `*http.Client`, for example with a custom TLS root pool).
- `SessionOptions`: `BuildNumber`, `RunID`, `Branch`, `CommitHash`, `MachineID`, `TotalTests`, `LaunchName`, `SuiteName`, `ReleaseName`, `Metadata`, and the CI-context fields below. `New` does not read `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH` or `TESTLOOKUP_COMMIT`; set those fields yourself or use `SessionOptionsFromEnv()`.
- `Session`: `Record(ctx, name, status, durationMs, RecordOptions{SuiteName, ClassName, Error, StackTrace, Tags, Metadata})` with `testlookup.Passed`, `Failed`, `Skipped` or `Broken`; `Log(ctx, message, level)`; `Metric(ctx, name, value, unit)`; `Stats()`; `Close(ctx)`. All methods are safe for concurrent use.
- Events are batched (50 events or 100 ms) and retried up to 5 times with exponential back-off; each batch keeps its client-generated `batch_id` across retries (see [Replay-safe live identity](../README.md#replay-safe-live-identity)).

## Configuration

`New` and `NewFromEnv` read only the environment variables listed above. The package also exports `LoadConfig()`, which reads the first of `./testlookup.properties`, `./.testlookup/testlookup.properties` and `~/.testlookup/testlookup.properties` (no YAML), overlays `TESTLOOKUP_*` variables (including `TESTLOOKUP_ENDPOINT`, `TESTLOOKUP_PROJECT` and `TESTLOOKUP_API_KEY`) and returns a `ResolvedConfig`. Nothing calls it for you, and `Config` has no API-key field, so copy the values you need into `Config` yourself:

```go
rc := testlookup.LoadConfig()
reporter, err := testlookup.New(testlookup.Config{
    BaseURL: rc.BaseURL, Token: rc.Token, ProjectID: rc.ProjectID,
    SuiteName: rc.SuiteName, ReleaseName: rc.ReleaseName,
})
```

## CI context

`StartSession` detects the CI provider, repository, pull/merge request number, actor and run URL from the standard variables of GitHub Actions, GitLab CI, Jenkins, Azure DevOps and CircleCI (`cicontext.go`; `DetectCIContext()` exposes the detection). `TESTLOOKUP_CI_PROVIDER`, `TESTLOOKUP_CI_REPO`, `TESTLOOKUP_PR_NUMBER`, `TESTLOOKUP_CI_ACTOR` and `TESTLOOKUP_CI_RUN_URL` override detection, and the `CIProvider`, `CIRepo`, `PRNumber`, `CIActor` and `CIRunURL` session options override both. The Go client does not collect a commit range.

## Known limitations

Today's behaviour, to be removed from this list as each is fixed:

- Authentication is a **JWT access token** only; there is no API-key option.
- The module path declared in `go.mod` (`github.com/testlookup/testlookup-go`)
  does not match the repository, so `go get` cannot fetch it; use a `replace`
  directive pointing at a clone, as shown above.
- `New` does not read `TESTLOOKUP_BUILD`, `TESTLOOKUP_BRANCH` or
  `TESTLOOKUP_COMMIT`, although the package documentation says so; use
  `SessionOptionsFromEnv()`.

## License

Apache-2.0, like the rest of the repository.
