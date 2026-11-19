package testlookup

// GAP-E05: the documented entry points of the Go SDK (New, StartSession,
// Record, Close, Login) were at 0% coverage. Each test here runs them against
// an httptest.Server and pins one promise from client/go/README.md:
//
//   - New(Config{}) falls back to TESTLOOKUP_URL / TESTLOOKUP_TOKEN / TESTLOOKUP_PROJECT_ID;
//   - StartSession sends "Authorization: Bearer <token>" and the detected CI fields;
//   - BatchSize is capped at 1,000;
//   - Close flushes what is still buffered, THEN deletes the session, in that order;
//   - Login posts form-encoded credentials and returns the token; a 401 is an error.
//
// The batch-retry policy on 4xx is an open owner question (Go retries every
// non-2xx, Python and JS only 429/503/5xx), so nothing here pins it.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	fakeToken     = "ci-jwt-token-test-only"
	fakeProjectID = "11111111-1111-4111-8111-111111111111"
)

type recordedRequest struct {
	Method string
	Path   string
	Auth   string
	Token  string // X-Session-Token
	Body   map[string]interface{}
}

// fakeServer answers the four stream endpoints and records every request in order.
type fakeServer struct {
	*httptest.Server
	mu        sync.Mutex
	requests  []recordedRequest
	deleteErr int // status for DELETE when non-zero
	batches   chan int
}

func newFakeServer(t *testing.T) *fakeServer {
	t.Helper()
	fs := &fakeServer{batches: make(chan int, 16)}
	fs.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]interface{}
		_ = json.Unmarshal(raw, &body)
		fs.mu.Lock()
		fs.requests = append(fs.requests, recordedRequest{
			Method: r.Method,
			Path:   r.URL.Path,
			Auth:   r.Header.Get("Authorization"),
			Token:  r.Header.Get("X-Session-Token"),
			Body:   body,
		})
		deleteErr := fs.deleteErr
		fs.mu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/stream/sessions":
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"session_id":"sess-0001-test-only","session_token":"st-test-only","run_id":"run-0001-test-only"}`))
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/stream/events/batch":
			events, _ := body["events"].([]interface{})
			w.WriteHeader(http.StatusAccepted)
			_ = json.NewEncoder(w).Encode(map[string]int{"accepted": len(events)})
			fs.batches <- len(events)
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/api/v1/stream/sessions/"):
			if deleteErr != 0 {
				w.WriteHeader(deleteErr)
				return
			}
			w.WriteHeader(http.StatusNoContent)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(fs.Server.Close)
	return fs
}

func (fs *fakeServer) recorded() []recordedRequest {
	fs.mu.Lock()
	defer fs.mu.Unlock()
	out := make([]recordedRequest, len(fs.requests))
	copy(out, fs.requests)
	return out
}

// quietReporter never flushes on its own before Close: a long interval and a
// batch size above the number of events a test records.
func quietReporter(t *testing.T, baseURL string) *Reporter {
	t.Helper()
	setEnv(t, nil) // blank every CI and TESTLOOKUP_CI_* variable (cicontext_test.go)
	r, err := New(Config{
		BaseURL:       baseURL,
		Token:         fakeToken,
		ProjectID:     fakeProjectID,
		ClientName:    "ci-runner-test-only",
		BatchSize:     100,
		BatchInterval: time.Hour,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return r
}

func TestNewFallsBackToTheEnvironment(t *testing.T) {
	t.Setenv("TESTLOOKUP_URL", "http://testlookup.example.test/")
	t.Setenv("TESTLOOKUP_TOKEN", fakeToken)
	t.Setenv("TESTLOOKUP_PROJECT_ID", fakeProjectID)

	r, err := New(Config{})
	if err != nil {
		t.Fatalf("New(Config{}) with the env set: %v", err)
	}
	if r.cfg.BaseURL != "http://testlookup.example.test" {
		t.Errorf("BaseURL = %q, want the env URL without its trailing slash", r.cfg.BaseURL)
	}
	if r.cfg.Token != fakeToken {
		t.Errorf("Token = %q, want TESTLOOKUP_TOKEN", r.cfg.Token)
	}
	if r.cfg.ProjectID != fakeProjectID {
		t.Errorf("ProjectID = %q, want TESTLOOKUP_PROJECT_ID", r.cfg.ProjectID)
	}
	if r.cfg.BatchSize != defaultBatchSize || r.cfg.BatchInterval != defaultBatchInterval {
		t.Errorf("defaults = (%d, %s), want (%d, %s)", r.cfg.BatchSize, r.cfg.BatchInterval,
			defaultBatchSize, defaultBatchInterval)
	}
}

func TestNewExplicitConfigBeatsTheEnvironment(t *testing.T) {
	t.Setenv("TESTLOOKUP_URL", "http://env.example.test")
	t.Setenv("TESTLOOKUP_TOKEN", "env-token-test-only")
	t.Setenv("TESTLOOKUP_PROJECT_ID", "env-project")

	r, err := New(Config{BaseURL: "http://explicit.example.test", Token: fakeToken, ProjectID: fakeProjectID})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if r.cfg.BaseURL != "http://explicit.example.test" || r.cfg.Token != fakeToken || r.cfg.ProjectID != fakeProjectID {
		t.Errorf("cfg = %q %q %q, want the explicit values", r.cfg.BaseURL, r.cfg.Token, r.cfg.ProjectID)
	}
}

func TestNewRefusesAMissingRequiredField(t *testing.T) {
	t.Setenv("TESTLOOKUP_URL", "")
	t.Setenv("TESTLOOKUP_TOKEN", "")
	t.Setenv("TESTLOOKUP_PROJECT_ID", "")

	cases := []Config{
		{Token: fakeToken, ProjectID: fakeProjectID},
		{BaseURL: "http://testlookup.example.test", ProjectID: fakeProjectID},
		{BaseURL: "http://testlookup.example.test", Token: fakeToken},
	}
	for i, cfg := range cases {
		if r, err := New(cfg); err == nil || r != nil {
			t.Errorf("case %d: New(%+v) = (%v, %v), want an error", i, cfg, r, err)
		}
	}
}

func TestBatchSizeIsCappedAtOneThousand(t *testing.T) {
	r, err := New(Config{BaseURL: "http://testlookup.example.test", Token: fakeToken,
		ProjectID: fakeProjectID, BatchSize: 5000})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if r.cfg.BatchSize != 1000 {
		t.Fatalf("BatchSize = %d, want the 1,000 cap", r.cfg.BatchSize)
	}
}

func TestACappedBatchSizeFlushesAtOneThousandEvents(t *testing.T) {
	fs := newFakeServer(t)
	setEnv(t, nil)
	r, err := New(Config{BaseURL: fs.URL, Token: fakeToken, ProjectID: fakeProjectID,
		BatchSize: 5000, BatchInterval: time.Hour})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	ctx := context.Background()
	s, err := r.StartSession(ctx, SessionOptions{})
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	for i := 0; i < 1000; i++ {
		s.Record(ctx, "TestCap", Passed, 1, RecordOptions{})
	}
	// The 1,000th event fills a capped batch and triggers an eager flush. Without
	// the cap the buffer would wait for a 5,000-event batch or the hourly tick.
	select {
	case n := <-fs.batches:
		if n != 1000 {
			t.Errorf("eager batch carried %d events, want 1000", n)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("no batch was posted after 1,000 events: BatchSize was not capped at 1,000")
	}
	if err := s.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}
}

func TestStartSessionSendsTheBearerTokenAndTheDetectedCIFields(t *testing.T) {
	fs := newFakeServer(t)
	r := quietReporter(t, fs.URL)
	setEnv(t, map[string]string{
		"GITHUB_ACTIONS":    "true",
		"GITHUB_REPOSITORY": "acme/webapp",
		"GITHUB_EVENT_NAME": "pull_request",
		"GITHUB_REF":        "refs/pull/421/merge",
		"GITHUB_ACTOR":      "octocat",
		"GITHUB_SERVER_URL": "https://github.com",
		"GITHUB_RUN_ID":     "99",
	})
	ctx := context.Background()

	s, err := r.StartSession(ctx, SessionOptions{BuildNumber: "build-42", Branch: "main", CIActor: "explicit-actor"})
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	defer func() { _ = s.Close(ctx) }()

	if s.SessionID != "sess-0001-test-only" || s.RunID != "run-0001-test-only" {
		t.Errorf("session ids = (%q, %q), want the server's", s.SessionID, s.RunID)
	}
	reqs := fs.recorded()
	if len(reqs) != 1 || reqs[0].Method != http.MethodPost || reqs[0].Path != "/api/v1/stream/sessions" {
		t.Fatalf("requests = %+v, want one POST /api/v1/stream/sessions", reqs)
	}
	create := reqs[0]
	if create.Auth != "Bearer "+fakeToken {
		t.Errorf("Authorization = %q, want %q", create.Auth, "Bearer "+fakeToken)
	}
	want := map[string]interface{}{
		"project_id":   fakeProjectID,
		"build_number": "build-42",
		"branch":       "main",
		"framework":    "go",
		"ci_provider":  "github_actions",
		"ci_repo":      "acme/webapp",
		"pr_number":    float64(421),
		"ci_actor":     "explicit-actor",
		"ci_run_url":   "https://github.com/acme/webapp/actions/runs/99",
	}
	for key, value := range want {
		if create.Body[key] != value {
			t.Errorf("session-create %s = %#v, want %#v", key, create.Body[key], value)
		}
	}
}

func TestCloseFlushesTheBufferThenDeletesTheSession(t *testing.T) {
	fs := newFakeServer(t)
	r := quietReporter(t, fs.URL)
	ctx := context.Background()

	s, err := r.StartSession(ctx, SessionOptions{BuildNumber: "build-42"})
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	s.Record(ctx, "TestLogin", Passed, 120, RecordOptions{})
	s.Record(ctx, "TestCheckout", Failed, 340, RecordOptions{Error: "expected 200, got 500"})
	s.Record(ctx, "TestLogout", Skipped, 0, RecordOptions{})

	if got := len(fs.recorded()); got != 1 {
		t.Fatalf("%d requests before Close, want only the session create (nothing flushed yet)", got)
	}
	if err := s.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}

	reqs := fs.recorded()
	var order []string
	for _, req := range reqs {
		order = append(order, req.Method+" "+req.Path)
	}
	wantOrder := []string{
		"POST /api/v1/stream/sessions",
		"POST /api/v1/stream/events/batch",
		"DELETE /api/v1/stream/sessions/sess-0001-test-only",
	}
	if strings.Join(order, " | ") != strings.Join(wantOrder, " | ") {
		t.Fatalf("Close must flush the buffered events, then delete the session:\n got %v\nwant %v", order, wantOrder)
	}
	batch := reqs[1]
	if batch.Token != "st-test-only" {
		t.Errorf("batch X-Session-Token = %q, want the session token", batch.Token)
	}
	if batch.Body["session_id"] != "sess-0001-test-only" || batch.Body["run_id"] != "run-0001-test-only" {
		t.Errorf("batch ids = (%v, %v), want the session's", batch.Body["session_id"], batch.Body["run_id"])
	}
	events, _ := batch.Body["events"].([]interface{})
	var names []string
	for _, e := range events {
		ev, _ := e.(map[string]interface{})
		names = append(names, ev["test_name"].(string)+":"+ev["status"].(string))
	}
	if strings.Join(names, ",") != "TestLogin:PASSED,TestCheckout:FAILED,TestLogout:SKIPPED" {
		t.Errorf("batch events = %v, want the three records in order", names)
	}
	if reqs[2].Auth != "Bearer "+fakeToken {
		t.Errorf("DELETE Authorization = %q, want the bearer token", reqs[2].Auth)
	}
	if sent, failed := s.Stats(); sent != 3 || failed != 0 {
		t.Errorf("Stats() = (%d, %d), want (3, 0)", sent, failed)
	}
}

func TestCloseWithAnEmptyBufferOnlyDeletesTheSession(t *testing.T) {
	fs := newFakeServer(t)
	r := quietReporter(t, fs.URL)
	ctx := context.Background()

	s, err := r.StartSession(ctx, SessionOptions{})
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	if err := s.Close(ctx); err != nil {
		t.Fatalf("Close: %v", err)
	}
	reqs := fs.recorded()
	if len(reqs) != 2 || reqs[1].Method != http.MethodDelete {
		t.Fatalf("requests = %+v, want the create then one DELETE and no empty batch", reqs)
	}
}

func TestCloseReportsARefusedSessionDelete(t *testing.T) {
	fs := newFakeServer(t)
	r := quietReporter(t, fs.URL)
	ctx := context.Background()

	s, err := r.StartSession(ctx, SessionOptions{})
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	fs.mu.Lock()
	fs.deleteErr = http.StatusForbidden
	fs.mu.Unlock()

	err = s.Close(ctx)
	if err == nil || !strings.Contains(err.Error(), "403") {
		t.Fatalf("Close() error = %v, want an HTTP 403 error", err)
	}
}

func TestLoginPostsFormEncodedCredentialsAndReturnsTheToken(t *testing.T) {
	var got struct {
		method, path, contentType string
		form                      url.Values
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		got.method, got.path, got.contentType = r.Method, r.URL.Path, r.Header.Get("Content-Type")
		got.form, _ = url.ParseQuery(string(raw))
		if got.form.Get("username") == "dana" && got.form.Get("password") == "ci-password-test-only" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"access_token":"` + fakeToken + `","token_type":"bearer"}`))
			return
		}
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"Incorrect username or password"}`))
	}))
	defer server.Close()
	ctx := context.Background()

	token, err := Login(ctx, server.URL+"/", "dana", "ci-password-test-only")
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if token != fakeToken {
		t.Errorf("token = %q, want %q", token, fakeToken)
	}
	if got.method != http.MethodPost || got.path != "/api/v1/auth/login" {
		t.Errorf("request = %s %s, want POST /api/v1/auth/login", got.method, got.path)
	}
	if got.contentType != "application/x-www-form-urlencoded" {
		t.Errorf("Content-Type = %q, want form-encoded (the endpoint is OAuth2PasswordRequestForm)", got.contentType)
	}

	token, err = Login(ctx, server.URL, "dana", "wrong-password-test-only")
	if err == nil || token != "" {
		t.Fatalf("Login with a wrong password = (%q, %v), want an error and no token", token, err)
	}
	if !strings.Contains(err.Error(), "401") {
		t.Errorf("error = %v, want it to name the 401", err)
	}
}
