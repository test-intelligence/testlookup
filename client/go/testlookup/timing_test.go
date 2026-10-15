package testlookup

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

// EXEC-01: a result carries when the test began (started_at_ms) next to
// timestamp_ms, its finish. A result recorded without a start sends exactly
// the keys it always did.

func bufferedSession() *Session {
	return &Session{
		SessionID: "session-1",
		RunID:     "run-1",
		reporter:  &Reporter{cfg: Config{BatchSize: 1000}},
	}
}

func lastEvent(t *testing.T, s *Session) map[string]interface{} {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.buffer) == 0 {
		t.Fatal("nothing was recorded")
	}
	raw, err := json.Marshal(s.buffer[len(s.buffer)-1])
	if err != nil {
		t.Fatalf("encode event: %v", err)
	}
	var event map[string]interface{}
	if err := json.Unmarshal(raw, &event); err != nil {
		t.Fatalf("decode event: %v", err)
	}
	return event
}

func startedAt(t *testing.T, event map[string]interface{}) int64 {
	t.Helper()
	value, ok := event["started_at_ms"].(float64)
	if !ok {
		t.Fatalf("started_at_ms missing from %v", event)
	}
	return int64(value)
}

func TestRecordSendsTheStartItIsGiven(t *testing.T) {
	s := bufferedSession()
	started := time.UnixMilli(1_791_972_001_250)
	s.Record(context.Background(), "TestCheckout", Passed, 1800, RecordOptions{StartedAt: started})

	event := lastEvent(t, s)
	if got := startedAt(t, event); got != 1_791_972_001_250 {
		t.Fatalf("started_at_ms = %d, want 1791972001250", got)
	}
	if _, ok := event["timestamp_ms"]; !ok {
		t.Fatal("timestamp_ms (the finish) is missing")
	}
}

func TestRecordWithoutAStartSendsNoStart(t *testing.T) {
	s := bufferedSession()
	s.Record(context.Background(), "TestCheckout", Passed, 5, RecordOptions{})

	if _, ok := lastEvent(t, s)["started_at_ms"]; ok {
		t.Fatal("started_at_ms was invented for a result recorded without a start")
	}
}

func TestRecordTestSendsTheTestsStart(t *testing.T) {
	s := bufferedSession()
	tr := NewTestReporter(s, "pkg/cart")
	start := time.Now().Add(-40 * time.Millisecond)
	tr.RecordTest(t, "TestCart", start)

	event := lastEvent(t, s)
	if got := startedAt(t, event); got != start.UnixMilli() {
		t.Fatalf("started_at_ms = %d, want %d", got, start.UnixMilli())
	}
	if finished := int64(event["timestamp_ms"].(float64)); finished < start.UnixMilli() {
		t.Fatalf("timestamp_ms %d is before the start %d", finished, start.UnixMilli())
	}
}
