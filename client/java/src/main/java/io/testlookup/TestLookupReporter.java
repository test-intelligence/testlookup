package io.testlookup;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.ArrayNode;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.logging.Logger;

/**
 * TestLookup Reporter — Java Client SDK
 * ========================================
 * Streams test execution events to a TestLookup server in real-time.
 * <p>
 * Designed for high-throughput concurrent test execution:
 * <ul>
 *   <li>Creates a lightweight session (one REST call at startup)</li>
 *   <li>Batches events and flushes every BATCH_INTERVAL_MS or every BATCH_SIZE events</li>
 *   <li>Uses LinkedBlockingQueue for thread-safe, non-blocking accumulation</li>
 *   <li>Retries failed flushes with exponential back-off</li>
 *   <li>Thread-safe — safe to call from parallel test runners</li>
 * </ul>
 *
 * <h3>Quick start</h3>
 * <pre>{@code
 * TestLookupReporter reporter = new TestLookupReporter.Builder()
 *     .baseUrl("http://localhost:8000")
 *     .token("<jwt>")
 *     .projectId("<uuid>")
 *     .build();
 *
 * try (LiveSession session = reporter.startSession(
 *         SessionOptions.builder().buildNumber("build-42").build())) {
 *
 *     session.record("login test",    TestStatus.PASSED, 120);
 *     session.record("checkout test", TestStatus.FAILED, 340,
 *         RecordOptions.builder().error("AssertionError: expected 200").build());
 * }
 * // session.close() is called automatically; triggers AI analysis pipeline
 * }</pre>
 *
 * <h3>Login helper</h3>
 * <pre>{@code
 * String token = TestLookupReporter.login("http://localhost:8000", "admin", "secret");
 * }</pre>
 */
public class TestLookupReporter {

    private static final Logger LOG = Logger.getLogger(TestLookupReporter.class.getName());

    private static final int BATCH_SIZE          = 50;
    private static final int MAX_BATCH_SIZE      = 1_000;
    private static final int BATCH_INTERVAL_MS   = 100;
    private static final int MAX_RETRIES         = 5;
    private static final long RETRY_BASE_DELAY_MS = 500L;
    private static final int CONNECT_TIMEOUT_SEC = 10;
    private static final int REQUEST_TIMEOUT_SEC = 30;

    // ── Builder ──────────────────────────────────────────────────────────────

    private final String     baseUrl;
    private final String     token;
    private final String     projectId;
    private final String     clientName;
    private final String     framework;
    private final int        batchSize;
    private final int        batchIntervalMs;
    private final HttpClient http;

    static final ObjectMapper MAPPER = new ObjectMapper();

    private TestLookupReporter(Builder b) {
        this.baseUrl        = b.baseUrl.replaceAll("/$", "");
        this.token          = b.token;
        this.projectId      = b.projectId;
        this.clientName     = b.clientName != null ? b.clientName : getHostname();
        this.framework      = b.framework  != null ? b.framework  : "java";
        this.batchSize      = Math.min(b.batchSize > 0 ? b.batchSize : BATCH_SIZE, MAX_BATCH_SIZE);
        this.batchIntervalMs = b.batchIntervalMs > 0 ? b.batchIntervalMs : BATCH_INTERVAL_MS;
        this.http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(CONNECT_TIMEOUT_SEC))
            .version(HttpClient.Version.HTTP_1_1)
            .build();
    }

    public static class Builder {
        private String baseUrl;
        private String token;
        private String projectId;
        private String clientName;
        private String framework;
        private int    batchSize;
        private int    batchIntervalMs;

        public Builder baseUrl(String v)         { this.baseUrl        = v; return this; }
        public Builder token(String v)           { this.token          = v; return this; }
        public Builder projectId(String v)       { this.projectId      = v; return this; }
        public Builder clientName(String v)      { this.clientName     = v; return this; }
        public Builder framework(String v)       { this.framework      = v; return this; }
        public Builder batchSize(int v)          { this.batchSize      = v; return this; }
        public Builder batchIntervalMs(int v)    { this.batchIntervalMs = v; return this; }
        public TestLookupReporter build()         { return new TestLookupReporter(this); }
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /**
     * Register a new live session with the server.
     *
     * @return LiveSession — call {@code close()} (or use try-with-resources) when done.
     */
    public LiveSession startSession(SessionOptions opts) throws TestLookupException {
        ObjectNode payload = MAPPER.createObjectNode();
        payload.put("project_id",  projectId);
        payload.put("client_name", clientName);
        payload.put("framework",   framework);
        payload.put("machine_id",  opts.machineId != null ? opts.machineId : getHostname());

        if (opts.buildNumber != null) payload.put("build_number", opts.buildNumber);
        if (opts.runId       != null) payload.put("run_id",       opts.runId);
        if (opts.branch      != null) payload.put("branch",       opts.branch);
        if (opts.commitHash  != null) payload.put("commit_hash",  opts.commitHash);
        if (opts.totalTests  >= 0)    payload.put("total_tests",  opts.totalTests);

        String body;
        try { body = MAPPER.writeValueAsString(payload); }
        catch (Exception e) { throw new TestLookupException("Serialization error", e); }

        String resp = postJson(baseUrl + "/api/v1/stream/sessions", body, token);

        try {
            var node = MAPPER.readTree(resp);
            return new LiveSession(
                node.get("session_id").asText(),
                node.get("session_token").asText(),
                node.get("run_id").asText(),
                this
            );
        } catch (Exception e) {
            throw new TestLookupException("Failed to parse session response: " + resp, e);
        }
    }

    /** Shorthand — start a session with only a build number. */
    public LiveSession startSession(String buildNumber) throws TestLookupException {
        return startSession(SessionOptions.builder().buildNumber(buildNumber).build());
    }

    /**
     * Mark a session as complete and trigger the AI analysis pipeline.
     * Called automatically by {@link LiveSession#close()}.
     */
    public void closeSession(String sessionId) {
        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl + "/api/v1/stream/sessions/" + sessionId))
            .header("Authorization", "Bearer " + token)
            .timeout(Duration.ofSeconds(REQUEST_TIMEOUT_SEC))
            .DELETE()
            .build();
        try {
            http.send(req, HttpResponse.BodyHandlers.discarding());
            LOG.info("TestLookup: session closed: " + sessionId);
        } catch (Exception e) {
            LOG.warning("TestLookup: failed to close session " + sessionId + ": " + e.getMessage());
        }
    }

    /**
     * Obtain a JWT access token via username/password login.
     *
     * <pre>{@code
     * String token = TestLookupReporter.login("http://localhost:8000", "admin", "secret");
     * }</pre>
     */
    public static String login(String baseUrl, String username, String password)
            throws TestLookupException {
        String formBody = "username=" + urlEncode(username) + "&password=" + urlEncode(password);
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl.replaceAll("/$", "") + "/api/v1/auth/login"))
            .header("Content-Type", "application/x-www-form-urlencoded")
            .timeout(Duration.ofSeconds(REQUEST_TIMEOUT_SEC))
            .POST(HttpRequest.BodyPublishers.ofString(formBody))
            .build();
        try {
            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200)
                throw new TestLookupException("Login failed (" + resp.statusCode() + "): " + resp.body());
            return MAPPER.readTree(resp.body()).get("access_token").asText();
        } catch (TestLookupException e) {
            throw e;
        } catch (Exception e) {
            throw new TestLookupException("Login request failed", e);
        }
    }

    // ── Internal ─────────────────────────────────────────────────────────────

    String postBatch(String sessionToken, String jsonBody) throws Exception {
        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl + "/api/v1/stream/events/batch"))
            .header("Content-Type", "application/json")
            .header("X-Session-Token", sessionToken)
            .timeout(Duration.ofSeconds(REQUEST_TIMEOUT_SEC))
            .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
            .build();
        HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (resp.statusCode() == 401) throw new TestLookupAuthException("Session token rejected");
        if (resp.statusCode() < 200 || resp.statusCode() >= 300)
            throw new RuntimeException("HTTP " + resp.statusCode() + ": " + resp.body());
        return resp.body();
    }

    private String postJson(String url, String body, String bearerToken) throws TestLookupException {
        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .header("Content-Type", "application/json")
            .header("Authorization", "Bearer " + bearerToken)
            .timeout(Duration.ofSeconds(REQUEST_TIMEOUT_SEC))
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .build();
        try {
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() < 200 || resp.statusCode() >= 300)
                throw new TestLookupException("HTTP " + resp.statusCode() + ": " + resp.body());
            return resp.body();
        } catch (TestLookupException e) {
            throw e;
        } catch (Exception e) {
            throw new TestLookupException("Request failed: " + url, e);
        }
    }

    int getBatchSize()         { return batchSize; }
    int getBatchIntervalMs()   { return batchIntervalMs; }
    int getMaxRetries()        { return MAX_RETRIES; }
    long getRetryBaseDelayMs() { return RETRY_BASE_DELAY_MS; }

    private static String getHostname() {
        try { return java.net.InetAddress.getLocalHost().getHostName(); }
        catch (Exception e) { return "unknown"; }
    }

    private static String urlEncode(String s) {
        try { return java.net.URLEncoder.encode(s, "UTF-8"); }
        catch (Exception e) { return s; }
    }

    // ── Nested types ─────────────────────────────────────────────────────────

    public enum TestStatus { PASSED, FAILED, SKIPPED, BROKEN }

    public static class SessionOptions {
        public final String buildNumber;
        public final String runId;
        public final String branch;
        public final String commitHash;
        public final String machineId;
        public final int    totalTests;

        private SessionOptions(Builder b) {
            this.buildNumber = b.buildNumber;
            this.runId       = b.runId;
            this.branch      = b.branch;
            this.commitHash  = b.commitHash;
            this.machineId   = b.machineId;
            this.totalTests  = b.totalTests;
        }

        public static Builder builder() { return new Builder(); }

        public static class Builder {
            private String buildNumber;
            private String runId;
            private String branch;
            private String commitHash;
            private String machineId;
            private int    totalTests = -1;

            public Builder buildNumber(String v)  { this.buildNumber = v; return this; }
            public Builder runId(String v)        { this.runId       = v; return this; }
            public Builder branch(String v)       { this.branch      = v; return this; }
            public Builder commitHash(String v)   { this.commitHash  = v; return this; }
            public Builder machineId(String v)    { this.machineId   = v; return this; }
            public Builder totalTests(int v)      { this.totalTests  = v; return this; }
            public SessionOptions build()         { return new SessionOptions(this); }
        }
    }

    public static class RecordOptions {
        public final String       suiteName;
        public final String       className;
        public final String       error;
        public final String       stackTrace;
        public final List<String> tags;
        public final Map<String, Object> metadata;

        private RecordOptions(Builder b) {
            this.suiteName  = b.suiteName;
            this.className  = b.className;
            this.error      = b.error;
            this.stackTrace = b.stackTrace;
            this.tags       = b.tags;
            this.metadata   = b.metadata;
        }

        public static Builder builder() { return new Builder(); }
        public static final RecordOptions EMPTY = builder().build();

        public static class Builder {
            private String       suiteName;
            private String       className;
            private String       error;
            private String       stackTrace;
            private List<String> tags;
            private Map<String, Object> metadata;

            public Builder suiteName(String v)           { this.suiteName  = v; return this; }
            public Builder className(String v)           { this.className  = v; return this; }
            public Builder error(String v)               { this.error      = v; return this; }
            public Builder stackTrace(String v)          { this.stackTrace = v; return this; }
            public Builder tags(List<String> v)          { this.tags       = v; return this; }
            public Builder metadata(Map<String, Object> v){ this.metadata  = v; return this; }
            public RecordOptions build()                 { return new RecordOptions(this); }
        }
    }

    // ── LiveSession ───────────────────────────────────────────────────────────

    /**
     * An active test execution session. Thread-safe.
     *
     * <p>Use with try-with-resources for automatic close-on-exit:
     * <pre>{@code
     * try (LiveSession s = reporter.startSession("build-42")) {
     *     s.record("my test", TestStatus.PASSED, 120);
     * }
     * }</pre>
     */
    public static class LiveSession implements AutoCloseable {
        private final String            sessionId;
        private final String            sessionToken;
        private final String            runId;
        private final TestLookupReporter reporter;

        private final BlockingQueue<ObjectNode>   queue;
        private final ScheduledExecutorService    scheduler;
        private final AtomicLong                  statSent    = new AtomicLong();
        private final AtomicLong                  statFailed  = new AtomicLong();
        private volatile boolean                  closed      = false;

        LiveSession(String sessionId, String sessionToken, String runId, TestLookupReporter reporter) {
            this.sessionId    = sessionId;
            this.sessionToken = sessionToken;
            this.runId        = runId;
            this.reporter     = reporter;
            this.queue        = new LinkedBlockingQueue<>(50_000);

            this.scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "testlookup-flusher-" + sessionId.substring(0, 8));
                t.setDaemon(true);
                return t;
            });

            scheduler.scheduleAtFixedRate(
                this::flushOnce,
                reporter.getBatchIntervalMs(),
                reporter.getBatchIntervalMs(),
                TimeUnit.MILLISECONDS
            );
            LOG.info("TestLookup: session started: " + sessionId + " run=" + runId);
        }

        public String getSessionId() { return sessionId; }
        public String getRunId()     { return runId; }
        public long   getSentCount() { return statSent.get(); }
        public long getFailedCount() { return statFailed.get(); }

        // ── Public API ────────────────────────────────────────────────────────

        /** Record a test result. */
        public void record(String testName, TestStatus status, long durationMs) {
            record(testName, status, durationMs, RecordOptions.EMPTY);
        }

        /** Record a test result with extra options. */
        public void record(String testName, TestStatus status, long durationMs, RecordOptions opts) {
            if (closed) return;
            ObjectNode event = MAPPER.createObjectNode();
            event.put("event_type",   "test_result");
            event.put("test_name",    testName);
            event.put("status",       status.name());
            event.put("duration_ms",  durationMs);
            event.put("timestamp_ms", Instant.now().toEpochMilli());

            if (opts.suiteName  != null) event.put("suite_name",     opts.suiteName);
            if (opts.className  != null) event.put("class_name",     opts.className);
            if (opts.error      != null) event.put("error_message",  opts.error);
            if (opts.stackTrace != null) event.put("stack_trace",    opts.stackTrace);
            if (opts.tags != null && !opts.tags.isEmpty()) {
                ArrayNode arr = event.putArray("tags");
                opts.tags.forEach(arr::add);
            }

            enqueue(event);
        }

        /** Record a log line (not a test result). */
        public void log(String message, String level) {
            if (closed) return;
            ObjectNode event = MAPPER.createObjectNode();
            event.put("event_type",   "log");
            event.putNull("test_name");
            event.putNull("status");
            event.put("timestamp_ms", Instant.now().toEpochMilli());
            ObjectNode meta = event.putObject("metadata");
            meta.put("level",   level != null ? level : "INFO");
            meta.put("message", message);
            enqueue(event);
        }

        /** Record a numeric metric (e.g. response time, memory usage). */
        public void metric(String name, double value, String unit) {
            if (closed) return;
            ObjectNode event = MAPPER.createObjectNode();
            event.put("event_type",   "metric");
            event.put("test_name",    name);
            event.putNull("status");
            event.put("duration_ms",  (long) value);
            event.put("timestamp_ms", Instant.now().toEpochMilli());
            ObjectNode meta = event.putObject("metadata");
            meta.put("value", value);
            meta.put("unit",  unit != null ? unit : "");
            enqueue(event);
        }

        /**
         * Flush remaining events, stop the background scheduler, and close the session
         * on the server (triggering the AI analysis pipeline).
         * Called automatically by try-with-resources.
         */
        @Override
        public void close() {
            if (closed) return;
            closed = true;

            scheduler.shutdown();
            try { scheduler.awaitTermination(5, TimeUnit.SECONDS); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }

            // Final drain
            flushAll();

            reporter.closeSession(sessionId);
            LOG.info("TestLookup: session closed: " + sessionId
                + " sent=" + statSent.get() + " failed=" + statFailed.get());
        }

        // ── Internal ─────────────────────────────────────────────────────────

        private void enqueue(ObjectNode event) {
            if (!queue.offer(event)) {
                LOG.warning("TestLookup: event queue full — dropping event");
            }
            if (queue.size() >= reporter.getBatchSize()) {
                scheduler.execute(this::flushOnce);
            }
        }

        private void flushOnce() {
            if (queue.isEmpty()) return;
            List<ObjectNode> batch = new ArrayList<>(reporter.getBatchSize());
            queue.drainTo(batch, reporter.getBatchSize());
            if (!batch.isEmpty()) postBatch(batch);
        }

        private void flushAll() {
            while (!queue.isEmpty()) {
                List<ObjectNode> batch = new ArrayList<>(MAX_BATCH_SIZE);
                queue.drainTo(batch, MAX_BATCH_SIZE);
                if (!batch.isEmpty()) postBatch(batch);
            }
        }

        private void postBatch(List<ObjectNode> events) {
            try {
                ObjectNode payload = MAPPER.createObjectNode();
                payload.put("session_id", sessionId);
                payload.put("run_id",     runId);
                ArrayNode arr = payload.putArray("events");
                events.forEach(arr::add);
                String body = MAPPER.writeValueAsString(payload);

                for (int attempt = 1; attempt <= reporter.getMaxRetries(); attempt++) {
                    try {
                        String resp = reporter.postBatch(sessionToken, body);
                        int accepted = MAPPER.readTree(resp).path("accepted").asInt(events.size());
                        statSent.addAndGet(accepted);
                        return;
                    } catch (TestLookupAuthException e) {
                        LOG.severe("TestLookup: session token rejected — stopping flush");
                        statFailed.addAndGet(events.size());
                        return;
                    } catch (Exception e) {
                        long delay = reporter.getRetryBaseDelayMs() * (1L << (attempt - 1));
                        if (attempt == reporter.getMaxRetries()) {
                            LOG.severe("TestLookup: batch POST failed after " + attempt
                                + " attempts (" + events.size() + " events lost): " + e.getMessage());
                            statFailed.addAndGet(events.size());
                            return;
                        }
                        LOG.warning("TestLookup: batch attempt " + attempt + " failed, retrying in " + delay + "ms");
                        try { Thread.sleep(delay); } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            return;
                        }
                    }
                }
            } catch (Exception e) {
                LOG.severe("TestLookup: unexpected error during batch POST: " + e.getMessage());
                statFailed.addAndGet(events.size());
            }
        }
    }

    // ── Exception types ───────────────────────────────────────────────────────

    public static class TestLookupException extends Exception {
        public TestLookupException(String msg) { super(msg); }
        public TestLookupException(String msg, Throwable cause) { super(msg, cause); }
    }

    static class TestLookupAuthException extends Exception {
        public TestLookupAuthException(String msg) { super(msg); }
    }
}
