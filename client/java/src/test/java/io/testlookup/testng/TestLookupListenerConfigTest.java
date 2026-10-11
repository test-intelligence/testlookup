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

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * The TestNG listener, driven by a real in-process TestNG run.
 *
 * <p>Its {@code resolve()} read suite parameters, {@code -D} and environment
 * variables only. The README and sample put build / branch / commit / launch
 * in {@code testlookup.properties}, where the listener never looked: the
 * session got {@code testng-<millis>}, no branch, no commit, no launch label.
 */
class TestLookupListenerConfigTest {

    /** Run only by the TestNG instances below; Surefire runs JUnit here. */
    public static class Sample {
        @org.testng.annotations.Test public void logsIn() { }
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

    private void runTestNg(Path outputDir, Map<String, String> suiteParams) {
        XmlSuite suite = new XmlSuite();
        suite.setName("Checkout suite");
        suite.setParameters(suiteParams);
        XmlTest test = new XmlTest(suite);
        test.setName("checkout");
        test.setXmlClasses(List.of(new XmlClass(Sample.class)));

        TestNG testng = new TestNG(false);
        testng.setOutputDirectory(outputDir.toString());
        testng.setXmlSuites(List.of(suite));
        testng.addListener(new TestLookupListener());
        testng.run();
    }

    private TestConfigScope config(Path dir) throws Exception {
        return new TestConfigScope()
            .sysProps(Map.of(
                "testlookup.url", server.baseUrl(),
                "testlookup.token", "jwt-test",
                "testlookup.projectId", "proj-1"))
            .classpathProperties(dir, Map.of(
                "testlookup.build", "42",
                "testlookup.branch", "main",
                "testlookup.commit", "abc1234",
                "testlookup.launch", "Nightly",
                "testlookup.release", "2026.10"));
    }

    @Test
    @DisplayName("build, branch, commit and launch come from testlookup.properties")
    void propertiesFileReachesTheSession(@TempDir Path dir) throws Exception {
        try (TestConfigScope ignored = config(dir)) {
            runTestNg(dir.resolve("out"), Map.of());
        }

        assertEquals(1, server.sessionCreates.size());
        JsonNode create = server.sessionCreates.get(0);
        assertEquals("42", create.path("build_number").asText());
        assertEquals("main", create.path("branch").asText());
        assertEquals("abc1234", create.path("commit_hash").asText());
        assertEquals("Nightly", create.path("launch_name").asText());
        assertEquals("Nightly", create.path("suite_name").asText());
        assertEquals("2026.10", create.path("release_name").asText());
        assertEquals(List.of("logsIn:PASSED"), server.results());
    }

    @Test
    @DisplayName("a testng.xml suite parameter still beats the properties file")
    void suiteParameterBeatsTheFile(@TempDir Path dir) throws Exception {
        try (TestConfigScope ignored = config(dir)) {
            runTestNg(dir.resolve("out"), Map.of("testlookup.build", "from-xml", "testlookup.launch", "Xml launch"));
        }

        JsonNode create = server.sessionCreates.get(0);
        assertEquals("from-xml", create.path("build_number").asText());
        assertEquals("Xml launch", create.path("launch_name").asText());
    }
}
