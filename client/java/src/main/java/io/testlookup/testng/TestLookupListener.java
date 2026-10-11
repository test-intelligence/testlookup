package io.testlookup.testng;

import io.testlookup.ConfigLoader;
import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.*;

import org.testng.*;

import java.util.Map;
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
 * Suite parameters &gt; JVM system properties &gt; Environment variables &gt;
 * {@code testlookup.properties} / {@code testlookup.yaml} (filesystem, then classpath).
 * The file keys are the canonical ones: {@code testlookup.build},
 * {@code testlookup.branch}, {@code testlookup.commit}, {@code testlookup.launch},
 * {@code testlookup.suite}, {@code testlookup.release}, {@code testlookup.ca_cert_path},
 * {@code testlookup.insecure}.
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
 *   TESTLOOKUP_LAUNCH       Launch label
 *   TESTLOOKUP_SUITE        Run-level suite (default: launch, then the &lt;suite name&gt;)
 *   TESTLOOKUP_RELEASE      Release name
 *   TESTLOOKUP_CA_CERT      PEM CA bundle to trust (self-signed servers)
 *   TESTLOOKUP_INSECURE     true skips TLS verification (dev only)
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
        // Resolve config: suite params > system props > env vars > config file
        // (ConfigLoader layers the last three).
        Map<String, Object> cfg = ConfigLoader.load();
        String url       = resolve(suite, "testlookup.url",       cfg, "server.url",   null);
        String apiKey    = resolve(suite, "testlookup.apiKey",    cfg, "auth.api_key", null);
        String token     = resolve(suite, "testlookup.token",     cfg, "auth.token",   null);
        String projectId = resolve(suite, "testlookup.projectId", cfg, "project.id",   null);

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
                .framework(ConfigLoader.getString(cfg, "reporting.framework", "testng"));

            // Apply suite-level overrides (Builder values take precedence over ConfigLoader)
            if (url != null)       builder.baseUrl(url);
            if (apiKey != null)    builder.apiKey(apiKey);
            if (token != null)     builder.token(token);
            if (projectId != null) builder.projectId(projectId);

            reporter = builder.build();

            // testlookup.suite preferred, testlookup.launch as the fallback —
            // both flow into SessionOptions.suiteName so every event in the run
            // is stamped with the same suite identifier. When neither is
            // configured via the suite-param/JVM/env channels the listener
            // sees, the TestNG <suite name="..."> value is a sensible last
            // resort — it's the same label the test author put on the run.
            // (Reporter.suiteName from testlookup.properties is also read by
            // the SDK Builder at session-create time and serves as the
            // session-level default downstream; this listener-level fallback
            // closes the gap for runs where testlookup.properties doesn't
            // configure a suite at all.)
            String launch = resolve(suite, "testlookup.launch", cfg, "reporting.launch_name", null);
            String configuredSuiteLocal = resolve(suite, "testlookup.suite", cfg, "reporting.suite_name", null);
            if (configuredSuiteLocal == null) {
                configuredSuiteLocal = launch;
            }
            if (configuredSuiteLocal == null) {
                String testngSuiteName = suite.getName();
                if (testngSuiteName != null && !testngSuiteName.isEmpty()
                        && !"Default suite".equalsIgnoreCase(testngSuiteName)) {
                    configuredSuiteLocal = testngSuiteName;
                }
            }
            this.configuredSuite = configuredSuiteLocal;

            // build / branch / commit / launch from testlookup.properties used to
            // be dropped here: only suite params, -D and env were consulted.
            session = reporter.startSession(
                SessionOptions.builder()
                    .buildNumber(resolve(suite, "testlookup.build",  cfg, "ci.build_number",
                                    "testng-" + System.currentTimeMillis()))
                    .branch(     resolve(suite, "testlookup.branch", cfg, "ci.branch",      null))
                    .commitHash( resolve(suite, "testlookup.commit", cfg, "ci.commit_hash", null))
                    .launchName( launch)
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

        String error = null;
        String stack = null;
        Throwable t  = result.getThrowable();
        if (t != null) {
            error = t.getMessage();
            java.io.StringWriter sw = new java.io.StringWriter();
            t.printStackTrace(new java.io.PrintWriter(sw));
            stack = sw.toString();
        }

        // Per-event ``suite_name`` is set only when the listener could
        // explicitly resolve one from testng.xml ``<parameter>``, JVM
        // property, or env var. When it can't, we deliberately leave it
        // null so ``LiveSession.record()`` falls back to the session-level
        // suite the Reporter resolved from ``testlookup.properties``
        // (``reporting.suite_name`` / ``reporting.launch_name``) and the
        // ``<suite name="...">`` value carried on the session payload.
        // The previous behaviour defaulted to ``result.getTestClass().getName()``
        // when configuredSuite was null, which split a single logical
        // suite into N per-class buckets on /test-management and
        // /coverage/suite (one per test class) even when the user had
        // ``testlookup.launch=My Suite`` in testlookup.properties.
        // ``class_name`` is still captured separately, so the per-class
        // grouping is recoverable without overloading suite_name.
        RecordOptions.Builder optsBuilder = RecordOptions.builder()
            .className(result.getTestClass().getRealClass().getName())
            .error(error)
            .stackTrace(stack);
        if (configuredSuite != null) {
            optsBuilder.suiteName(configuredSuite);
        }

        session.record(testName, status, durationMs, optsBuilder.build());
    }

    /**
     * Resolve a config value with precedence: testng.xml suite parameter >
     * {@link ConfigLoader} (JVM system property > environment variable >
     * config file) > default.
     */
    private static String resolve(ISuite suite, String paramName,
                                  Map<String, Object> cfg, String cfgPath, String defaultVal) {
        String v = suite.getParameter(paramName);
        if (v != null && !v.isEmpty()) return v;
        return ConfigLoader.getString(cfg, cfgPath, defaultVal);
    }
}
