/*
 * E2E validation harness — pushes a small, deterministic test run via
 * TestLookupReporter so an operator can verify the full feature
 * surface (runs page, search, suites, dashboard, AI pipeline) after a
 * deploy. Not wired into any build target; invoked manually:
 *
 *   java -cp client/java/target/testlookup-reporter-1.0.0-all.jar \
 *        client/java/examples/E2EValidation.java <endpoint> <apiKey> <projectId>
 *
 * (Java 21+ supports single-file source-launcher mode, so no compile step
 * is required — the JAR ends up on the classpath through the -cp flag.)
 */
import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.RecordOptions;
import io.testlookup.TestLookupReporter.SessionOptions;
import io.testlookup.TestLookupReporter.TestStatus;
import io.testlookup.TestLookupReporter.LiveSession;
import java.util.List;
import java.util.UUID;

public class E2EValidation {

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println(
                "Usage: java E2EValidation <endpoint> <apiKey> <projectId>"
            );
            System.exit(2);
        }
        String endpoint  = args[0];
        String apiKey    = args[1];
        String projectId = args[2];

        // Build number is unique per invocation so this script is safe to
        // re-run without colliding with a prior validation run.
        String buildNumber = "e2e-" + UUID.randomUUID().toString().substring(0, 8);
        System.out.println("buildNumber=" + buildNumber);

        TestLookupReporter reporter = new TestLookupReporter.Builder()
            .baseUrl(endpoint)
            .apiKey(apiKey)
            .projectId(projectId)
            .clientName("e2e-validation")
            .framework("inline")
            .build();

        // The published fat JAR predates the source's session-level
        // suiteName/releaseName setters; per-test suite_name flows in
        // through RecordOptions.suiteName below, which the JAR supports.
        SessionOptions opts = SessionOptions.builder()
            .buildNumber(buildNumber)
            .branch("main")
            .commitHash("deadbeef")
            .totalTests(4)
            .launchName("E2E validation run")
            .build();

        try (LiveSession session = reporter.startSession(opts)) {
            System.out.println("sessionId=" + session.getSessionId());
            System.out.println("runId="     + session.getRunId());

            RecordOptions smokeSuite = RecordOptions.builder()
                .suiteName("e2e-smoke")
                .build();
            session.record("e2e_login_works",     TestStatus.PASSED, 120, smokeSuite);
            session.record("e2e_dashboard_loads", TestStatus.PASSED, 210, smokeSuite);
            session.record("e2e_search_returns",  TestStatus.PASSED, 305, smokeSuite);
            session.record(
                "e2e_failure_path_visible",
                TestStatus.FAILED,
                440,
                RecordOptions.builder()
                    .suiteName("e2e-smoke")
                    .error("AssertionError: expected 200, got 500")
                    .tags(List.of("regression", "smoke"))
                    .build()
            );
            // close() is invoked by try-with-resources, which flushes the
            // batched events and sends the run_complete sentinel.
        }
        System.out.println("done");
    }
}
