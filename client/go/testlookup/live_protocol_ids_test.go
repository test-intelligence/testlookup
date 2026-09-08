package testlookup

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
)

func TestPostBatchRetryReusesBatchIdentityAndEventOrder(t *testing.T) {
	var payloads []map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read request: %v", err)
		}
		var payload map[string]interface{}
		if err := json.Unmarshal(body, &payload); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		payloads = append(payloads, payload)
		w.Header().Set("Content-Type", "application/json")
		if len(payloads) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte(`{"accepted":1}`))
	}))
	defer server.Close()

	reporter := &Reporter{cfg: Config{BaseURL: server.URL, HTTPClient: server.Client()}}
	session := &Session{
		SessionID:    "session-1",
		RunID:        "run-1",
		sessionToken: "token-1",
		reporter:     reporter,
	}
	session.postBatch(context.Background(), []liveEvent{{EventType: "test_result"}})

	if len(payloads) != 2 {
		t.Fatalf("request count = %d, want 2", len(payloads))
	}
	if !reflect.DeepEqual(payloads[0], payloads[1]) {
		t.Fatalf("retry payload changed:\nfirst: %#v\nretry: %#v", payloads[0], payloads[1])
	}
	if payloads[0]["batch_id"] == "" {
		t.Fatal("batch_id was not serialized")
	}
}
