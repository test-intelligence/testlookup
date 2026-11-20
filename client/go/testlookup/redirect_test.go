package testlookup

// The rule: the SDK follows a redirect only when the target has the same
// scheme, host and port as the configured BaseURL. A redirect anywhere else is
// an error that names the other host and says to configure the final URL, and
// no request (so no token, session token or login form) is sent there.
//
// Refusal cases: session create, the event batch, and Login, each redirected
// to a second server that must never be called; https to http on the same
// host. Positive cases: a same-origin path redirect still works for a POST
// with its body, and a direct answer is unaffected (every other test here).

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// otherHost is the server a hostile or misconfigured redirect points at. It
// counts every request it receives; the tests want zero.
func otherHost(t *testing.T) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var hits atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"access_token":"stolen","session_id":"x","session_token":"x","run_id":"x","accepted":1}`))
	}))
	t.Cleanup(server.Close)
	return server, &hits
}

func wantRefusal(t *testing.T, err error, other *httptest.Server) {
	t.Helper()
	if err == nil {
		t.Fatal("a redirect to another host must be an error")
	}
	otherURL, _ := url.Parse(other.URL)
	if !strings.Contains(err.Error(), otherURL.Host) {
		t.Errorf("error = %v, want it to name the redirect's host %s", err, otherURL.Host)
	}
	if !strings.Contains(err.Error(), "final URL") {
		t.Errorf("error = %v, want it to say to configure the final URL", err)
	}
}

func TestSessionCreateRedirectedToAnotherHostIsRefused(t *testing.T) {
	other, hits := otherHost(t)
	for _, status := range []int{http.StatusMovedPermanently, http.StatusTemporaryRedirect, http.StatusPermanentRedirect} {
		redirecting := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, other.URL+r.URL.Path, status)
		}))
		r := quietReporter(t, redirecting.URL)
		_, err := r.StartSession(context.Background(), SessionOptions{BuildNumber: "build-42"})
		redirecting.Close()
		wantRefusal(t, err, other)
	}
	if got := hits.Load(); got != 0 {
		t.Fatalf("the other host received %d request(s); it must receive none", got)
	}
}

func TestBatchRedirectedToAnotherHostIsRefusedWithoutRetries(t *testing.T) {
	other, hits := otherHost(t)
	var batchCalls atomic.Int64
	first := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/stream/sessions":
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"session_id":"sess-0001-test-only","session_token":"st-test-only","run_id":"run-0001-test-only"}`))
		case r.URL.Path == "/api/v1/stream/events/batch":
			batchCalls.Add(1)
			http.Redirect(w, r, other.URL+r.URL.Path, http.StatusTemporaryRedirect)
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	defer first.Close()
	r := quietReporter(t, first.URL)
	ctx := context.Background()
	s, err := r.StartSession(ctx, SessionOptions{})
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	s.Record(ctx, "TestLogin", Passed, 120, RecordOptions{})
	started := time.Now()
	_ = s.Close(ctx)

	if got := hits.Load(); got != 0 {
		t.Fatalf("the other host received %d request(s) carrying X-Session-Token; it must receive none", got)
	}
	if sent, failed := s.Stats(); sent != 0 || failed != 1 {
		t.Errorf("Stats() = (%d sent, %d failed), want (0, 1)", sent, failed)
	}
	// A refused redirect will be refused again: no back-off loop (5 attempts would take 7.5 s).
	if got := batchCalls.Load(); got != 1 {
		t.Errorf("the batch was posted %d times, want 1 (a refused redirect is not retried)", got)
	}
	if took := time.Since(started); took > 3*time.Second {
		t.Errorf("Close took %s; a refused redirect must fail at once", took)
	}
}

func TestLoginRedirectedToAnotherHostIsRefused(t *testing.T) {
	other, hits := otherHost(t)
	redirecting := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, other.URL+r.URL.Path, http.StatusTemporaryRedirect)
	}))
	defer redirecting.Close()

	token, err := Login(context.Background(), redirecting.URL, "dana", "ci-password-test-only")
	wantRefusal(t, err, other)
	if token != "" {
		t.Errorf("token = %q, want none", token)
	}
	if got := hits.Load(); got != 0 {
		t.Fatalf("the other host received %d request(s) carrying the login form; it must receive none", got)
	}
}

func TestLoginUsesAClientWithATimeout(t *testing.T) {
	if loginClient.Timeout != defaultRequestTimeout {
		t.Errorf("Login's client timeout = %s, want %s", loginClient.Timeout, defaultRequestTimeout)
	}
	if loginClient.CheckRedirect == nil {
		t.Error("Login's client has no redirect policy")
	}
}

func TestARedirectOnTheSameOriginIsStillFollowed(t *testing.T) {
	var posted string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/stream/sessions":
			// What a proxy or framework does for a missing trailing slash.
			http.Redirect(w, r, "/api/v1/stream/sessions/", http.StatusTemporaryRedirect)
		case "/api/v1/stream/sessions/":
			raw, _ := io.ReadAll(r.Body)
			posted = r.Method + " " + r.Header.Get("Authorization") + " " + string(raw)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"session_id":"sess-0001-test-only","session_token":"st-test-only","run_id":"run-0001-test-only"}`))
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	defer server.Close()
	r := quietReporter(t, server.URL)
	s, err := r.StartSession(context.Background(), SessionOptions{BuildNumber: "build-42"})
	if err != nil {
		t.Fatalf("StartSession through a same-origin redirect: %v", err)
	}
	if s.SessionID != "sess-0001-test-only" {
		t.Errorf("SessionID = %q", s.SessionID)
	}
	if !strings.HasPrefix(posted, "POST Bearer "+fakeToken+" ") || !strings.Contains(posted, `"build_number":"build-42"`) {
		t.Errorf("the redirected request = %q, want the same POST, token and body", posted)
	}
	_ = s.Close(context.Background())
}

func TestSameOriginComparesSchemeHostAndPort(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"https://tl.example.test/api", "https://tl.example.test/api/", true},
		{"https://tl.example.test", "https://TL.Example.Test:443/x", true},
		{"http://tl.example.test", "http://tl.example.test:80/x", true},
		{"https://tl.example.test", "http://tl.example.test/x", false}, // never a downgrade
		{"http://tl.example.test", "https://tl.example.test/x", false}, // nor an upgrade: configure the final URL
		{"https://tl.example.test", "https://tl.example.test:8443/x", false},
		{"https://tl.example.test", "https://other.example.test/x", false},
		{"https://tl.example.test", "https://tl.example.test.evil.test/x", false},
		{"http://127.0.0.1:8000", "http://127.0.0.1:8001/x", false},
	}
	for _, c := range cases {
		a, _ := url.Parse(c.a)
		b, _ := url.Parse(c.b)
		if got := sameOrigin(a, b); got != c.want {
			t.Errorf("sameOrigin(%s, %s) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

func TestACallerSuppliedClientGetsThePolicyUnlessItHasItsOwn(t *testing.T) {
	setEnv(t, nil)
	mine := &http.Client{Timeout: 5 * time.Second}
	r, err := New(Config{BaseURL: "http://testlookup.example.test", Token: fakeToken, ProjectID: fakeProjectID, HTTPClient: mine})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if r.cfg.HTTPClient.CheckRedirect == nil {
		t.Error("a caller's client without a redirect policy must get the same-origin policy")
	}
	if mine.CheckRedirect != nil {
		t.Error("the caller's own client value must not be changed (the SDK works on a copy)")
	}
	if r.cfg.HTTPClient.Timeout != 5*time.Second {
		t.Errorf("the copy lost the caller's timeout: %s", r.cfg.HTTPClient.Timeout)
	}

	called := false
	own := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { called = true; return http.ErrUseLastResponse }}
	r, err = New(Config{BaseURL: "http://testlookup.example.test", Token: fakeToken, ProjectID: fakeProjectID, HTTPClient: own})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	_ = r.cfg.HTTPClient.CheckRedirect(nil, nil)
	if !called {
		t.Error("a caller's own redirect policy must be kept")
	}
}
