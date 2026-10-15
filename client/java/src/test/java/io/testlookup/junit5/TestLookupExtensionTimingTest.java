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
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.platform.engine.discovery.DiscoverySelectors.selectClass;
import static org.junit.platform.launcher.EngineFilter.includeEngines;

/**
 * EXEC-01: each result carries when the test began and how long its
 * {@code @BeforeEach}, body and {@code @AfterEach} took, measured by a real
 * Jupiter engine run. {@code timestamp_ms} stays the moment the result was
 * recorded, i.e. the finish.
 */
class TestLookupExtensionTimingTest {

    @ExtendWith(TestLookupExtension.class)
    static class FixtureSample {
        @BeforeEach void openBrowser() throws InterruptedException { Thread.sleep(60); }
        @AfterEach void closeBrowser() throws InterruptedException { Thread.sleep(40); }
        @Test void checksOut() throws InterruptedException { Thread.sleep(120); }
        @Test @Disabled("not yet") void refunds() { }
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

    private void runSample() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope().sysProps(Map.of(
                "testlookup.url", server.baseUrl(),
                "testlookup.token", "jwt-test",
                "testlookup.projectId", "proj-1"))) {
            LauncherFactory.create().execute(LauncherDiscoveryRequestBuilder.request()
                .selectors(selectClass(FixtureSample.class))
                .filters(includeEngines("junit-jupiter"))
                .build());
        }
    }

    @Test
    @DisplayName("a result carries its start and its setup / body / teardown split")
    void phasesAreMeasured() throws Exception {
        long before = System.currentTimeMillis();
        runSample();

        JsonNode ran = server.result("checksOut()");
        assertNotNull(ran, server.results().toString());
        long setup = ran.path("setup_ms").asLong(-1);
        long call = ran.path("call_ms").asLong(-1);
        long teardown = ran.path("teardown_ms").asLong(-1);
        assertTrue(setup >= 50 && setup < 30_000, "setup_ms=" + setup);
        assertTrue(call >= 100 && call < 30_000, "call_ms=" + call);
        assertTrue(teardown >= 30 && teardown < 30_000, "teardown_ms=" + teardown);
        assertTrue(call == ran.path("duration_ms").asLong(), "duration_ms keeps meaning the body");

        long started = ran.path("started_at_ms").asLong(-1);
        long finished = ran.path("timestamp_ms").asLong(-1);
        assertTrue(started >= before, "started_at_ms=" + started + " before=" + before);
        assertTrue(finished - started >= setup + call, "start " + started + " finish " + finished);
    }

    @Test
    @DisplayName("a disabled test, which never ran, sends no timing")
    void disabledTestHasNoTiming() throws Exception {
        runSample();

        JsonNode skipped = server.result("refunds()");
        assertNotNull(skipped, server.results().toString());
        for (String key : new String[] {"started_at_ms", "setup_ms", "call_ms", "teardown_ms"}) {
            assertFalse(skipped.has(key), key + " on a disabled test: " + skipped);
        }
    }
}
