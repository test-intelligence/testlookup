package io.testlookup.junit5;

import io.testlookup.ConfigLoader;
import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;
import io.testlookup.http.ExchangeBuffer;

import org.junit.jupiter.api.extension.*;

import java.util.Map;
import java.util.Optional;
import java.util.logging.Logger;

/**
 * JUnit 5 Extension — streams test results to TestLookup in real-time.
 *
 * <p>One live session covers the whole test run, however many classes use the
 * extension: it opens before the first class and closes when JUnit finishes
 * the run. Each result carries the test method's measured duration.
 *
 * <h3>Usage (class-level annotation)</h3>
 * <pre>{@code
 * @ExtendWith(TestLookupExtension.class)
 * class MyTest {
 *     @Test void testLogin() { ... }
 *     @Test void testCheckout() { ... }
 * }
 * }</pre>
 *
 * <h3>Global registration</h3>
 * The jar ships {@code META-INF/services/org.junit.jupiter.api.extension.Extension}
 * naming {@code io.testlookup.junit5.TestLookupExtension}; enable JUnit's
 * auto-detection with {@code -Djunit.jupiter.extensions.autodetection.enabled=true}.
 *
 * <h3>Configuration</h3>
 * Everything resolves through {@link ConfigLoader}: JVM system properties &gt;
 * environment variables &gt; {@code testlookup.properties} / {@code testlookup.yaml}
 * (filesystem, then classpath).
 * <pre>
 *   -Dtestlookup.url        TESTLOOKUP_URL / _ENDPOINT   testlookup.endpoint   (required)
 *   -Dtestlookup.apiKey     TESTLOOKUP_API_KEY           testlookup.api.key    (or a token)
 *   -Dtestlookup.token      TESTLOOKUP_TOKEN             testlookup.token
 *   -Dtestlookup.projectId  TESTLOOKUP_PROJECT(_ID)      testlookup.project    (required)
 *   -Dtestlookup.build      TESTLOOKUP_BUILD             testlookup.build      (default junit5-&lt;ms&gt;)
 *   -Dtestlookup.branch     TESTLOOKUP_BRANCH            testlookup.branch
 *   -Dtestlookup.commit     TESTLOOKUP_COMMIT            testlookup.commit
 *   -Dtestlookup.launch     TESTLOOKUP_LAUNCH            testlookup.launch
 *   -Dtestlookup.suite      TESTLOOKUP_SUITE             testlookup.suite      (default: launch)
 *   -Dtestlookup.release    TESTLOOKUP_RELEASE           testlookup.release
 *   -Dtestlookup.ca_cert_path TESTLOOKUP_CA_CERT         testlookup.ca_cert_path
 *   -Dtestlookup.insecure   TESTLOOKUP_INSECURE          testlookup.insecure
 * </pre>
 *
 * <h3>HTTP capture</h3>
 * With {@code io.testlookup.restassured.TestLookupRestAssuredFilter} registered, the RestAssured calls made by
 * the test method (and its {@code @AfterEach} methods) are sent, redacted, after its result. This class never
 * loads RestAssured.
 */
public class TestLookupExtension
        implements BeforeAllCallback, BeforeEachCallback, BeforeTestExecutionCallback,
                   AfterTestExecutionCallback, AfterEachCallback, TestWatcher {

    private static final Logger LOG = Logger.getLogger(TestLookupExtension.class.getName());
    private static final String NS  = "io.testlookup";

    private static final ExtensionContext.Namespace NAMESPACE =
        ExtensionContext.Namespace.create(NS);

    private static final String KEY_RUN      = "run";
    private static final String KEY_STARTED  = "startedNanos";

    /**
     * The run's session, kept in the ROOT context's store: JUnit closes that
     * store once, after the last test of the run. A per-class store (as
     * before) opened and closed one session per test class, so a suite of 40
     * classes became 40 runs in TestLookup.
     */
    static final class RunSession implements ExtensionContext.Store.CloseableResource {
        /** null when the session could not be opened — the run continues unreported. */
        final LiveSession session;
        /**
         * Measured duration per test (unique id). Kept here, not in the test's
         * own store: JUnit may close that store before TestWatcher callbacks.
         */
        final Map<String, Long> durationsMs = new java.util.concurrent.ConcurrentHashMap<>();
        /** Phase timing per test (unique id), for the same reason. */
        final Map<String, Phases> phases = new java.util.concurrent.ConcurrentHashMap<>();

        RunSession(LiveSession session) { this.session = session; }

        @Override
        public void close() {
            if (session == null) return;
            session.close();
            LOG.info("TestLookupExtension: session closed: " + session.getSessionId());
        }
    }

    // ── BeforeAllCallback ─────────────────────────────────────────────────────

    @Override
    public void beforeAll(ExtensionContext ctx) {
        // When auto-discovered via ServiceLoader, skip silently if not configured
        if (!ConfigLoader.isConfigured()) {
            LOG.fine("TestLookupExtension: no configuration found — skipping");
            return;
        }
        ctx.getRoot().getStore(NAMESPACE)
            .getOrComputeIfAbsent(KEY_RUN, k -> openRun(), RunSession.class);
    }

    private static RunSession openRun() {
        try {
            Map<String, Object> cfg = ConfigLoader.load();
            // Builder.build() resolves url / auth / project / suite / release /
            // TLS from the same sources.
            TestLookupReporter reporter = new TestLookupReporter.Builder()
                .framework(ConfigLoader.getString(cfg, "reporting.framework", "junit5"))
                .build();

            String launch = ConfigLoader.getString(cfg, "reporting.launch_name", null);
            LiveSession session = reporter.startSession(
                SessionOptions.builder()
                    .buildNumber(ConfigLoader.getString(cfg, "ci.build_number",
                                    "junit5-" + System.currentTimeMillis()))
                    .branch(     ConfigLoader.getString(cfg, "ci.branch", null))
                    .commitHash( ConfigLoader.getString(cfg, "ci.commit_hash", null))
                    .launchName( launch)
                    .suiteName(  ConfigLoader.getString(cfg, "reporting.suite_name", launch))
                    .build()
            );
            LOG.info("TestLookupExtension: session started: " + session.getSessionId());
            return new RunSession(session);
        } catch (Exception e) {
            LOG.warning("TestLookupExtension: disabled — " + e.getMessage());
            return new RunSession(null);
        }
    }

    // ── Timing ────────────────────────────────────────────────────────────────

    /**
     * When a test began and how long its {@code @BeforeEach} / body / {@code @AfterEach}
     * took. JUnit calls the extension's beforeEach before the test's own
     * {@code @BeforeEach} methods and its afterEach after the {@code @AfterEach}
     * methods, so the gaps between the callbacks are the setup and teardown.
     */
    static final class Phases {
        final long startedAtMs = System.currentTimeMillis();
        final long eachNanos = System.nanoTime();
        volatile long bodyStartNanos = -1;
        volatile long bodyEndNanos = -1;
        volatile Long setupMs;
        volatile Long callMs;
        volatile Long teardownMs;
    }

    @Override
    public void beforeEach(ExtensionContext ctx) {
        RunSession run = run(ctx);
        if (run != null) run.phases.put(ctx.getUniqueId(), new Phases());
    }

    @Override
    public void beforeTestExecution(ExtensionContext ctx) {
        long now = System.nanoTime();
        ctx.getStore(NAMESPACE).put(KEY_STARTED, now);
        Phases p = phases(ctx);
        if (p != null) {
            p.bodyStartNanos = now;
            p.setupMs = (now - p.eachNanos) / 1_000_000L;
        }
        // HTTP capture: the test's buffer opens right before the test method and closes once the result is
        // recorded. A ThreadLocal, never the test's store: JUnit closes that store before the TestWatcher
        // callbacks run.
        RunSession run = run(ctx);
        if (run != null && run.session != null) ExchangeBuffer.open(testName(ctx), className(ctx));
    }

    @Override
    public void afterTestExecution(ExtensionContext ctx) {
        long now = System.nanoTime();
        Long started = ctx.getStore(NAMESPACE).remove(KEY_STARTED, Long.class);
        RunSession run = run(ctx);
        if (started != null && run != null) {
            run.durationsMs.put(ctx.getUniqueId(), (now - started) / 1_000_000L);
        }
        Phases p = phases(ctx);
        if (p != null && p.bodyStartNanos >= 0) {
            p.bodyEndNanos = now;
            p.callMs = (now - p.bodyStartNanos) / 1_000_000L;
        }
    }

    @Override
    public void afterEach(ExtensionContext ctx) {
        Phases p = phases(ctx);
        if (p != null && p.bodyEndNanos >= 0) {
            p.teardownMs = (System.nanoTime() - p.bodyEndNanos) / 1_000_000L;
        }
    }

    private static Phases phases(ExtensionContext ctx) {
        RunSession run = run(ctx);
        return run == null ? null : run.phases.get(ctx.getUniqueId());
    }

    // ── TestWatcher ───────────────────────────────────────────────────────────

    @Override
    public void testSuccessful(ExtensionContext ctx) {
        record(ctx, TestStatus.PASSED, null, null);
    }

    @Override
    public void testFailed(ExtensionContext ctx, Throwable cause) {
        String error = cause != null ? cause.getMessage() : "Test failed";
        String stack = cause != null ? stackTraceToString(cause) : null;
        record(ctx, TestStatus.FAILED, error, stack);
    }

    @Override
    public void testAborted(ExtensionContext ctx, Throwable cause) {
        String error = cause != null ? cause.getMessage() : "Test aborted";
        record(ctx, TestStatus.BROKEN, error, null);
    }

    @Override
    public void testDisabled(ExtensionContext ctx, Optional<String> reason) {
        record(ctx, TestStatus.SKIPPED, reason.orElse(null), null);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private void record(ExtensionContext ctx, TestStatus status, String error, String stack) {
        RunSession run = run(ctx);
        if (run == null || run.session == null) {
            ExchangeBuffer.close();
            return;
        }

        String testName  = testName(ctx);
        String suiteName = ctx.getParent().map(ExtensionContext::getDisplayName).orElse(null);
        // A disabled test never executes, so it has no duration.
        Long durationMs = run.durationsMs.remove(ctx.getUniqueId());
        // ...and no timing either: beforeEach never ran for it.
        Phases phases = run.phases.remove(ctx.getUniqueId());

        RecordOptions.Builder opts = RecordOptions.builder()
            .suiteName(suiteName)
            .className(className(ctx))
            .error(error)
            .stackTrace(stack);
        if (phases != null) {
            opts.startedAtMs(phases.startedAtMs)
                .setupMs(phases.setupMs)
                .callMs(phases.callMs)
                .teardownMs(phases.teardownMs);
        }

        run.session.record(testName, status, durationMs != null ? durationMs : 0L, opts.build());
        sendCapturedExchanges(run.session);
    }

    /** Close this thread's HTTP capture buffer and queue what it holds; never fails the test. */
    private static void sendCapturedExchanges(LiveSession session) {
        try {
            ExchangeBuffer.Snapshot captured = ExchangeBuffer.close();
            if (captured != null) {
                session.postHttpExchanges(captured.testName, captured.className, captured.exchanges,
                    captured.dropped);
            }
        } catch (RuntimeException e) {
            LOG.fine("TestLookupExtension: captured HTTP exchanges not sent (" + e.getClass().getSimpleName() + ")");
        }
    }

    /** The test_result event's test name; the HTTP capture buffer is opened with the same value. */
    private static String testName(ExtensionContext ctx) {
        return ctx.getDisplayName();
    }

    /** The test_result event's class name; the HTTP capture buffer is opened with the same value. */
    private static String className(ExtensionContext ctx) {
        return ctx.getTestClass().map(Class::getName).orElse(null);
    }

    private static RunSession run(ExtensionContext ctx) {
        return ctx.getRoot().getStore(NAMESPACE).get(KEY_RUN, RunSession.class);
    }

    private static String stackTraceToString(Throwable t) {
        java.io.StringWriter sw = new java.io.StringWriter();
        t.printStackTrace(new java.io.PrintWriter(sw));
        return sw.toString();
    }
}
