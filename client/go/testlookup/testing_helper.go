package testlookup

import (
	"context"
	"fmt"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"
)

// ── TestReporter wraps a Session for use with Go's testing package ─────────────

// TestReporter integrates a TestLookup Session with Go's *testing.T / *testing.B.
//
// # Usage with go test
//
//	func TestMain(m *testing.M) {
//	    reporter, _ := testlookup.NewFromEnv()
//	    session, _ := reporter.StartSession(context.Background(), testlookup.SessionOptions{
//	        BuildNumber: os.Getenv("CI_BUILD_NUMBER"),
//	        Branch:      os.Getenv("GIT_BRANCH"),
//	    })
//	    tr := testlookup.NewTestReporter(session, "my/pkg")
//
//	    code := m.Run()
//	    tr.Flush(context.Background())
//	    os.Exit(code)
//	}
//
//	func TestLogin(t *testing.T) {
//	    defer tr.RecordTest(t, "TestLogin", time.Now())
//	    // ... test body ...
//	}
//
// # Usage with testify/suite
//
//	type MySuite struct {
//	    suite.Suite
//	    tr *testlookup.TestReporter
//	}
//
//	func (s *MySuite) SetupSuite() {
//	    reporter, _ := testlookup.NewFromEnv()
//	    session, _ := reporter.StartSession(...)
//	    s.tr = testlookup.NewTestReporter(session, "MySuite")
//	}
//
//	func (s *MySuite) TearDownSuite() {
//	    s.tr.Flush(context.Background())
//	}
//
//	func (s *MySuite) TestLogin() {
//	    start := time.Now()
//	    defer s.tr.RecordTest(s.T(), "TestLogin", start)
//	    // ... test body ...
//	}
type TestReporter struct {
	session   *Session
	suiteName string
}

// NewTestReporter creates a TestReporter wrapping the given session.
// suiteName is used as the suite_name in all recorded events (e.g. package path).
func NewTestReporter(session *Session, suiteName string) *TestReporter {
	return &TestReporter{session: session, suiteName: suiteName}
}

// RecordTest records the result of a *testing.T test.
// Call with `defer tr.RecordTest(t, t.Name(), time.Now())` at the start of each test.
//
// The result (PASSED/FAILED/SKIPPED) is derived from t.Failed() and t.Skipped()
// after the test body returns.
func (tr *TestReporter) RecordTest(t testing.TB, testName string, start time.Time) {
	t.Helper()
	durationMs := time.Since(start).Milliseconds()

	status := Passed
	var errMsg string

	switch {
	case t.Skipped():
		status = Skipped
	case t.Failed():
		status = Failed
		errMsg = fmt.Sprintf("test %s failed", testName)
	}

	tr.session.Record(context.Background(), testName, status, durationMs, RecordOptions{
		SuiteName: tr.suiteName,
		Error:     errMsg,
	})
}

// RunTest wraps a test function: runs fn, records the result, and returns.
// Use this for table-driven tests or subtests.
//
//	for _, tc := range cases {
//	    tc := tc
//	    t.Run(tc.name, func(t *testing.T) {
//	        tr.RunTest(t, tc.name, func() {
//	            // ... test body ...
//	        })
//	    })
//	}
func (tr *TestReporter) RunTest(t testing.TB, testName string, fn func()) {
	t.Helper()
	start := time.Now()
	fn()
	tr.RecordTest(t, testName, start)
}

// Flush flushes buffered events and closes the session on the server.
// Call this once at the end of TestMain (after m.Run() returns).
func (tr *TestReporter) Flush(ctx context.Context) {
	if err := tr.session.Close(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "[TestLookup] failed to close session: %v\n", err)
	}
}

// ── NewFromEnv creates a Reporter from environment variables ──────────────────

// NewFromEnv creates a Reporter using environment variables for all required fields:
//
//	TESTLOOKUP_URL          - server base URL (required)
//	TESTLOOKUP_TOKEN        - JWT access token (required)
//	TESTLOOKUP_PROJECT_ID   - target project UUID (required)
//
// Returns an error if any required variable is unset.
func NewFromEnv() (*Reporter, error) {
	return New(Config{}) // withDefaults() reads env vars
}

// ── SessionOptionsFromEnv reads common CI env vars ────────────────────────────

// SessionOptionsFromEnv returns a SessionOptions populated from common CI
// environment variables. Override individual fields as needed:
//
//	opts := testlookup.SessionOptionsFromEnv()
//	opts.BuildNumber = myCustomBuildID
//	session, err := reporter.StartSession(ctx, opts)
func SessionOptionsFromEnv() SessionOptions {
	return SessionOptions{
		BuildNumber: envFirst("CI_BUILD_NUMBER", "BUILD_NUMBER", "GITHUB_RUN_NUMBER", ""),
		Branch:      envFirst("CI_COMMIT_BRANCH", "GIT_BRANCH", "GITHUB_REF_NAME", ""),
		CommitHash:  envFirst("CI_COMMIT_SHA", "GIT_COMMIT", "GITHUB_SHA", ""),
	}
}

// ── Benchmark helper ──────────────────────────────────────────────────────────

// RecordBenchmark records a benchmark result as a metric event.
//
//	func BenchmarkLogin(b *testing.B) {
//	    start := time.Now()
//	    for i := 0; i < b.N; i++ { /* ... */ }
//	    tr.RecordBenchmark(b, "BenchmarkLogin", start)
//	}
func (tr *TestReporter) RecordBenchmark(b *testing.B, name string, start time.Time) {
	b.Helper()
	ns := time.Since(start).Nanoseconds()
	tr.session.Metric(context.Background(), name, float64(ns)/1e6, "ms")
}

// ── Package-level test suite integration ─────────────────────────────────────

// RunSuite is a convenience wrapper for running a TestMain-style suite with
// TestLookup reporting. It creates a reporter and session from environment
// variables, runs m.Run(), and then closes the session.
//
//	func TestMain(m *testing.M) {
//	    testlookup.RunSuite(m, "my/package/path")
//	}
func RunSuite(m *testing.M, suiteName string) {
	reporter, err := NewFromEnv()
	if err != nil {
		// TestLookup not configured — run without reporting
		os.Exit(m.Run())
	}

	opts := SessionOptionsFromEnv()
	if opts.BuildNumber == "" {
		opts.BuildNumber = fmt.Sprintf("go-test-%d", time.Now().Unix())
	}
	if suiteName != "" {
		// Use the last two path components as build label if no build number set
		parts := strings.Split(suiteName, "/")
		if len(parts) >= 2 {
			opts.BuildNumber = strings.Join(parts[len(parts)-2:], "/") + "-" + fmt.Sprint(time.Now().Unix())
		}
	}

	ctx := context.Background()
	session, err := reporter.StartSession(ctx, opts)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[TestLookup] failed to start session: %v — running without reporting\n", err)
		os.Exit(m.Run())
	}

	code := m.Run()

	if err := session.Close(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "[TestLookup] failed to close session: %v\n", err)
	}

	os.Exit(code)
}

// ── Subtest wrapper ───────────────────────────────────────────────────────────

// T wraps a *testing.T to automatically record results.
// Use it as a drop-in for *testing.T when you want recording without explicit calls.
//
//	func TestSomething(t *testing.T) {
//	    qt := tr.T(t)
//	    qt.Run("subtest1", func(t *testing.T) { /* t here is original */ })
//	}
type T struct {
	*testing.T
	tr    *TestReporter
	start time.Time
	name  string
}

// T wraps t in a recording T. The result is recorded when the test function returns.
func (tr *TestReporter) T(t *testing.T) *T {
	return &T{T: t, tr: tr, start: time.Now(), name: t.Name()}
}

// Done records the result. Call `defer qt.Done()` after constructing.
func (qt *T) Done() {
	qt.tr.RecordTest(qt.T, qt.name, qt.start)
}

// ── Utilities ─────────────────────────────────────────────────────────────────

func envFirst(keys ...string) string {
	for i, k := range keys {
		if i == len(keys)-1 {
			return k // last element is default
		}
		if v := os.Getenv(k); v != "" {
			return v
		}
	}
	return ""
}

// CallerPackage returns the package path of the calling test file.
// Useful for automatically setting suiteName:
//
//	tr := testlookup.NewTestReporter(session, testlookup.CallerPackage())
func CallerPackage() string {
	_, file, _, ok := runtime.Caller(1)
	if !ok {
		return "unknown"
	}
	// Strip everything after last '/'
	idx := strings.LastIndex(file, "/")
	if idx >= 0 {
		file = file[:idx]
	}
	// Return last two path components
	parts := strings.Split(file, "/")
	if len(parts) >= 2 {
		return strings.Join(parts[len(parts)-2:], "/")
	}
	return file
}
