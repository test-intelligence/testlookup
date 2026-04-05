package io.testlookup.junit5;

import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;

import org.junit.jupiter.api.extension.*;

import java.util.Optional;
import java.util.logging.Logger;

/**
 * JUnit 5 Extension — streams test results to TestLookup in real-time.
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
 * <h3>Global registration (src/test/resources/META-INF/services)</h3>
 * Add a file {@code org.junit.jupiter.api.extension.Extension} containing:
 * <pre>ai.testlookup.junit5.TestLookupExtension</pre>
 *
 * <h3>Configuration via environment variables</h3>
 * <pre>
 *   TESTLOOKUP_URL          Server base URL     (required)
 *   TESTLOOKUP_TOKEN        JWT access token    (required)
 *   TESTLOOKUP_PROJECT_ID   Target project UUID (required)
 *   TESTLOOKUP_BUILD        CI build number     (optional)
 *   TESTLOOKUP_BRANCH       Git branch name     (optional)
 *   TESTLOOKUP_COMMIT       Git commit SHA      (optional)
 * </pre>
 *
 * <h3>Configuration via JVM system properties (takes precedence)</h3>
 * <pre>
 *   -Dtestlookup.url=...
 *   -Dtestlookup.token=...
 *   -Dtestlookup.projectId=...
 *   -Dtestlookup.build=...
 *   -Dtestlookup.branch=...
 * </pre>
 */
public class TestLookupExtension
        implements BeforeAllCallback, AfterAllCallback, TestWatcher {

    private static final Logger LOG = Logger.getLogger(TestLookupExtension.class.getName());
    private static final String NS  = "ai.testlookup";

    // One session per top-level test class (stored in JUnit's ExtensionContext store)
    private static final ExtensionContext.Namespace NAMESPACE =
        ExtensionContext.Namespace.create(NS);

    private static final String KEY_SESSION  = "session";
    private static final String KEY_REPORTER = "reporter";

    // ── BeforeAllCallback ─────────────────────────────────────────────────────

    @Override
    public void beforeAll(ExtensionContext ctx) {
        String url       = prop("testlookup.url",       "TESTLOOKUP_URL");
        String token     = prop("testlookup.token",     "TESTLOOKUP_TOKEN");
        String projectId = prop("testlookup.projectId", "TESTLOOKUP_PROJECT_ID");

        if (url.isEmpty() || token.isEmpty() || projectId.isEmpty()) {
            LOG.warning("TestLookupExtension: disabled — missing URL, token, or projectId");
            return;
        }

        TestLookupReporter reporter = new TestLookupReporter.Builder()
            .baseUrl(url)
            .token(token)
            .projectId(projectId)
            .framework("junit5")
            .build();

        try {
            LiveSession session = reporter.startSession(
                SessionOptions.builder()
                    .buildNumber(prop("testlookup.build",  "TESTLOOKUP_BUILD",
                                    "junit5-" + System.currentTimeMillis()))
                    .branch(     nullable("testlookup.branch", "TESTLOOKUP_BRANCH"))
                    .commitHash( nullable("testlookup.commit", "TESTLOOKUP_COMMIT"))
                    .build()
            );

            var store = ctx.getStore(NAMESPACE);
            store.put(KEY_REPORTER, reporter);
            store.put(KEY_SESSION,  session);
            LOG.info("TestLookupExtension: session started: " + session.getSessionId());
        } catch (Exception e) {
            LOG.warning("TestLookupExtension: failed to start session: " + e.getMessage());
        }
    }

    // ── AfterAllCallback ──────────────────────────────────────────────────────

    @Override
    public void afterAll(ExtensionContext ctx) {
        LiveSession session = getSession(ctx);
        if (session == null) return;
        session.close();
        LOG.info("TestLookupExtension: session closed: " + session.getSessionId());
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
        LiveSession session = getSession(ctx);
        if (session == null) return;

        String testName  = ctx.getDisplayName();
        String suiteName = ctx.getParent().map(ExtensionContext::getDisplayName).orElse(null);

        RecordOptions opts = RecordOptions.builder()
            .suiteName(suiteName)
            .className(ctx.getTestClass().map(Class::getName).orElse(null))
            .error(error)
            .stackTrace(stack)
            .build();

        session.record(testName, status, 0L, opts);
    }

    private LiveSession getSession(ExtensionContext ctx) {
        // Walk up to root to find the session (set in beforeAll of outermost class)
        ExtensionContext current = ctx;
        while (current != null) {
            LiveSession s = (LiveSession) current.getStore(NAMESPACE).get(KEY_SESSION);
            if (s != null) return s;
            current = current.getParent().orElse(null);
        }
        return null;
    }

    private static String prop(String sysProp, String envVar) {
        String v = System.getProperty(sysProp);
        if (v != null && !v.isEmpty()) return v;
        v = System.getenv(envVar);
        return v != null ? v : "";
    }

    private static String prop(String sysProp, String envVar, String defaultVal) {
        String v = prop(sysProp, envVar);
        return v.isEmpty() ? defaultVal : v;
    }

    private static String nullable(String sysProp, String envVar) {
        String v = prop(sysProp, envVar);
        return v.isEmpty() ? null : v;
    }

    private static String stackTraceToString(Throwable t) {
        java.io.StringWriter sw = new java.io.StringWriter();
        t.printStackTrace(new java.io.PrintWriter(sw));
        return sw.toString();
    }
}
