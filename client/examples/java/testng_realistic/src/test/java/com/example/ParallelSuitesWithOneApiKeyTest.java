package com.example;

import io.testlookup.TestLookupReporter;
import io.testlookup.TestLookupReporter.LiveSession;
import io.testlookup.TestLookupReporter.RecordOptions;
import io.testlookup.TestLookupReporter.SessionOptions;
import io.testlookup.TestLookupReporter.TestStatus;

import org.testng.Assert;
import org.testng.annotations.Test;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.Collectors;

public class ParallelSuitesWithOneApiKeyTest {

    private static final Map<String, List<SimulatedCase>> SUITES = Map.of(
        "auth-api", Arrays.asList(
            new SimulatedCase("login_accepts_valid_credentials", true, 92),
            new SimulatedCase("login_rejects_locked_account", true, 74),
            new SimulatedCase("refresh_token_rotates", true, 61)
        ),
        "checkout-api", Arrays.asList(
            new SimulatedCase("cart_total_includes_tax", true, 113),
            new SimulatedCase("expired_coupon_is_rejected", true, 85),
            new SimulatedCase("payment_gateway_timeout_is_retried", false, 247)
        ),
        "orders-ui", Arrays.asList(
            new SimulatedCase("order_history_loads", true, 174),
            new SimulatedCase("order_detail_shows_tracking", true, 143),
            new SimulatedCase("cancel_button_hidden_after_ship", true, 96)
        ),
        "notifications", Arrays.asList(
            new SimulatedCase("email_receipt_sent", true, 126),
            new SimulatedCase("sms_opt_out_is_honored", true, 108),
            new SimulatedCase("webhook_signature_is_verified", true, 88)
        )
    );

    @Test
    public void runMultipleSuitesInParallelWithOneApiKey() throws Exception {
        TestLookupReporter reporter = new TestLookupReporter.Builder()
            .framework("testng-programmatic")
            .build();
        String buildId = "testng-parallel-" + UUID.randomUUID().toString().substring(0, 8);

        ExecutorService executor = Executors.newFixedThreadPool(SUITES.size());
        try {
            List<Callable<Long>> jobs = SUITES.entrySet().stream()
                .<Callable<Long>>map(entry -> () -> runSuite(reporter, buildId, entry.getKey(), entry.getValue()))
                .collect(Collectors.toList());

            long sent = 0;
            for (Future<Long> future : executor.invokeAll(jobs)) {
                sent += future.get();
            }

            Assert.assertTrue(sent >= 12, "all parallel suite events should be accepted");
        } finally {
            executor.shutdownNow();
        }
    }

    private long runSuite(
        TestLookupReporter reporter,
        String buildId,
        String suiteName,
        List<SimulatedCase> cases
    ) throws Exception {
        LiveSession session = reporter.startSession(
            SessionOptions.builder()
                .buildNumber(buildId)
                .launchName("Parallel suites - " + buildId)
                .totalTests(cases.size())
                .build()
        );
        try {
            for (SimulatedCase testCase : cases) {
                Thread.sleep(Math.max(1, testCase.durationMs / 20));
                if (testCase.shouldPass) {
                    session.record(
                        testCase.name,
                        TestStatus.PASSED,
                        testCase.durationMs,
                        RecordOptions.builder()
                            .suiteName(suiteName)
                            .className("ParallelSuitesWithOneApiKeyTest")
                            .tags(Arrays.asList("parallel", "regression"))
                            .metadata(metadata("worker", suiteName, "expected_result", "business rule holds"))
                            .build()
                    );
                } else {
                    session.record(
                        testCase.name,
                        TestStatus.FAILED,
                        testCase.durationMs,
                        RecordOptions.builder()
                            .suiteName(suiteName)
                            .className("ParallelSuitesWithOneApiKeyTest")
                            .error("AssertionError: expected retry_count <= 2, got 4")
                            .stackTrace("checkout.retry_policy: expected retry_count <= 2, got 4")
                            .tags(Arrays.asList("parallel", "regression", "known-risk"))
                            .metadata(metadata(
                                "worker", suiteName,
                                "assertion", "retry_count <= 2",
                                "expected_result", "gateway timeout is retried at most twice",
                                "actual_result", "gateway timeout retried four times"
                            ))
                            .build()
                    );
                }
            }
        } finally {
            session.close();
        }
        return session.getSentCount();
    }

    private static Map<String, Object> metadata(String... pairs) {
        Map<String, Object> values = new LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            values.put(pairs[i], pairs[i + 1]);
        }
        return values;
    }

    private static final class SimulatedCase {
        final String name;
        final boolean shouldPass;
        final long durationMs;

        SimulatedCase(String name, boolean shouldPass, long durationMs) {
            this.name = name;
            this.shouldPass = shouldPass;
            this.durationMs = durationMs;
        }
    }
}
