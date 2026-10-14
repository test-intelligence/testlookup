package io.testlookup.testng;

import com.fasterxml.jackson.databind.JsonNode;
import io.testlookup.FakeTestLookupServer;
import io.testlookup.TestConfigScope;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.testng.TestNG;
import org.testng.xml.XmlClass;
import org.testng.xml.XmlSuite;
import org.testng.xml.XmlTest;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** EXEC-01: a TestNG result carries the start TestNG stamped on it, from a real in-process run. */
class TestLookupListenerTimingTest {

    /** Run only by the TestNG instance below; Surefire runs JUnit here. */
    public static class SlowSample {
        @org.testng.annotations.Test public void exportsReport() throws InterruptedException { Thread.sleep(80); }
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

    @Test
    @DisplayName("the result carries when the test method started")
    void startIsSent(@TempDir Path dir) throws Exception {
        long before = System.currentTimeMillis();
        try (TestConfigScope ignored = new TestConfigScope().sysProps(Map.of(
                "testlookup.url", server.baseUrl(),
                "testlookup.token", "jwt-test",
                "testlookup.projectId", "proj-1"))) {
            XmlSuite suite = new XmlSuite();
            suite.setName("Reports");
            XmlTest test = new XmlTest(suite);
            test.setName("reports");
            test.setXmlClasses(List.of(new XmlClass(SlowSample.class)));
            TestNG testng = new TestNG(false);
            testng.setOutputDirectory(dir.resolve("out").toString());
            testng.setXmlSuites(List.of(suite));
            testng.addListener(new TestLookupListener());
            testng.run();
        }

        JsonNode result = server.result("exportsReport");
        assertNotNull(result, server.results().toString());
        long started = result.path("started_at_ms").asLong(-1);
        long duration = result.path("duration_ms").asLong(-1);
        assertTrue(started >= before, "started_at_ms=" + started + " before=" + before);
        assertTrue(duration >= 70, "duration_ms=" + duration);
        assertTrue(result.path("timestamp_ms").asLong() - started >= duration, result.toString());
    }
}
