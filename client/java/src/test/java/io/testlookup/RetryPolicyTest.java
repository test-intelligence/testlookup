package io.testlookup;

import org.junit.jupiter.api.Test;

import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests the Java SDK's retry-policy alignment (Phase 4.6). Mirrors
 * {@code test_python_sdk_retry_policy.py} so both clients honour the
 * same contract:
 *
 * <ol>
 *   <li>HTTP 429 and 503 are retryable; other 4xx/5xx are not.</li>
 *   <li>{@code Retry-After} is honoured exactly (no doubling, no
 *       halving). Integer delta-seconds and HTTP-date forms both work.</li>
 *   <li>Cumulative retry time is capped at
 *       {@link TestLookupReporter#MAX_RETRY_TOTAL_MS} (5 min). When the
 *       header (or backoff) would push past the cap, the policy
 *       returns {@code -1} so the caller surfaces a hard failure.</li>
 * </ol>
 *
 * The retry-loop integration with {@code LiveSession.postBatch} relies
 * on the same helper, so these tests transitively pin the end-to-end
 * behaviour. We don't need a MockWebServer dependency just to verify
 * the decision rules.
 */
public class RetryPolicyTest {

    // ── isRetryableStatus ─────────────────────────────────────────────────────

    @Test
    void retryable_statuses_are_only_429_and_503() {
        assertTrue(TestLookupReporter.RetryPolicy.isRetryableStatus(429));
        assertTrue(TestLookupReporter.RetryPolicy.isRetryableStatus(503));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(200));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(400));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(401));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(404));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(500));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(502));
        assertFalse(TestLookupReporter.RetryPolicy.isRetryableStatus(504));
    }

    // ── parseRetryAfterMs ─────────────────────────────────────────────────────

    @Test
    void parses_integer_seconds() {
        assertEquals(30_000L, TestLookupReporter.RetryPolicy.parseRetryAfterMs("30"));
        assertEquals(0L,      TestLookupReporter.RetryPolicy.parseRetryAfterMs("0"));
        assertEquals(5_000L,  TestLookupReporter.RetryPolicy.parseRetryAfterMs("  5  "));
    }

    @Test
    void parses_http_date() {
        // Pick a wall-clock 10 seconds in the future. parseRetryAfterMs
        // returns the delta to "now" so we expect ~10_000ms — give it a
        // small slack for parsing + clock jitter.
        ZonedDateTime future = ZonedDateTime.now().plusSeconds(10);
        String header = future.format(DateTimeFormatter.RFC_1123_DATE_TIME);
        long parsed = TestLookupReporter.RetryPolicy.parseRetryAfterMs(header);
        assertTrue(parsed >= 8_000L && parsed <= 12_000L,
            "expected ~10s, got " + parsed + "ms");
    }

    @Test
    void returns_negative_for_missing_or_unparseable() {
        assertEquals(-1L, TestLookupReporter.RetryPolicy.parseRetryAfterMs(null));
        assertEquals(-1L, TestLookupReporter.RetryPolicy.parseRetryAfterMs(""));
        assertEquals(-1L, TestLookupReporter.RetryPolicy.parseRetryAfterMs("   "));
        assertEquals(-1L, TestLookupReporter.RetryPolicy.parseRetryAfterMs("not-a-date"));
    }

    // ── computeNextDelayMs ────────────────────────────────────────────────────

    @Test
    void honours_retry_after_exactly_no_doubling_no_halving() {
        long base = 500L;
        long cap = TestLookupReporter.MAX_RETRY_TOTAL_MS;
        // Retry-After: 30 → exactly 30_000ms, not base*2^(attempt-1).
        long delay = TestLookupReporter.RetryPolicy.computeNextDelayMs(
            "30", /*attempt*/ 1, /*elapsedMs*/ 0L, base, cap);
        assertEquals(30_000L, delay);
        // Attempt does not change the answer when Retry-After is set.
        long delay2 = TestLookupReporter.RetryPolicy.computeNextDelayMs(
            "30", /*attempt*/ 4, /*elapsedMs*/ 0L, base, cap);
        assertEquals(30_000L, delay2);
    }

    @Test
    void falls_back_to_exponential_without_header() {
        long base = 500L;
        long cap = TestLookupReporter.MAX_RETRY_TOTAL_MS;
        assertEquals(500L,  TestLookupReporter.RetryPolicy.computeNextDelayMs(null, 1, 0L, base, cap));
        assertEquals(1000L, TestLookupReporter.RetryPolicy.computeNextDelayMs(null, 2, 0L, base, cap));
        assertEquals(2000L, TestLookupReporter.RetryPolicy.computeNextDelayMs(null, 3, 0L, base, cap));
        assertEquals(4000L, TestLookupReporter.RetryPolicy.computeNextDelayMs(null, 4, 0L, base, cap));
    }

    @Test
    void returns_negative_when_already_past_cap() {
        long base = 500L;
        long cap = TestLookupReporter.MAX_RETRY_TOTAL_MS;
        long delay = TestLookupReporter.RetryPolicy.computeNextDelayMs(
            "30", 2, cap + 1L, base, cap);
        assertEquals(-1L, delay,
            "policy must surface hard failure once the retry budget is blown");
    }

    @Test
    void returns_negative_when_delay_would_overflow_cap() {
        // Retry-After: 9999s against a 300s cap → don't sleep, fail
        // immediately. The server is asking for longer than we'll tolerate.
        long base = 500L;
        long cap = TestLookupReporter.MAX_RETRY_TOTAL_MS;
        long delay = TestLookupReporter.RetryPolicy.computeNextDelayMs(
            "9999", 1, 0L, base, cap);
        assertEquals(-1L, delay);
    }

    @Test
    void respects_remaining_budget_for_backoff_too() {
        // Exponential delay at attempt=10 is 500 * 2^9 = 256_000ms, well
        // under the cap from a fresh start, but if we've already burned
        // 290s, the 256s next-delay overflows and we should bail.
        long base = 500L;
        long cap = TestLookupReporter.MAX_RETRY_TOTAL_MS;
        long delay = TestLookupReporter.RetryPolicy.computeNextDelayMs(
            null, 10, 290_000L, base, cap);
        assertEquals(-1L, delay);
    }
}
