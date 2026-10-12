package io.testlookup;

import io.testlookup.testng.TestLookupListener;

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
import java.util.Properties;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Credentials resolve as a pair, the same way in every SDK: an explicit token
 * or key beats any configured one; configured ones go layer by layer (JVM
 * property, then environment, then file); within a layer the API key wins;
 * and a {@code qai_} value is an API key whichever option carried it.
 *
 * <p>Builder.build() filled the token and the key independently from the
 * merged config, so an explicit {@code .token(jwt)} or a testng.xml
 * {@code testlookup.token} lost to any API key in testlookup.properties.
 */
class CredentialResolutionTest {

    /** Shaped like a minted key; not a key. */
    private static final String FAKE_KEY = "qai_" + "x".repeat(43);
    private static final String FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.e30.not-a-real-signature";

    public static class Sample {
        @org.testng.annotations.Test public void logsIn() { }
    }

    @TempDir Path dir;
    private FakeTestLookupServer server;

    @BeforeEach
    void startServer() throws Exception {
        server = FakeTestLookupServer.http();
    }

    @AfterEach
    void stopServer() {
        server.close();
    }

    private void openAndClose(TestLookupReporter r) throws Exception {
        try (TestLookupReporter.LiveSession s = r.startSession("b-1")) {
            s.record("t", TestLookupReporter.TestStatus.PASSED, 1);
        }
    }

    private TestLookupReporter.Builder reporter() {
        return new TestLookupReporter.Builder().baseUrl(server.baseUrl()).projectId("proj-1");
    }

    @Test
    @DisplayName("an explicit token beats an API key in testlookup.properties")
    void explicitTokenBeatsFileKey() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .classpathProperties(dir, Map.of("testlookup.api.key", FAKE_KEY))) {
            openAndClose(reporter().token(FAKE_JWT).build());
        }
        assertEquals(List.of("Bearer " + FAKE_JWT), server.sessionCreateAuth);
    }

    @Test
    @DisplayName("a token as a JVM property beats an API key in the file")
    void sysPropTokenBeatsFileKey() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .sysProps(Map.of("testlookup.token", FAKE_JWT))
                .classpathProperties(dir, Map.of("testlookup.api.key", "qai_REPLACE_ME"))) {
            openAndClose(reporter().build());
        }
        assertEquals(List.of("Bearer " + FAKE_JWT), server.sessionCreateAuth);
    }

    @Test
    @DisplayName("a token in the environment beats an API key in the file")
    void envTokenBeatsFileKey() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .classpathProperties(dir, Map.of("testlookup.api.key", "qai_REPLACE_ME"))) {
            String[] pair = ConfigLoader.credentials(Map.of("TESTLOOKUP_TOKEN", FAKE_JWT), new Properties());
            assertArrayEquals(new String[] { FAKE_JWT, null }, pair);
        }
    }

    @Test
    @DisplayName("within one layer the API key wins")
    void keyWinsWithinALayer() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .classpathProperties(dir, Map.of("testlookup.token", FAKE_JWT, "testlookup.api.key", FAKE_KEY))) {
            openAndClose(reporter().build());
        }
        assertEquals(List.of("X-API-Key " + FAKE_KEY), server.sessionCreateAuth);
    }

    @Test
    @DisplayName("a qai_ value passed as the token is sent as X-API-Key")
    void qaiTokenIsSentAsApiKey() throws Exception {
        openAndClose(reporter().token(FAKE_KEY).build());
        assertEquals(List.of("X-API-Key " + FAKE_KEY), server.sessionCreateAuth);
    }

    @Test
    @DisplayName("a testng.xml token beats an API key in testlookup.properties")
    void suiteParamTokenBeatsFileKey() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .classpathProperties(dir, Map.of(
                    "testlookup.url", server.baseUrl(),
                    "testlookup.project", "proj-1",
                    "testlookup.api.key", "qai_REPLACE_ME"))) {
            XmlSuite suite = new XmlSuite();
            suite.setName("Auth suite");
            suite.setParameters(Map.of("testlookup.token", FAKE_JWT));
            XmlTest test = new XmlTest(suite);
            test.setName("auth");
            test.setXmlClasses(List.of(new XmlClass(Sample.class)));
            TestNG testng = new TestNG(false);
            testng.setOutputDirectory(dir.resolve("out").toString());
            testng.setXmlSuites(List.of(suite));
            testng.addListener(new TestLookupListener());
            testng.run();
        }
        assertEquals(List.of("Bearer " + FAKE_JWT), server.sessionCreateAuth);
    }
}
