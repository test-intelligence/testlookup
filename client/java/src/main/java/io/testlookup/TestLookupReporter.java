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
 * <h3>Quick start (API key — recommended for CI/CD)</h3>
 * <pre>{@code
 * TestLookupReporter reporter = new TestLookupReporter.Builder()
 *     .baseUrl("http://localhost:8000")
 *     .apiKey("qai_...")
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
 * <h3>Quick start (JWT token)</h3>
 * <pre>{@code
 * TestLookupReporter reporter = new TestLookupReporter.Builder()
 *     .baseUrl("http://localhost:8000")
 *     .token("<jwt>")
 *     .projectId("<uuid>")
 *     .build();
 * }</pre>
 *
 * <h3>Zero-code TestNG integration</h3>
 * <p>Add the listener to your {@code testng.xml} with suite parameters — no Java code needed:
 * <pre>{@code
 * <suite name="My Suite">
 *   <parameter name="testlookup.url" value="http://localhost:8000"/>
 *   <parameter name="testlookup.apiKey" value="qai_..."/>
 *   <parameter name="testlookup.projectId" value="your-project-uuid"/>
 *   <listeners>
 *     <listener class-name="io.testlookup.testng.TestLookupListener"/>
 *   </listeners>
 * </suite>
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
    /**
     * Hard cap on the wall-clock time a single batch can spend retrying.
     * Matches the Python SDK's {@code MAX_RETRY_TOTAL_SECONDS}; 5 min lets
     * one server-side rate-limit window (1 min fixed bucket) be ridden out
     * without infinitely buffering during a sustained outage. A
     * misconfigured project surfaces a hard failure instead of silently
     * holding state for hours.
     */
    static final long MAX_RETRY_TOTAL_MS = 300_000L;
    private static final int CONNECT_TIMEOUT_SEC = 10;
    private static final int REQUEST_TIMEOUT_SEC = 30;

    /**
     * Heartbeat interval — emit a ``live_heartbeat`` event whenever the
     * SDK has been silent this long. The server uses Redis
     * ``last_event_at`` to drive its idle-session reaper (5-minute
     * threshold by default); without the heartbeat a legitimate run with
     * a single long test would be falsely closed mid-flight. The
     * interval is well under the server's reaper threshold so the
     * reaper still fires promptly when the client genuinely dies.
     */
    private static final long HEARTBEAT_INTERVAL_MS = 30_000L;

    // ── Builder ──────────────────────────────────────────────────────────────

    private final String     baseUrl;
    private final String     token;      // JWT Bearer token (mutually exclusive with apiKey)
    private final String     apiKey;     // X-API-Key header value
    private final String     projectId;
    private final String     clientName;
    private final String     framework;
    private final String     suiteName;  // resolved from testlookup.suite or testlookup.launch
    private final String     releaseName; // resolved from testlookup.release; null → server uses project default
    private final int        batchSize;
    private final int        batchIntervalMs;
    private final HttpClient http;

    static final ObjectMapper MAPPER = new ObjectMapper();

    private TestLookupReporter(Builder b) {
        this.baseUrl        = b.baseUrl.replaceAll("/$", "");
        this.token          = b.token;
        this.apiKey         = b.apiKey;
        this.projectId      = b.projectId;
        this.clientName     = b.clientName != null ? b.clientName : getHostname();
        this.framework      = b.framework  != null ? b.framework  : "java";
        // Resolved run-level suite: testlookup.suite preferred, testlookup.launch fallback.
        this.suiteName      = b.suiteName;
        this.releaseName    = b.releaseName;
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
        private String apiKey;
        private String projectId;
        private String clientName;
        private String framework;
        private String suiteName;
        private String releaseName;
        private int    batchSize;
        private int    batchIntervalMs;

        public Builder baseUrl(String v)         { this.baseUrl        = v; return this; }
        /** Set JWT Bearer token for authentication. Mutually exclusive with {@link #apiKey}. */
        public Builder token(String v)           { this.token          = v; return this; }
        /** Set API key for authentication (sent as X-API-Key header). Mutually exclusive with {@link #token}. */
        public Builder apiKey(String v)          { this.apiKey         = v; return this; }
        public Builder projectId(String v)       { this.projectId      = v; return this; }
        public Builder clientName(String v)      { this.clientName     = v; return this; }
        public Builder framework(String v)       { this.framework      = v; return this; }
        /** Default run-level suite applied to every record() that doesn't override it. */
        public Builder suiteName(String v)       { this.suiteName      = v; return this; }
        /** Default release name. Null → server falls back to the project's default release. */
        public Builder releaseName(String v)     { this.releaseName    = v; return this; }
        public Builder batchSize(int v)          { this.batchSize      = v; return this; }
        public Builder batchIntervalMs(int v)    { this.batchIntervalMs = v; return this; }

        /**
         * Build the reporter, resolving unset fields from testlookup.yaml and
         * environment variables. Explicit Builder values always take precedence.
         */
        public TestLookupReporter build() {
            Map<String, Object> cfg = ConfigLoader.load();
            if (this.baseUrl == null)    this.baseUrl    = ConfigLoader.getString(cfg, "server.url", null);
            if (this.token == null)      this.token      = ConfigLoader.getString(cfg, "auth.token", null);
            if (this.apiKey == null)     this.apiKey     = ConfigLoader.getString(cfg, "auth.api_key", null);
            if (this.projectId == null)  this.projectId  = ConfigLoader.getString(cfg, "project.id", null);
            if (this.clientName == null) this.clientName  = ConfigLoader.getString(cfg, "reporting.client_name", null);
            if (this.framework == null)  this.framework   = ConfigLoader.getString(cfg, "reporting.framework", null);
            if (this.suiteName == null)  this.suiteName   = ConfigLoader.getString(cfg, "reporting.suite_name", null);
            // Fallback: testlookup.launch (reporting.launch_name) doubles as the
            // run-level suite when testlookup.suite isn't explicitly configured.
            if (this.suiteName == null)  this.suiteName   = ConfigLoader.getString(cfg, "reporting.launch_name", null);
            if (this.releaseName == null) this.releaseName = ConfigLoader.getString(cfg, "reporting.release_name", null);
            if (this.batchSize <= 0)     this.batchSize   = ConfigLoader.getInt(cfg, "reporting.batch_size", 0);
            if (this.batchIntervalMs<=0) this.batchIntervalMs = ConfigLoader.getInt(cfg, "reporting.batch_interval_ms", 0);

            boolean hasAuth = this.token != null || this.apiKey != null;
            if (this.baseUrl == null || !hasAuth || this.projectId == null) {
                throw new IllegalStateException(
                    "baseUrl, token or apiKey, and projectId are required. "
                    + "Provide them via Builder, testlookup.yaml, env vars "
                    + "(TESTLOOKUP_URL, TESTLOOKUP_TOKEN or TESTLOOKUP_API_KEY, TESTLOOKUP_PROJECT_ID), "
                    + "or JVM properties (-Dtestlookup.url, -Dtestlookup.token or -Dtestlookup.apiKey, -Dtestlookup.projectId)."
                );
            }
            return new TestLookupReporter(this);
        }
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
        if (opts.branch      != null) payload.put("branch",       opts.branch);
        if (opts.commitHash  != null) payload.put("commit_hash",  opts.commitHash);
        if (opts.totalTests  >= 0)    payload.put("total_tests",  opts.totalTests);
        // Per-session suite beats the reporter-level default (resolved from
        // testlookup.suite > testlookup.launch in Builder.build).
        String sessionSuite = opts.suiteName != null ? opts.suiteName : suiteName;
        // Same precedence for release_name: per-session override > reporter
        // default (testlookup.release). Blank/null → server falls back to the
        // project's default release.
        String sessionRelease = opts.releaseName != null ? opts.releaseName : releaseName;
        if (opts.launchName  != null) payload.put("launch_name",  opts.launchName);
        if (sessionSuite     != null) payload.put("suite_name",   sessionSuite);
        if (sessionRelease   != null) payload.put("release_name", sessionRelease);

        String body;
        try { body = MAPPER.writeValueAsString(payload); }
        catch (Exception e) { throw new TestLookupException("Serialization error", e); }

        String resp = postJson(baseUrl + "/api/v1/stream/sessions", body);

        try {
            var node = MAPPER.readTree(resp);
            return new LiveSession(
                node.get("session_id").asText(),
                node.get("session_token").asText(),
                node.get("run_id").asText(),
                sessionSuite,
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
        HttpRequest.Builder rb = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl + "/api/v1/stream/sessions/" + sessionId))
            .timeout(Duration.ofSeconds(REQUEST_TIMEOUT_SEC))
            .DELETE();
        applyAuth(rb);
        try {
            // Capture the body so a non-2xx response surfaces a useful diagnostic.
            // Without this, a 401 from the server returns to the SDK as "success"
            // and the live-session row stays status=active forever — no TestRun
            // is ever persisted and the dashboard never sees the run.
            HttpResponse<String> resp = http.send(rb.build(),
                HttpResponse.BodyHandlers.ofString());
            int status = resp.statusCode();
            if (status >= 200 && status < 300) {
                LOG.info("TestLookup: session closed: " + sessionId);
            } else {
                String body = resp.body();
                if (body != null && body.length() > 500) {
                    body = body.substring(0, 500) + "…";
                }
                LOG.warning("TestLookup: server rejected close for session "
                    + sessionId + " — HTTP " + status + " body=" + body);
            }
        } catch (Exception e) {
            LOG.warning("TestLookup: failed to close session " + sessionId
                + ": " + e.getMessage());
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
        int status = resp.statusCode();
        if (status == 401) throw new TestLookupAuthException("Session token rejected");
        if (RetryPolicy.isRetryableStatus(status)) {
            // Surface the Retry-After header to the caller so the retry
            // loop can honour the server's backoff hint exactly.
            String retryAfter = resp.headers().firstValue("Retry-After").orElse(null);
            throw new RetryableHttpException(status, retryAfter,
                "HTTP " + status + ": " + resp.body());
        }
        if (status < 200 || status >= 300)
            throw new RuntimeException("HTTP " + status + ": " + resp.body());
        return resp.body();
    }

    private String postJson(String url, String body) throws TestLookupException {
        HttpRequest.Builder rb = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .header("Content-Type", "application/json")
            .timeout(Duration.ofSeconds(REQUEST_TIMEOUT_SEC))
            .POST(HttpRequest.BodyPublishers.ofString(body));
        applyAuth(rb);
        try {
            HttpResponse<String> resp = http.send(rb.build(), HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() < 200 || resp.statusCode() >= 300)
                throw new TestLookupException("HTTP " + resp.statusCode() + ": " + resp.body());
            return resp.body();
        } catch (TestLookupException e) {
            throw e;
        } catch (Exception e) {
            // Surface the underlying cause in the message so listeners that
            // log only ``e.getMessage()`` still see *why* the request failed
            // (timeout vs connect-refused vs unknown-host). The previous
            // "Request failed: <url>" stripped the actual diagnostic, which
            // left users with a half-disabled SDK and no way to self-recover
            // without attaching a debugger — see 2026-05-15 regression report.
            String cause = e.getClass().getSimpleName()
                + (e.getMessage() != null ? ": " + e.getMessage() : "");
            throw new TestLookupException("Request failed (" + cause + "): " + url, e);
        }
    }

    /** Apply the correct auth header: X-API-Key if apiKey is set, else Authorization Bearer. */
    private void applyAuth(HttpRequest.Builder rb) {
        if (apiKey != null) {
            rb.header("X-API-Key", apiKey);
        } else if (token != null) {
            rb.header("Authorization", "Bearer " + token);
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
        public final String branch;
        public final String commitHash;
        public final String machineId;
        public final int    totalTests;
        public final String launchName;
        public final String suiteName;
        public final String releaseName;

        private SessionOptions(Builder b) {
            this.buildNumber = b.buildNumber;
            this.branch      = b.branch;
            this.commitHash  = b.commitHash;
            this.machineId   = b.machineId;
            this.totalTests  = b.totalTests;
            this.launchName  = b.launchName;
            this.suiteName   = b.suiteName;
            this.releaseName = b.releaseName;
        }

        public static Builder builder() { return new Builder(); }

        public static class Builder {
            private String buildNumber;
            private String branch;
            private String commitHash;
            private String machineId;
            private int    totalTests = -1;
            private String launchName;
            private String suiteName;
            private String releaseName;

            public Builder buildNumber(String v)  { this.buildNumber = v; return this; }
            public Builder branch(String v)       { this.branch      = v; return this; }
            public Builder commitHash(String v)   { this.commitHash  = v; return this; }
            public Builder machineId(String v)    { this.machineId   = v; return this; }
            public Builder totalTests(int v)      { this.totalTests  = v; return this; }
            public Builder launchName(String v)   { this.launchName  = v; return this; }
            /** Per-session suite override (beats the reporter-level default). */
            public Builder suiteName(String v)    { this.suiteName   = v; return this; }
            /** Per-session release override (beats the reporter-level default). */
            public Builder releaseName(String v)  { this.releaseName = v; return this; }
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
        private final String            suiteName;  // record() default
        private final TestLookupReporter reporter;

        private final BlockingQueue<ObjectNode>   queue;
        private final ScheduledExecutorService    scheduler;
        private final AtomicLong                  statSent    = new AtomicLong();
        private final AtomicLong                  statFailed  = new AtomicLong();
        /** Wall-clock epoch ms of the most recent real (non-heartbeat) event we
         *  enqueued — used to suppress heartbeats while the session is busy. */
        private final AtomicLong                  lastRealEnqueueMs = new AtomicLong(Instant.now().toEpochMilli());
        private volatile boolean                  closed      = false;

        LiveSession(String sessionId, String sessionToken, String runId,
                    String suiteName, TestLookupReporter reporter) {
            this.sessionId    = sessionId;
            this.sessionToken = sessionToken;
            this.runId        = runId;
            this.suiteName    = suiteName;
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

            // Heartbeat task — keeps the server-side last_event_at fresh
            // during long inter-test gaps so the idle-session reaper
            // doesn't kill a legitimately-running session. Runs on the
            // same scheduler so it shuts down with the session.
            scheduler.scheduleAtFixedRate(
                this::heartbeatTick,
                HEARTBEAT_INTERVAL_MS,
                HEARTBEAT_INTERVAL_MS,
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

            // Per-record suite wins; otherwise inherit the session-level suite
            // resolved from testlookup.suite > testlookup.launch.
            String effectiveSuite = opts.suiteName != null ? opts.suiteName : this.suiteName;
            if (effectiveSuite  != null) event.put("suite_name",     effectiveSuite);
            if (opts.className  != null) event.put("class_name",     opts.className);
            if (opts.error      != null) event.put("error_message",  opts.error);
            if (opts.stackTrace != null) event.put("stack_trace",    opts.stackTrace);
            if (opts.tags != null && !opts.tags.isEmpty()) {
                ArrayNode arr = event.putArray("tags");
                opts.tags.forEach(arr::add);
            }
            if (opts.metadata != null && !opts.metadata.isEmpty()) {
                event.set("metadata", MAPPER.valueToTree(opts.metadata));
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
            lastRealEnqueueMs.set(Instant.now().toEpochMilli());
            if (!queue.offer(event)) {
                LOG.warning("TestLookup: event queue full — dropping event");
            }
            if (queue.size() >= reporter.getBatchSize()) {
                scheduler.execute(this::flushOnce);
            }
        }

        /**
         * Emit a ``live_heartbeat`` event if no real event has been
         * enqueued in {@link #HEARTBEAT_INTERVAL_MS}. The heartbeat
         * carries no test payload — its only job is to bump the
         * server-side ``last_event_at`` so the reaper doesn't classify
         * the session as idle. We don't update
         * {@code lastRealEnqueueMs} for heartbeats so the suppression
         * check stays based on real activity only.
         */
        void heartbeatTick() {
            if (closed) return;
            long sinceLast = Instant.now().toEpochMilli() - lastRealEnqueueMs.get();
            if (sinceLast < HEARTBEAT_INTERVAL_MS) return;
            ObjectNode event = MAPPER.createObjectNode();
            event.put("event_type",   "live_heartbeat");
            event.put("timestamp_ms", Instant.now().toEpochMilli());
            // NB: skip enqueue()'s lastRealEnqueueMs.set — heartbeat is
            // not a "real" event, so we don't want it to suppress the
            // next heartbeat tick.
            if (!queue.offer(event)) {
                LOG.fine("TestLookup: heartbeat dropped, queue full");
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

                long startMs = System.currentTimeMillis();
                for (int attempt = 1; attempt <= reporter.getMaxRetries(); attempt++) {
                    long elapsedMs = System.currentTimeMillis() - startMs;
                    try {
                        String resp = reporter.postBatch(sessionToken, body);
                        int accepted = MAPPER.readTree(resp).path("accepted").asInt(events.size());
                        statSent.addAndGet(accepted);
                        return;
                    } catch (TestLookupAuthException e) {
                        LOG.severe("TestLookup: session token rejected — stopping flush");
                        statFailed.addAndGet(events.size());
                        return;
                    } catch (RetryableHttpException e) {
                        long delay = RetryPolicy.computeNextDelayMs(
                            e.retryAfter, attempt, elapsedMs,
                            reporter.getRetryBaseDelayMs(), MAX_RETRY_TOTAL_MS);
                        if (delay < 0 || attempt == reporter.getMaxRetries()) {
                            LOG.severe("TestLookup: giving up after HTTP " + e.statusCode
                                + " (attempt=" + attempt + " events=" + events.size()
                                + " elapsedMs=" + elapsedMs + " capMs=" + MAX_RETRY_TOTAL_MS
                                + " runId=" + runId + ")");
                            statFailed.addAndGet(events.size());
                            return;
                        }
                        LOG.warning("TestLookup: batch throttled: status=" + e.statusCode
                            + " attempt=" + attempt + " delayMs=" + delay
                            + " retryAfter=" + e.retryAfter + " elapsedMs=" + elapsedMs
                            + " runId=" + runId + " events=" + events.size());
                        try { Thread.sleep(delay); } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            return;
                        }
                    } catch (Exception e) {
                        long delay = RetryPolicy.computeNextDelayMs(
                            null, attempt, elapsedMs,
                            reporter.getRetryBaseDelayMs(), MAX_RETRY_TOTAL_MS);
                        if (delay < 0 || attempt == reporter.getMaxRetries()) {
                            LOG.severe("TestLookup: batch POST failed after " + attempt
                                + " attempts (events=" + events.size()
                                + " elapsedMs=" + elapsedMs + " capMs=" + MAX_RETRY_TOTAL_MS
                                + " runId=" + runId + "): " + e.getMessage());
                            statFailed.addAndGet(events.size());
                            return;
                        }
                        LOG.warning("TestLookup: batch transport error: attempt=" + attempt
                            + " delayMs=" + delay + " elapsedMs=" + elapsedMs
                            + " runId=" + runId + " events=" + events.size()
                            + " error=" + e.getClass().getSimpleName() + ": " + e.getMessage());
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

    // ── Retry policy ──────────────────────────────────────────────────────────

    /**
     * Pure-function helpers for the batch retry contract. Mirrors the Python
     * SDK's {@code _compute_next_retry_delay} so both clients honour
     * {@code Retry-After} the same way and cap cumulative retry time at the
     * same wall-clock budget.
     *
     * <p>Why a separate class: the retry decision is testable without HTTP,
     * which means we don't need a MockWebServer dependency just to pin the
     * 429/503/Retry-After/total-cap rules.
     */
    static final class RetryPolicy {

        /** Status codes the SDK treats as transient server-side back-pressure. */
        static boolean isRetryableStatus(int status) {
            return status == 429 || status == 503;
        }

        /**
         * Parse an HTTP ``Retry-After`` header value into milliseconds.
         * Accepts either a delta-seconds integer (e.g. ``"30"``) or an
         * HTTP-date (RFC 9110 §10.2.3). Returns ``-1`` when the header is
         * absent or unparseable so the caller falls back to backoff.
         */
        static long parseRetryAfterMs(String headerValue) {
            if (headerValue == null) return -1L;
            String value = headerValue.trim();
            if (value.isEmpty()) return -1L;
            try {
                double seconds = Double.parseDouble(value);
                return Math.max(0L, (long) (seconds * 1000.0));
            } catch (NumberFormatException ignored) {
                // fall through to HTTP-date parsing
            }
            try {
                java.time.ZonedDateTime target =
                    java.time.ZonedDateTime.parse(value, java.time.format.DateTimeFormatter.RFC_1123_DATE_TIME);
                long deltaMs = java.time.Duration.between(
                    java.time.ZonedDateTime.now(target.getZone()), target).toMillis();
                return Math.max(0L, deltaMs);
            } catch (Exception ignored) {
                return -1L;
            }
        }

        /**
         * Returns the milliseconds to wait before the next retry, or ``-1``
         * when the cumulative retry budget would be blown.
         *
         * <p>Precedence: an explicit ``Retry-After`` header wins absolutely
         * (no doubling, no halving — we honour exactly what the server
         * said). Without one we fall back to the exponential schedule
         * {@code baseDelayMs * 2^(attempt-1)}. In either case, if the
         * resulting delay would push total elapsed past {@code capMs}, we
         * return ``-1`` so the caller surfaces a hard failure.
         */
        static long computeNextDelayMs(
                String retryAfterHeader,
                int attempt,
                long elapsedMs,
                long baseDelayMs,
                long capMs) {
            if (elapsedMs >= capMs) return -1L;
            long parsed = parseRetryAfterMs(retryAfterHeader);
            long delay = parsed >= 0
                ? parsed
                : baseDelayMs * (1L << Math.max(0, attempt - 1));
            long remaining = capMs - elapsedMs;
            if (delay > remaining) return -1L;
            return delay;
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

    /**
     * Thrown by {@link #postBatch} when the server returns a retryable
     * HTTP status (429/503). Carries the status code and the raw
     * {@code Retry-After} header so the calling retry loop can honour
     * the server's backoff hint exactly.
     */
    static class RetryableHttpException extends Exception {
        final int statusCode;
        final String retryAfter;
        RetryableHttpException(int statusCode, String retryAfter, String msg) {
            super(msg);
            this.statusCode = statusCode;
            this.retryAfter = retryAfter;
        }
    }
}
