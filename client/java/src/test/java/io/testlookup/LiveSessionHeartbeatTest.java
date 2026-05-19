package io.testlookup;

import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.time.Instant;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests the {@code LiveSession.heartbeatTick} contract: a heartbeat
 * event is enqueued only when the session has been silent for at least
 * {@code HEARTBEAT_INTERVAL_MS}. Mirrors the Python SDK behaviour so
 * both clients keep the server-side {@code last_event_at} fresh during
 * long inter-test gaps without spamming healthy streams.
 *
 * <p>We use reflection to reach the package-private fields rather than
 * widening the API surface for testing alone.
 */
public class LiveSessionHeartbeatTest {

    private static TestLookupReporter.LiveSession newSession() {
        TestLookupReporter reporter = new TestLookupReporter.Builder()
                .baseUrl("http://test.invalid")
                .apiKey("tlk_test_key")
                .projectId("00000000-0000-0000-0000-000000000001")
                .clientName("unit-test")
                .batchSize(50)
                .batchIntervalMs(60_000)  // slow so the auto-flush doesn't fight us
                .build();
        return new TestLookupReporter.LiveSession(
                "sess-id-test-abc12345",
                "session-token-test",
                "run-test",
                "test-suite",
                reporter
        );
    }

    @SuppressWarnings("unchecked")
    private static BlockingQueue<ObjectNode> queueOf(TestLookupReporter.LiveSession s) throws Exception {
        Field f = TestLookupReporter.LiveSession.class.getDeclaredField("queue");
        f.setAccessible(true);
        return (BlockingQueue<ObjectNode>) f.get(s);
    }

    private static AtomicLong lastRealMsOf(TestLookupReporter.LiveSession s) throws Exception {
        Field f = TestLookupReporter.LiveSession.class.getDeclaredField("lastRealEnqueueMs");
        f.setAccessible(true);
        return (AtomicLong) f.get(s);
    }

    @Test
    void heartbeat_fires_when_session_has_been_idle() throws Exception {
        TestLookupReporter.LiveSession session = newSession();
        try {
            // Pretend the last real event was 10 minutes ago.
            lastRealMsOf(session).set(Instant.now().toEpochMilli() - 600_000L);
            BlockingQueue<ObjectNode> q = queueOf(session);
            q.clear();

            session.heartbeatTick();

            // Drain the queue and assert a heartbeat is on it.
            boolean found = false;
            for (ObjectNode evt : q) {
                if ("live_heartbeat".equals(evt.path("event_type").asText())) {
                    found = true;
                    assertTrue(evt.has("timestamp_ms"), "heartbeat must carry timestamp_ms");
                    break;
                }
            }
            assertTrue(found, "expected a live_heartbeat event on the queue");
        } finally {
            // Drain before close so flushAll() doesn't burn ~7s retrying
            // POSTs to the dummy URL on the way out.
            try { queueOf(session).clear(); } catch (Exception ignored) {}
            session.close();
        }
    }

    @Test
    void heartbeat_suppressed_while_session_is_busy() throws Exception {
        TestLookupReporter.LiveSession session = newSession();
        try {
            // Real event just enqueued — heartbeat must NOT fire.
            lastRealMsOf(session).set(Instant.now().toEpochMilli());
            BlockingQueue<ObjectNode> q = queueOf(session);
            q.clear();

            session.heartbeatTick();

            // No heartbeats expected on the queue.
            for (ObjectNode evt : q) {
                assertNotEquals("live_heartbeat", evt.path("event_type").asText(),
                        "heartbeat must be suppressed when session is busy");
            }
        } finally {
            // Drain before close so flushAll() doesn't burn ~7s retrying
            // POSTs to the dummy URL on the way out.
            try { queueOf(session).clear(); } catch (Exception ignored) {}
            session.close();
        }
    }

    @Test
    void heartbeat_does_not_update_busy_marker() throws Exception {
        TestLookupReporter.LiveSession session = newSession();
        try {
            // Idle session — heartbeat WILL fire.
            long before = Instant.now().toEpochMilli() - 600_000L;
            lastRealMsOf(session).set(before);
            queueOf(session).clear();

            session.heartbeatTick();

            // The "last real" marker is for real activity only — emitting a
            // heartbeat must not touch it, otherwise the next tick would
            // suppress itself forever and we'd be back to the bug.
            assertEquals(before, lastRealMsOf(session).get(),
                    "heartbeat must NOT touch lastRealEnqueueMs");
        } finally {
            // Drain before close so flushAll() doesn't burn ~7s retrying
            // POSTs to the dummy URL on the way out.
            try { queueOf(session).clear(); } catch (Exception ignored) {}
            session.close();
        }
    }
}
