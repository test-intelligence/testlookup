package io.testlookup.testng;

import io.testlookup.ConfigLoader;
import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;

import org.testng.*;

import java.util.logging.Logger;

/**
 * TestNG Listener — streams test results to TestLookup in real-time.
 *
 * <h3>Simplest usage (testng.xml with suite parameters)</h3>
 * <pre>{@code
 * <suite name="My Suite">
 *   <parameter name="testlookup.url" value="http://localhost:8000"/>
 *   <parameter name="testlookup.apiKey" value="qai_..."/>
 *   <parameter name="testlookup.projectId" value="your-project-uuid"/>
 *   <listeners>
 *     <listener class-name="io.testlookup.testng.TestLookupListener"/>
 *   </listeners>
 *   <test name="API Tests">
 *     <classes>
 *       <class name="com.example.ApiTest"/>
 *     </classes>
 *   </test>
 * </suite>
 * }</pre>
 *
 * <h3>Optional suite parameters</h3>
 * <pre>
 *   testlookup.url          Server base URL       (required)
 *   testlookup.apiKey       API key               (required — or use testlookup.token)
 *   testlookup.token        JWT access token       (alternative to apiKey)
 *   testlookup.projectId    Target project UUID   (required)
 *   testlookup.build        CI build number       (optional)
 *   testlookup.branch       Git branch name       (optional)
 *   testlookup.commit       Git commit SHA        (optional)
 * </pre>
 *
 * <h3>Configuration precedence</h3>
 * Suite parameters &gt; JVM system properties &gt; Environment variables &gt; testlookup.yaml
 *
 * <h3>Environment variable fallbacks</h3>
 * <pre>
 *   TESTLOOKUP_URL          Server base URL
 *   TESTLOOKUP_API_KEY      API key
 *   TESTLOOKUP_TOKEN        JWT access token
 *   TESTLOOKUP_PROJECT_ID   Target project UUID
 *   TESTLOOKUP_BUILD        CI build number
 *   TESTLOOKUP_BRANCH       Git branch name
 *   TESTLOOKUP_COMMIT       Git commit SHA
 * </pre>
 *
 * <h3>JVM system properties (takes precedence over env vars)</h3>
 * <pre>
 *   -Dtestlookup.url=...
 *   -Dtestlookup.apiKey=...
 *   -Dtestlookup.token=...
 *   -Dtestlookup.projectId=...
 *   -Dtestlookup.build=...
 * </pre>
 */
public class TestLookupListener implements ISuiteListener, ITestListener {

    private static final Logger LOG = Logger.getLogger(TestLookupListener.class.getName());

    private TestLookupReporter reporter;
    private LiveSession       session;
    /** Resolved run-level suite (testlookup.suite > testlookup.launch). */
    private String            configuredSuite;

    // ── ISuiteListener ────────────────────────────────────────────────────────

    @Override
    public void onStart(ISuite suite) {
        // Resolve config: suite params > system props > env vars > testlookup.yaml
        String url       = resolve(suite, "testlookup.url",       "TESTLOOKUP_URL",        null);
        String apiKey    = resolve(suite, "testlookup.apiKey",    "TESTLOOKUP_API_KEY",    null);
        String token     = resolve(suite, "testlookup.token",     "TESTLOOKUP_TOKEN",      null);
        String projectId = resolve(suite, "testlookup.projectId", "TESTLOOKUP_PROJECT_ID", null);

        // Skip silently if essential config is missing
        boolean hasAuth = apiKey != null || token != null;
        if (url == null || !hasAuth || projectId == null) {
            if (!ConfigLoader.isConfigured()) {
                LOG.fine("TestLookupListener: no configuration found — skipping");
                return;
            }
        }

        try {
            TestLookupReporter.Builder builder = new TestLookupReporter.Builder()
                .framework("testng");

            // Apply suite-level overrides (Builder values take precedence over ConfigLoader)
            if (url != null)       builder.baseUrl(url);
            if (apiKey != null)    builder.apiKey(apiKey);
            if (token != null)     builder.token(token);
            if (projectId != null) builder.projectId(projectId);

            reporter = builder.build();

            // testlookup.suite preferred, testlookup.launch as the fallback —
            // both flow into SessionOptions.suiteName so every event in the run
            // is stamped with the same suite identifier.
            String configuredSuiteLocal = resolve(suite, "testlookup.suite",  "TESTLOOKUP_SUITE",  null);
            if (configuredSuiteLocal == null) {
                configuredSuiteLocal = resolve(suite, "testlookup.launch", "TESTLOOKUP_LAUNCH", null);
            }
            this.configuredSuite = configuredSuiteLocal;

            session = reporter.startSession(
                SessionOptions.builder()
                    .buildNumber(resolve(suite, "testlookup.build",  "TESTLOOKUP_BUILD",
                                    "testng-" + System.currentTimeMillis()))
                    .branch(     resolve(suite, "testlookup.branch", "TESTLOOKUP_BRANCH", null))
                    .commitHash( resolve(suite, "testlookup.commit", "TESTLOOKUP_COMMIT", null))
                    .launchName( resolve(suite, "testlookup.launch", "TESTLOOKUP_LAUNCH", null))
                    .suiteName(  configuredSuiteLocal)
                    .build()
            );
            LOG.info("TestLookupListener: session started: " + session.getSessionId());
        } catch (Exception e) {
            // Include the exception class so users can distinguish transient
            // connection failures (deploy in progress, hosts file wrong)
            // from configuration errors. Previously the warning collapsed
            // every failure mode into one terse "Request failed: <url>"
            // line that hid the real cause — see 2026-05-15 regression
            // where the SDK couldn't reach the backend during a deploy
            // window and the warning didn't say so.
            String msg = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
            LOG.warning("TestLookupListener: disabled — " + msg);
            // Stack trace at FINE level so users hitting the wall can
            // enable verbose logging without code changes:
            //   -Djava.util.logging.ConsoleHandler.level=FINE
            LOG.log(java.util.logging.Level.FINE, "TestLookupListener startSession() exception", e);
            reporter = null;
            session = null;
        }
    }

    @Override
    public void onFinish(ISuite suite) {
        if (session == null) return;
        session.close();
        LOG.info("TestLookupListener: suite finished — session closed: " + session.getSessionId());
    }

    // ── ITestListener ─────────────────────────────────────────────────────────

    @Override
    public void onTestSuccess(ITestResult result) {
        record(result, TestStatus.PASSED);
    }

    @Override
    public void onTestFailure(ITestResult result) {
        record(result, TestStatus.FAILED);
    }

    @Override
    public void onTestSkipped(ITestResult result) {
        record(result, TestStatus.SKIPPED);
    }

    @Override
    public void onTestFailedButWithinSuccessPercentage(ITestResult result) {
        record(result, TestStatus.BROKEN);
    }

    @Override
    public void onTestFailedWithTimeout(ITestResult result) {
        record(result, TestStatus.BROKEN);
    }

    // These are required by the interface but we have nothing to do here
    @Override public void onStart(ITestContext context) {}
    @Override public void onFinish(ITestContext context) {}
    @Override public void onTestStart(ITestResult result) {}

    // ── Helper ────────────────────────────────────────────────────────────────

    private void record(ITestResult result, TestStatus status) {
        if (session == null) return;

        long durationMs = result.getEndMillis() - result.getStartMillis();
        String testName  = result.getMethod().getMethodName();
        // User-configured suite wins; otherwise fall back to the test class name.
        String suiteName = configuredSuite != null
            ? configuredSuite
            : result.getTestClass().getName();

        String error = null;
        String stack = null;
        Throwable t  = result.getThrowable();
        if (t != null) {
            error = t.getMessage();
            java.io.StringWriter sw = new java.io.StringWriter();
            t.printStackTrace(new java.io.PrintWriter(sw));
            stack = sw.toString();
        }

        RecordOptions opts = RecordOptions.builder()
            .suiteName(suiteName)
            .className(result.getTestClass().getRealClass().getName())
            .error(error)
            .stackTrace(stack)
            .build();

        session.record(testName, status, durationMs, opts);
    }

    /**
     * Resolve a config value with precedence:
     * suite parameter > JVM system property > environment variable > default.
     */
    private static String resolve(ISuite suite, String paramName, String envVar, String defaultVal) {
        // 1. Suite parameter (highest precedence for testng.xml config)
        String v = suite.getParameter(paramName);
        if (v != null && !v.isEmpty()) return v;

        // 2. JVM system property
        v = System.getProperty(paramName);
        if (v != null && !v.isEmpty()) return v;

        // 3. Environment variable
        if (envVar != null) {
            v = System.getenv(envVar);
            if (v != null && !v.isEmpty()) return v;
        }

        return defaultVal;
    }
}
