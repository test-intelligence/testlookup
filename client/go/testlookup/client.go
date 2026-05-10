// Package testlookup provides a Go client for streaming test execution events
// to a TestLookup server in real-time.
//
// # Quick start
//
//	reporter, err := testlookup.New(testlookup.Config{
//	    BaseURL:   "http://localhost:8000",
//	    Token:     "<jwt>",
//	    ProjectID: "<uuid>",
//	})
//	if err != nil {
//	    log.Fatal(err)
//	}
//
//	session, err := reporter.StartSession(ctx, testlookup.SessionOptions{
//	    BuildNumber: "build-42",
//	    Branch:      "main",
//	})
//	if err != nil {
//	    log.Fatal(err)
//	}
//	defer session.Close(ctx)
//
//	session.Record(ctx, "TestLogin",    testlookup.Passed, 120, testlookup.RecordOptions{})
//	session.Record(ctx, "TestCheckout", testlookup.Failed, 340, testlookup.RecordOptions{
//	    Error: "assertion failed: expected status 200, got 500",
//	})
//
// # Login helper
//
//	token, err := testlookup.Login(ctx, "http://localhost:8000", "admin", "secret")
//
// # Environment variable fallback
//
// If Config fields are empty, the client reads from environment variables:
//
//	TESTLOOKUP_URL          - server base URL
//	TESTLOOKUP_TOKEN        - JWT access token
//	TESTLOOKUP_PROJECT_ID   - target project UUID
//	TESTLOOKUP_BUILD        - build identifier
//	TESTLOOKUP_BRANCH       - git branch
//	TESTLOOKUP_COMMIT       - git commit SHA
package testlookup

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ── Constants ─────────────────────────────────────────────────────────────────

const (
	defaultBatchSize        = 50
	maxBatchSize            = 1_000
	defaultBatchInterval    = 100 * time.Millisecond
	defaultConnectTimeout   = 10 * time.Second
	defaultRequestTimeout   = 30 * time.Second
	maxRetries              = 5
	retryBaseDelay          = 500 * time.Millisecond
	maxQueueSize            = 50_000
)

// ── Status constants ──────────────────────────────────────────────────────────

type TestStatus string

const (
	Passed  TestStatus = "PASSED"
	Failed  TestStatus = "FAILED"
	Skipped TestStatus = "SKIPPED"
	Broken  TestStatus = "BROKEN"
)

// ── Config ────────────────────────────────────────────────────────────────────

// Config holds reporter configuration. Empty string fields fall back to
// the corresponding environment variable (see package doc).
type Config struct {
	// BaseURL is the TestLookup server URL, e.g. "http://localhost:8000".
	BaseURL string
	// Token is the JWT access token from /api/v1/auth/login.
	Token string
	// ProjectID is the target project UUID.
	ProjectID string
	// ClientName is a human-readable label for this machine (default: os.Hostname()).
	ClientName string
	// Framework is the test framework name (default: "go").
	Framework string
	// BatchSize is the number of events per flush (default: 50, max: 1000).
	BatchSize int
	// BatchInterval is the time between periodic flushes (default: 100ms).
	BatchInterval time.Duration
	// HTTPClient is a custom HTTP client (optional). If nil, a default is used.
	HTTPClient *http.Client
}

func (c *Config) withDefaults() Config {
	out := *c
	if out.BaseURL == ""    { out.BaseURL    = env("TESTLOOKUP_URL", "") }
	if out.Token == ""      { out.Token      = env("TESTLOOKUP_TOKEN", "") }
	if out.ProjectID == ""  { out.ProjectID  = env("TESTLOOKUP_PROJECT_ID", "") }
	if out.ClientName == "" { out.ClientName, _ = os.Hostname() }
	if out.Framework == ""  { out.Framework  = "go" }
	if out.BatchSize <= 0   { out.BatchSize  = defaultBatchSize }
	if out.BatchSize > maxBatchSize { out.BatchSize = maxBatchSize }
	if out.BatchInterval <= 0 { out.BatchInterval = defaultBatchInterval }
	if out.HTTPClient == nil {
		out.HTTPClient = &http.Client{
			Timeout: defaultRequestTimeout,
			Transport: &http.Transport{
				ResponseHeaderTimeout: defaultRequestTimeout,
				TLSHandshakeTimeout:   defaultConnectTimeout,
			},
		}
	}
	out.BaseURL = strings.TrimRight(out.BaseURL, "/")
	return out
}

// ── SessionOptions ────────────────────────────────────────────────────────────

type SessionOptions struct {
	BuildNumber string
	RunID       string
	Branch      string
	CommitHash  string
	MachineID   string
	TotalTests  int // 0 = unknown
	// LaunchName is the human-readable launch label (analogous to ReportPortal's rp.launch).
	// When empty the server falls back to BuildNumber for display.
	LaunchName string
	Metadata   map[string]interface{}
}

// ── RecordOptions ─────────────────────────────────────────────────────────────

type RecordOptions struct {
	SuiteName  string
	ClassName  string
	Error      string
	StackTrace string
	Tags       []string
	Metadata   map[string]interface{}
}

// ── Reporter ──────────────────────────────────────────────────────────────────

// Reporter creates and manages live execution sessions.
type Reporter struct {
	cfg Config
}

// New creates a new Reporter. Returns an error if required fields are missing.
func New(cfg Config) (*Reporter, error) {
	c := cfg.withDefaults()
	if c.BaseURL == "" || c.Token == "" || c.ProjectID == "" {
		return nil, fmt.Errorf("testlookup: BaseURL, Token, and ProjectID are required")
	}
	return &Reporter{cfg: c}, nil
}

// StartSession registers a new live session with the server and returns
// a *Session ready for recording events.
func (r *Reporter) StartSession(ctx context.Context, opts SessionOptions) (*Session, error) {
	payload := map[string]interface{}{
		"project_id":  r.cfg.ProjectID,
		"client_name": r.cfg.ClientName,
		"framework":   r.cfg.Framework,
		"machine_id":  coalesce(opts.MachineID, r.cfg.ClientName),
	}
	if opts.BuildNumber != "" { payload["build_number"] = opts.BuildNumber }
	if opts.RunID       != "" { payload["run_id"]       = opts.RunID }
	if opts.Branch      != "" { payload["branch"]       = opts.Branch }
	if opts.CommitHash  != "" { payload["commit_hash"]  = opts.CommitHash }
	if opts.TotalTests   > 0  { payload["total_tests"]  = opts.TotalTests }
	if opts.LaunchName  != "" { payload["launch_name"]  = opts.LaunchName }
	if opts.Metadata    != nil { payload["metadata"]    = opts.Metadata }

	var result struct {
		SessionID    string `json:"session_id"`
		SessionToken string `json:"session_token"`
		RunID        string `json:"run_id"`
	}
	if err := r.jsonRequest(ctx, http.MethodPost, "/api/v1/stream/sessions", payload, &result); err != nil {
		return nil, fmt.Errorf("testlookup: start session: %w", err)
	}

	s := &Session{
		SessionID:    result.SessionID,
		RunID:        result.RunID,
		sessionToken: result.SessionToken,
		reporter:     r,
		buffer:       make([]liveEvent, 0, r.cfg.BatchSize),
		stopCh:       make(chan struct{}),
	}

	s.wg.Add(1)
	go s.flushLoop()

	return s, nil
}

// CloseSession marks a session as complete on the server, triggering the AI
// analysis pipeline. Called automatically by Session.Close().
func (r *Reporter) CloseSession(ctx context.Context, sessionID string) error {
	req, err := http.NewRequestWithContext(
		ctx, http.MethodDelete,
		r.cfg.BaseURL+"/api/v1/stream/sessions/"+sessionID, nil,
	)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+r.cfg.Token)
	resp, err := r.cfg.HTTPClient.Do(req)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return nil
}

// Login obtains a JWT access token via username/password.
//
//	token, err := testlookup.Login(ctx, "http://localhost:8000", "admin", "secret")
func Login(ctx context.Context, baseURL, username, password string) (string, error) {
	form := url.Values{"username": {username}, "password": {password}}
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost,
		strings.TrimRight(baseURL, "/")+"/api/v1/auth/login",
		strings.NewReader(form.Encode()),
	)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("testlookup: login request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("testlookup: login failed (%d): %s", resp.StatusCode, body)
	}

	var result struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("testlookup: login decode: %w", err)
	}
	return result.AccessToken, nil
}

// ── Session ───────────────────────────────────────────────────────────────────

// Session represents an active test execution run.
// All methods are safe for concurrent use from multiple goroutines.
type Session struct {
	// SessionID is the server-assigned session UUID.
	SessionID string
	// RunID is the server-assigned run UUID (used in event batches).
	RunID string

	sessionToken string
	reporter     *Reporter

	mu     sync.Mutex
	buffer []liveEvent

	stopCh chan struct{}
	wg     sync.WaitGroup

	statSent   atomic.Int64
	statFailed atomic.Int64
}

// Record records a single test result.
//
//	session.Record(ctx, "TestLogin", testlookup.Passed, 120, testlookup.RecordOptions{})
//	session.Record(ctx, "TestCheckout", testlookup.Failed, 340, testlookup.RecordOptions{
//	    Error: "expected 200, got 500",
//	    SuiteName: "checkout_suite",
//	})
func (s *Session) Record(ctx context.Context, testName string, status TestStatus, durationMs int64, opts RecordOptions) {
	event := liveEvent{
		EventType:   "test_result",
		TestName:    testName,
		Status:      string(status),
		DurationMs:  durationMs,
		TimestampMs: time.Now().UnixMilli(),
	}
	if opts.SuiteName  != "" { event.SuiteName    = opts.SuiteName }
	if opts.ClassName  != "" { event.ClassName    = opts.ClassName }
	if opts.Error      != "" { event.ErrorMessage = opts.Error }
	if opts.StackTrace != "" { event.StackTrace   = opts.StackTrace }
	if len(opts.Tags)   > 0  { event.Tags         = opts.Tags }
	if opts.Metadata   != nil { event.Metadata    = opts.Metadata }

	s.enqueue(ctx, event)
}

// Log records a log message (not a test result).
func (s *Session) Log(ctx context.Context, message, level string) {
	if level == "" {
		level = "INFO"
	}
	s.enqueue(ctx, liveEvent{
		EventType:   "log",
		TimestampMs: time.Now().UnixMilli(),
		Metadata:    map[string]interface{}{"level": level, "message": message},
	})
}

// Metric records a numeric metric (e.g. memory usage, response time).
func (s *Session) Metric(ctx context.Context, name string, value float64, unit string) {
	s.enqueue(ctx, liveEvent{
		EventType:   "metric",
		TestName:    name,
		DurationMs:  int64(value),
		TimestampMs: time.Now().UnixMilli(),
		Metadata:    map[string]interface{}{"value": value, "unit": unit},
	})
}

// Stats returns the number of successfully sent and failed events.
func (s *Session) Stats() (sent, failed int64) {
	return s.statSent.Load(), s.statFailed.Load()
}

// Close flushes remaining events, stops the background flusher, and closes
// the session on the server. Always call Close (or defer it) after recording.
func (s *Session) Close(ctx context.Context) error {
	// Signal background flusher to stop and wait for it
	close(s.stopCh)
	s.wg.Wait()

	// Final flush of whatever is still in the buffer
	s.mu.Lock()
	remaining := make([]liveEvent, len(s.buffer))
	copy(remaining, s.buffer)
	s.buffer = s.buffer[:0]
	s.mu.Unlock()

	for len(remaining) > 0 {
		n := maxBatchSize
		if n > len(remaining) {
			n = len(remaining)
		}
		s.postBatch(ctx, remaining[:n])
		remaining = remaining[n:]
	}

	sent, failed := s.Stats()
	_ = fmt.Sprintf("testlookup: session %s closed: sent=%d failed=%d", s.SessionID[:8], sent, failed)

	return s.reporter.CloseSession(ctx, s.SessionID)
}

// ── Internal ──────────────────────────────────────────────────────────────────

func (s *Session) enqueue(ctx context.Context, event liveEvent) {
	s.mu.Lock()
	s.buffer = append(s.buffer, event)
	overflow := len(s.buffer) >= s.reporter.cfg.BatchSize
	s.mu.Unlock()

	if overflow {
		// Trigger an eager flush without blocking the caller
		go func() {
			s.flushOnce(ctx)
		}()
	}
}

func (s *Session) flushLoop() {
	defer s.wg.Done()
	ticker := time.NewTicker(s.reporter.cfg.BatchInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			s.flushOnce(context.Background())
		case <-s.stopCh:
			return
		}
	}
}

func (s *Session) flushOnce(ctx context.Context) {
	s.mu.Lock()
	if len(s.buffer) == 0 {
		s.mu.Unlock()
		return
	}
	n := s.reporter.cfg.BatchSize
	if n > len(s.buffer) {
		n = len(s.buffer)
	}
	batch := make([]liveEvent, n)
	copy(batch, s.buffer[:n])
	s.buffer = s.buffer[n:]
	s.mu.Unlock()

	s.postBatch(ctx, batch)
}

func (s *Session) postBatch(ctx context.Context, events []liveEvent) {
	payload := batchPayload{
		SessionID: s.SessionID,
		RunID:     s.RunID,
		Events:    events,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		s.statFailed.Add(int64(len(events)))
		return
	}

	for attempt := 1; attempt <= maxRetries; attempt++ {
		req, err := http.NewRequestWithContext(
			ctx, http.MethodPost,
			s.reporter.cfg.BaseURL+"/api/v1/stream/events/batch",
			bytes.NewReader(body),
		)
		if err != nil {
			s.statFailed.Add(int64(len(events)))
			return
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Session-Token", s.sessionToken)

		resp, err := s.reporter.cfg.HTTPClient.Do(req)
		if err == nil {
			defer resp.Body.Close()
			if resp.StatusCode == http.StatusUnauthorized {
				io.Copy(io.Discard, resp.Body)
				s.statFailed.Add(int64(len(events)))
				return
			}
			if resp.StatusCode == http.StatusAccepted || resp.StatusCode == http.StatusOK {
				var result struct{ Accepted int `json:"accepted"` }
				if json.NewDecoder(resp.Body).Decode(&result) == nil && result.Accepted > 0 {
					s.statSent.Add(int64(result.Accepted))
				} else {
					s.statSent.Add(int64(len(events)))
				}
				return
			}
			io.Copy(io.Discard, resp.Body)
		}

		delay := time.Duration(float64(retryBaseDelay) * math.Pow(2, float64(attempt-1)))
		if attempt == maxRetries {
			s.statFailed.Add(int64(len(events)))
			return
		}
		select {
		case <-time.After(delay):
		case <-ctx.Done():
			s.statFailed.Add(int64(len(events)))
			return
		}
	}
}

// ── Reporter helpers ──────────────────────────────────────────────────────────

func (r *Reporter) jsonRequest(ctx context.Context, method, path string, payload interface{}, result interface{}) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, method, r.cfg.BaseURL+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.cfg.Token)

	resp, err := r.cfg.HTTPClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, b)
	}

	if result != nil {
		return json.NewDecoder(resp.Body).Decode(result)
	}
	return nil
}

// ── Wire types ────────────────────────────────────────────────────────────────

type liveEvent struct {
	EventType    string                 `json:"event_type"`
	TestName     string                 `json:"test_name,omitempty"`
	Status       string                 `json:"status,omitempty"`
	DurationMs   int64                  `json:"duration_ms,omitempty"`
	TimestampMs  int64                  `json:"timestamp_ms"`
	SuiteName    string                 `json:"suite_name,omitempty"`
	ClassName    string                 `json:"class_name,omitempty"`
	ErrorMessage string                 `json:"error_message,omitempty"`
	StackTrace   string                 `json:"stack_trace,omitempty"`
	Tags         []string               `json:"tags,omitempty"`
	Metadata     map[string]interface{} `json:"metadata,omitempty"`
}

type batchPayload struct {
	SessionID string      `json:"session_id"`
	RunID     string      `json:"run_id"`
	Events    []liveEvent `json:"events"`
}

// ── Utilities ─────────────────────────────────────────────────────────────────

func env(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

func coalesce(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
