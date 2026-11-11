---
title: Make the Go TestReporter safe to call when reporting is off
labels: [good first issue, sdk, go, bug]
---

## What

The Go SDK README shows a `TestMain` that runs the tests without reporting when TestLookup is not configured (`client/go/README.md:49-58`). In that path the package-level `tr` stays `nil`. Each test then runs `defer tr.RecordTest(t, ...)` (`client/go/README.md:67`), and `RecordTest` reads `tr.session` (`client/go/testlookup/testing_helper.go:90`). On a nil `*TestReporter` that is a nil pointer dereference, so the first test panics. The same applies to `Flush` (`client/go/testlookup/testing_helper.go:118`), `RecordBenchmark` (`:164`), and `T(t).Done()` (`:234`, which calls `RecordTest`).

## Why it helps

The documented way to run tests without a server crashes. A developer who has not set `TESTLOOKUP_URL` locally hits this on the first test.

## Where to start

- `client/go/testlookup/testing_helper.go:66-121` (`NewTestReporter`, `RecordTest`, `RunTest`, `Flush`) and `:161-165`, `:228-235` (`RecordBenchmark`, `T`, `Done`).
- Suggested fix: return early when the receiver is nil, or when its `session` is nil, in `RecordTest`, `Flush` and `RecordBenchmark`. `RunTest` must still run `fn` when reporting is off. Methods on a nil pointer receiver are legal in Go, so the README example then works unchanged.
- Test to copy: `client/go/testlookup/timing_test.go:72-84` (`TestRecordTestSendsTheTestsStart`) and the `bufferedSession()` helper at `client/go/testlookup/timing_test.go:14-20`.

## Done when

- [ ] On a nil `*TestReporter`, `RecordTest`, `RunTest`, `Flush`, `RecordBenchmark` and `T(t).Done()` do nothing and do not panic. `RunTest` still calls its function.
- [ ] A new test in `client/go/testlookup/` calls each of them on a nil `*TestReporter` and checks that `RunTest` ran its function.
- [ ] The `TestReporter` helper table in `client/go/README.md` (from line 72) says these methods are no-ops when reporting is off.
- [ ] In `client/go`: `go vet ./...` and `go test ./...` pass. CI runs the same two commands (`.github/workflows/ci.yml:1533-1537`).
- [ ] A line under `[Unreleased]` → Fixed in `CHANGELOG.md`.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About 1–2 hours; no Docker stack needed. Go 1.21 or newer (`client/go/go.mod`); the module has no external dependencies.
