package io.testlookup.http;

import com.fasterxml.jackson.databind.node.ObjectNode;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The HTTP exchanges captured during the current test, one buffer per thread.
 *
 * <p>The framework integration opens the buffer when a test starts and closes it after the test's result is
 * recorded; the capture filter adds wire exchanges (already redacted by {@link HttpRedaction#wireExchange})
 * in between. A test keeps at most {@link HttpRedaction#EXCHANGES_PER_TEST_MAX} exchanges; the rest are
 * counted as dropped. An exchange made while no test is open (a {@code @BeforeAll}, a background thread) is
 * dropped and counted in {@link #droppedOutsideTests()}, never sent.
 *
 * <p>The buffer lives in a {@code ThreadLocal}, never in a JUnit store: JUnit 5.11 closes the test's own
 * store before its TestWatcher callbacks run. No HTTP-client import here, so the SDK loads without
 * RestAssured.
 */
public final class ExchangeBuffer {

    private static final ThreadLocal<Buffer> CURRENT = new ThreadLocal<>();
    private static final AtomicLong DROPPED_OUTSIDE_TESTS = new AtomicLong();

    private ExchangeBuffer() {
    }

    /** One test's captured exchanges, as handed to the live session. */
    public static final class Snapshot {
        public final String testName;
        public final String className;
        /** At most {@link HttpRedaction#EXCHANGES_PER_TEST_MAX}, in capture order; never empty. */
        public final List<ObjectNode> exchanges;
        /** Exchanges captured beyond the cap. */
        public final int dropped;

        Snapshot(String testName, String className, List<ObjectNode> exchanges, int dropped) {
            this.testName = testName;
            this.className = className;
            this.exchanges = Collections.unmodifiableList(exchanges);
            this.dropped = dropped;
        }
    }

    private static final class Buffer {
        final String testName;
        final String className;
        final List<ObjectNode> exchanges = new ArrayList<>();
        int dropped;

        Buffer(String testName, String className) {
            this.testName = testName;
            this.className = className;
        }
    }

    /**
     * Start capturing for a test on this thread. {@code testName} / {@code className} must be exactly what
     * the test's {@code test_result} event sends: the server derives the test case from them. A buffer left
     * open on this thread by an earlier test is discarded.
     */
    public static void open(String testName, String className) {
        CURRENT.set(new Buffer(testName, className));
    }

    /** Whether a test is open on this thread. */
    public static boolean isOpen() {
        return CURRENT.get() != null;
    }

    /**
     * Count one exchange seen while no test was open, without building it (the filter's cheap path when the
     * run has no live session or the call is made outside a test).
     */
    public static void dropOutsideTest() {
        DROPPED_OUTSIDE_TESTS.incrementAndGet();
    }

    /**
     * Add one wire exchange to the current test. {@code null} is ignored; past the cap the exchange is
     * counted as dropped; with no test open it is dropped and counted globally.
     */
    public static void add(ObjectNode wired) {
        if (wired == null) {
            return;
        }
        Buffer buffer = CURRENT.get();
        if (buffer == null) {
            dropOutsideTest();
            return;
        }
        if (buffer.exchanges.size() >= HttpRedaction.EXCHANGES_PER_TEST_MAX) {
            buffer.dropped++;
        } else {
            buffer.exchanges.add(wired);
        }
    }

    /**
     * Stop capturing on this thread: the test's snapshot, or {@code null} when no test was open or nothing
     * was captured (then there is nothing to send).
     */
    public static Snapshot close() {
        Buffer buffer = CURRENT.get();
        CURRENT.remove();
        if (buffer == null || buffer.exchanges.isEmpty()) {
            return null;
        }
        return new Snapshot(buffer.testName, buffer.className, buffer.exchanges, buffer.dropped);
    }

    /** Exchanges dropped because no test was open on their thread, since the JVM started. */
    public static long droppedOutsideTests() {
        return DROPPED_OUTSIDE_TESTS.get();
    }
}
