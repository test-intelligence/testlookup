package io.testlookup.junit5;

import com.fasterxml.jackson.databind.JsonNode;
import io.testlookup.FakeTestLookupServer;
import io.testlookup.TestConfigScope;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.platform.engine.discovery.DiscoverySelectors.selectClass;
import static org.junit.platform.launcher.EngineFilter.includeEngines;

/**
 * The JUnit 5 extension, driven by a real Jupiter engine.
 *
 * <p>It used to open a session in each class's {@code beforeAll} and close it
 * in {@code afterAll}: a run of N test classes reached TestLookup as N separate
 * runs. Every result carried {@code duration_ms = 0} -- and on JUnit 5.11 no
 * result was recorded at all (see {@link #everyResultIsRecorded}). Build, branch,
 * commit and launch were read only from {@code -D} and the environment, so the
 * same keys in {@code testlookup.properties} did nothing.
 *
 * <p>The sample classes below are static nested classes, which Surefire
 * excludes; only the launcher runs in this file execute them.
 */
class TestLookupExtensionRunTest {

    @ExtendWith(TestLookupExtension.class)
    static class CartSample {
        @Test void addsAnItem() throws InterruptedException { Thread.sleep(120); }
        @Test void emptiesTheCart() { }
    }

    @ExtendWith(TestLookupExtension.class)
    static class CheckoutSample {
        @Test void paysByCard() { }
        @Test @Disabled("pending rollout") void paysByVoucher() { }
    }

    private FakeTestLookupServer server;

    @BeforeEach
    void startServer() throws Exception {
        server = FakeTestLookupServer.http();
    }

    @AfterEach
    void stopServer() {
        server.close();
    }

    /** One JUnit run of both sample classes, as Maven or Gradle would start it. */
    private void runSamples() {
        LauncherFactory.create().execute(LauncherDiscoveryRequestBuilder.request()
            .selectors(selectClass(CartSample.class), selectClass(CheckoutSample.class))
            .filters(includeEngines("junit-jupiter"))
            .build());
    }

    private Map<String, String> connection() {
        return Map.of(
            "testlookup.url", server.baseUrl(),
            "testlookup.token", "jwt-test",
            "testlookup.projectId", "proj-1");
    }

    @Test
    @DisplayName("every test's result reaches TestLookup")
    void everyResultIsRecorded() throws Exception {
        // On JUnit 5.11 (the version this pom declares) the old extension
        // recorded nothing: its TestWatcher callbacks looked the session up
        // through the test's own store, which JUnit closes before calling
        // them, so every lookup threw "A NamespacedHierarchicalStore cannot be
        // modified or queried after it has been closed" and JUnit only logged it.
        try (TestConfigScope ignored = new TestConfigScope().sysProps(connection())) {
            runSamples();
        }

        List<String> results = new java.util.ArrayList<>(server.results());
        java.util.Collections.sort(results);
        assertEquals(List.of(
            "addsAnItem():PASSED", "emptiesTheCart():PASSED",
            "paysByCard():PASSED", "paysByVoucher():SKIPPED"), results);
    }

    @Test
    @DisplayName("one session covers the whole run, not one per test class")
    void oneSessionPerRun() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope().sysProps(connection())) {
            runSamples();
        }

        assertEquals(1, server.sessionCreates.size(), "sessions opened");
        assertEquals(1, server.closes.size(), "sessions closed");
        List<String> results = server.results();
        assertEquals(4, results.size(), results.toString());
        assertTrue(results.contains("paysByVoucher():SKIPPED"), results.toString());
    }

    @Test
    @DisplayName("each result carries the test's measured duration")
    void durationIsMeasured() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope().sysProps(connection())) {
            runSamples();
        }

        JsonNode slow = server.result("addsAnItem()");
        assertNotNull(slow, server.results().toString());
        long ms = slow.path("duration_ms").asLong();
        assertTrue(ms >= 100 && ms < 30_000, "duration_ms=" + ms);
    }

    @Test
    @DisplayName("build, branch, commit, launch, suite and release come from testlookup.properties")
    void propertiesFileReachesTheSession(@TempDir Path dir) throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .sysProps(connection())
                .classpathProperties(dir, Map.of(
                    "testlookup.build", "42",
                    "testlookup.branch", "main",
                    "testlookup.commit", "abc1234",
                    "testlookup.launch", "Nightly",
                    "testlookup.release", "2026.10"))) {
            runSamples();
        }

        JsonNode create = server.sessionCreates.get(0);
        assertEquals("42", create.path("build_number").asText());
        assertEquals("main", create.path("branch").asText());
        assertEquals("abc1234", create.path("commit_hash").asText());
        assertEquals("Nightly", create.path("launch_name").asText());
        assertEquals("Nightly", create.path("suite_name").asText(), "launch is the suite fallback");
        assertEquals("2026.10", create.path("release_name").asText());
    }

    @Test
    @DisplayName("a JVM property beats the properties file")
    void sysPropBeatsTheFile(@TempDir Path dir) throws Exception {
        java.util.HashMap<String, String> props = new java.util.HashMap<>(connection());
        props.put("testlookup.build", "from-jvm");
        try (TestConfigScope ignored = new TestConfigScope()
                .sysProps(props)
                .classpathProperties(dir, Map.of("testlookup.build", "42"))) {
            runSamples();
        }

        assertEquals("from-jvm", server.sessionCreates.get(0).path("build_number").asText());
    }
}
