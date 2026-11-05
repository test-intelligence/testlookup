package io.testlookup.restassured;

import com.fasterxml.jackson.databind.JsonNode;
import io.restassured.RestAssured;
import io.testlookup.FakeTestLookupServer;
import io.testlookup.TestConfigScope;
import io.testlookup.http.ExchangeBuffer;
import io.testlookup.junit5.TestLookupExtension;
import io.testlookup.testng.TestLookupListener;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;
import org.junit.platform.launcher.listeners.SummaryGeneratingListener;
import org.junit.platform.launcher.listeners.TestExecutionSummary;
import org.testng.TestNG;
import org.testng.xml.XmlClass;
import org.testng.xml.XmlSuite;
import org.testng.xml.XmlTest;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.logging.Handler;
import java.util.logging.LogRecord;
import java.util.logging.Logger;

import static io.restassured.RestAssured.given;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.platform.engine.discovery.DiscoverySelectors.selectClass;
import static org.junit.platform.launcher.EngineFilter.includeEngines;

/**
 * The RestAssured filter, driven the way users run it: real RestAssured calls, made by sample test classes
 * that a real Jupiter engine (and a real TestNG run) executes with the SDK's extension / listener. The fake
 * server is both the application under test ({@code /target}) and TestLookup.
 *
 * <p>The sample classes are static nested classes, which Surefire excludes; only the runs in this file
 * execute them.
 */
class TestLookupRestAssuredFilterTest {

    /** Non-hex, so no uuid or hash in a request can contain it by chance. */
    static final String SECRET = "w32bQzSecretKx";

    /** The fake's base URL, read by the sample classes. */
    static volatile String target;

    /** The secret in an Authorization header, an api_key query value and a JSON "password" field. */
    static void postOrder() {
        given().filter(new TestLookupRestAssuredFilter())
            .baseUri(target)
            .header("Authorization", "Bearer " + SECRET)
            .queryParam("api_key", SECRET)
            .contentType("application/json")
            .body("{\"item\":\"book\",\"password\":\"" + SECRET + "\"}")
        .when().post("/target/orders")
        .then().statusCode(201);
    }

    static void get(String path) {
        given().filter(new TestLookupRestAssuredFilter()).baseUri(target)
        .when().get(path)
        .then().statusCode(200);
    }

    @ExtendWith(TestLookupExtension.class)
    static class OrderSample {
        @BeforeAll static void seed() { get("/target/seed"); }
        @BeforeEach void logIn() { get("/target/login"); }
        @AfterEach void logOut() { get("/target/logout"); }
        @Test void createsAnOrder() { postOrder(); }
        @Test void downloadsAnInvoice() { get("/target/invoice.bin"); }
    }

    @ExtendWith(TestLookupExtension.class)
    static class CaptureOffSample {
        @Test void createsAnOrderUncaptured() {
            given().filter(new TestLookupRestAssuredFilter(Map.of("TESTLOOKUP_CAPTURE_HTTP", " Off ")))
                .baseUri(target)
            .when().get("/target/orders")
            .then().statusCode(200);
        }
    }

    @ExtendWith(TestLookupExtension.class)
    static class ManyCallsSample {
        @Test void pollsTwentyOneTimes() {
            for (int i = 1; i <= 21; i++) get("/target/poll/" + i);
        }
    }

    @ExtendWith(TestLookupExtension.class)
    static class OutsideSample {
        @BeforeAll static void before() { get("/target/before-all"); }
        @AfterAll static void after() { get("/target/after-all"); }
        @Test void makesNoCall() { }
    }

    @ExtendWith(TestLookupExtension.class)
    static class PlainSample {
        @Test void createsAnOrderPlainly() { postOrder(); }
    }

    /** A port nothing listens on, set by the test. */
    static volatile int closedPort;
    /** The exception the sample's call threw, as the sample saw it. */
    static volatile Throwable thrown;

    @ExtendWith(TestLookupExtension.class)
    static class RefusedSample {
        @Test void reachesNothing() {
            thrown = org.junit.jupiter.api.Assertions.assertThrows(java.net.ConnectException.class,
                () -> given().filter(new TestLookupRestAssuredFilter())
                    .baseUri("http://127.0.0.1:" + closedPort)
                    .when().get("/down"));
        }
    }

    /** Run only by the TestNG instance below; Surefire runs JUnit here. */
    public static class TestNgSample {
        @org.testng.annotations.BeforeMethod public void logIn() {
            given().baseUri(target).when().get("/target/login").then().statusCode(200);
        }

        @org.testng.annotations.AfterMethod public void logOut() {
            given().baseUri(target).when().get("/target/logout").then().statusCode(200);
        }

        @org.testng.annotations.Test public void createsAnOrderInTestNg() {
            // The filter is registered globally by the test (RestAssured.filters).
            given().baseUri(target)
                .header("Authorization", "Bearer " + SECRET)
                .queryParam("api_key", SECRET)
                .contentType("application/json")
                .body("{\"password\":\"" + SECRET + "\"}")
            .when().post("/target/orders")
            .then().statusCode(201);
        }
    }

    private FakeTestLookupServer server;

    @BeforeEach
    void startServer() throws Exception {
        server = FakeTestLookupServer.http();
        server.targetSecret = SECRET;
        target = server.baseUrl();
    }

    @AfterEach
    void stopServer() {
        server.close();
    }

    private Map<String, String> connection() {
        return Map.of(
            "testlookup.url", server.baseUrl(),
            "testlookup.token", "jwt-test",
            "testlookup.projectId", "proj-1");
    }

    /** One JUnit run of the sample classes, with the SDK configured; returns the run's summary. */
    private TestExecutionSummary run(Map<String, String> props, Class<?>... samples) throws Exception {
        SummaryGeneratingListener summary = new SummaryGeneratingListener();
        LauncherDiscoveryRequestBuilder request = LauncherDiscoveryRequestBuilder.request()
            .filters(includeEngines("junit-jupiter"));
        for (Class<?> sample : samples) request.selectors(selectClass(sample));
        try (TestConfigScope ignored = new TestConfigScope().sysProps(props)) {
            LauncherFactory.create().execute(request.build(), summary);
        }
        return summary.getSummary();
    }

    private TestExecutionSummary run(Class<?>... samples) throws Exception {
        return run(connection(), samples);
    }

    /** The exchanges POST for one test name, or null. */
    private JsonNode postFor(String testName) {
        synchronized (server.httpExchangePosts) {
            for (JsonNode post : server.httpExchangePosts) {
                if (testName.equals(post.path("test_name").asText())) return post;
            }
        }
        return null;
    }

    /** A captured header's value by name, any case (the JDK server sends "Set-cookie"). */
    private static String header(JsonNode message, String name) {
        java.util.Iterator<Map.Entry<String, JsonNode>> fields = message.path("headers").fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> field = fields.next();
            if (field.getKey().equalsIgnoreCase(name)) return field.getValue().asText();
        }
        return null;
    }

    private void assertNoSecretSent() {
        assertFalse(server.rawRequests.isEmpty(), "nothing reached the fake");
        synchronized (server.rawRequests) {
            for (String raw : server.rawRequests) {
                assertFalse(raw.contains(SECRET), "the raw secret was sent to TestLookup:\n" + raw);
            }
        }
    }

    @Test
    @DisplayName("one redacted POST per test, named exactly as the test's result")
    void oneRedactedPostPerTest() throws Exception {
        TestExecutionSummary summary = run(OrderSample.class);
        assertEquals(2, summary.getTestsSucceededCount(), "the filter must not change the calls");
        assertEquals(2, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
        assertNoSecretSent();

        for (String test : List.of("createsAnOrder()", "downloadsAnInvoice()")) {
            JsonNode post = postFor(test);
            JsonNode result = server.result(test);
            assertNotNull(post, test + " has no exchanges POST: " + server.httpExchangePosts);
            assertNotNull(result, server.results().toString());
            assertEquals(result.path("test_name").asText(), post.path("test_name").asText());
            assertEquals(result.path("class_name").asText(), post.path("class_name").asText());
            assertEquals(OrderSample.class.getName(), post.path("class_name").asText());
            assertEquals("s-1-00000000", post.path("session_id").asText());
            assertEquals("r-1", post.path("run_id").asText());
            assertEquals(0, post.path("exchanges_dropped").asInt(-1));
            // The buffer opens right before the test method (the @BeforeEach login is not in it) and closes
            // after the result, which JUnit reports after the @AfterEach methods (the logout is in it).
            assertEquals(2, post.path("exchanges").size(), post.toString());
            assertTrue(post.path("exchanges").get(1).path("url").asText().endsWith("/target/logout"),
                post.toString());
        }
        assertFalse(server.httpExchangePosts.toString().contains("/target/login"));
        assertEquals(List.of("Bearer jwt-test", "Bearer jwt-test"), server.httpExchangeAuth,
            "the reporter's own credential");

        JsonNode order = postFor("createsAnOrder()").path("exchanges").get(0);
        assertEquals("POST", order.path("method").asText());
        assertEquals(201, order.path("status").asInt());
        assertEquals("/target/orders", order.path("endpoint").asText());
        assertTrue(order.path("url").asText().contains("api_key=[REDACTED]"), order.toString());
        assertEquals("[REDACTED]", header(order.path("request"), "Authorization"));
        assertEquals("{\"item\":\"book\",\"password\":\"[REDACTED]\"}", order.path("request").path("body").asText());
        assertEquals("application/json", header(order.path("request"), "Content-Type"));
        assertEquals("[REDACTED]", header(order.path("response"), "Set-Cookie"));
        assertEquals("a, b", header(order.path("response"), "X-Trace"), "a repeated header is one value, joined");
        assertEquals("{\"ok\":true,\"access_token\":\"[REDACTED]\"}", order.path("response").path("body").asText());
        assertEquals(43, order.path("response").path("body_bytes").asInt(), "the size before redaction");
        assertTrue(order.path("duration_ms").asLong(-1) >= 0, order.toString());
        assertTrue(order.path("started_at").asText().matches("\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d\\.\\d{3}Z"),
            order.toString());

        JsonNode invoice = postFor("downloadsAnInvoice()").path("exchanges").get(0);
        assertTrue(invoice.path("url").asText().endsWith("/target/invoice.bin"), invoice.toString());
        assertEquals(200, invoice.path("status").asInt());
        assertTrue(invoice.path("response").path("body").isNull(), "a binary body is not captured: " + invoice);
        assertEquals(0, invoice.path("response").path("body_bytes").asInt(-1));

        // The @BeforeAll call ran, outside any test: never sent.
        assertTrue(server.targetRequests.contains("GET /target/seed"), server.targetRequests.toString());
        assertFalse(server.httpExchangePosts.toString().contains("/target/seed"));
    }

    @Test
    @DisplayName("the TestNG listener sends one redacted POST, named exactly as the test's result")
    void testNgThroughTheListener(@TempDir Path dir) throws Exception {
        RestAssured.filters(new TestLookupRestAssuredFilter());
        try (TestConfigScope ignored = new TestConfigScope().sysProps(connection())) {
            XmlSuite suite = new XmlSuite();
            suite.setName("Orders");
            XmlTest test = new XmlTest(suite);
            test.setName("orders");
            test.setXmlClasses(List.of(new XmlClass(TestNgSample.class)));
            TestNG testng = new TestNG(false);
            testng.setOutputDirectory(dir.resolve("out").toString());
            testng.setXmlSuites(List.of(suite));
            testng.addListener(new TestLookupListener());
            testng.run();
        } finally {
            RestAssured.reset();
        }

        JsonNode result = server.result("createsAnOrderInTestNg");
        assertNotNull(result, server.results().toString());
        assertEquals("PASSED", result.path("status").asText());
        assertEquals(1, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
        assertNoSecretSent();
        JsonNode post = server.httpExchangePosts.get(0);
        assertEquals(result.path("test_name").asText(), post.path("test_name").asText());
        assertEquals(result.path("class_name").asText(), post.path("class_name").asText());
        assertEquals(TestNgSample.class.getName(), post.path("class_name").asText());
        assertEquals("r-1", post.path("run_id").asText());
        // TestNG starts the test after its @BeforeMethod and records the result before its @AfterMethod:
        // only the test method's own call is in the buffer.
        assertEquals(List.of("GET /target/login", "POST /target/orders", "GET /target/logout"),
            server.targetRequests);
        assertEquals(1, post.path("exchanges").size(), post.toString());
        JsonNode order = post.path("exchanges").get(0);
        assertEquals("POST", order.path("method").asText());
        assertEquals("[REDACTED]", header(order.path("request"), "Authorization"));
        assertTrue(order.path("url").asText().contains("api_key=[REDACTED]"), order.toString());
    }

    @Test
    @DisplayName("a falsy TESTLOOKUP_CAPTURE_HTTP turns capture off; the test still runs and reports")
    void aFalsyEnvTurnsCaptureOff() throws Exception {
        TestExecutionSummary summary = run(CaptureOffSample.class);

        assertEquals(1, summary.getTestsSucceededCount());
        assertEquals(List.of("GET /target/orders"), server.targetRequests);
        assertEquals(List.of("createsAnOrderUncaptured():PASSED"), server.results());
        assertEquals(0, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
    }

    @Test
    @DisplayName("-Dtestlookup.capture_http=false turns capture off")
    void theConfigPropertyTurnsCaptureOff() throws Exception {
        Map<String, String> props = new HashMap<>(connection());
        props.put("testlookup.capture_http", "false");
        TestExecutionSummary summary = run(props, PlainSample.class);

        assertEquals(1, summary.getTestsSucceededCount());
        assertEquals(List.of("createsAnOrderPlainly():PASSED"), server.results());
        assertEquals(0, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
    }

    @Test
    @DisplayName("a 21st exchange in one test: 20 sent, exchanges_dropped 1")
    void theTwentyFirstExchangeIsCounted() throws Exception {
        run(ManyCallsSample.class);

        assertEquals(21, server.targetRequests.size());
        assertEquals(1, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
        JsonNode post = server.httpExchangePosts.get(0);
        assertEquals("pollsTwentyOneTimes()", post.path("test_name").asText());
        assertEquals(20, post.path("exchanges").size());
        assertEquals(1, post.path("exchanges_dropped").asInt(-1));
        assertTrue(post.path("exchanges").get(19).path("url").asText().endsWith("/target/poll/20"), post.toString());
    }

    @Test
    @DisplayName("an exchange outside any test is never sent")
    void anExchangeOutsideAnyTestIsNeverSent() throws Exception {
        long droppedBefore = ExchangeBuffer.droppedOutsideTests();
        get("/target/no-test-here"); // this test method runs without the extension
        run(OutsideSample.class);

        assertEquals(List.of("GET /target/no-test-here", "GET /target/before-all", "GET /target/after-all"),
            server.targetRequests);
        assertEquals(List.of("makesNoCall():PASSED"), server.results());
        assertEquals(0, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
        assertTrue(ExchangeBuffer.droppedOutsideTests() - droppedBefore >= 3,
            "dropped outside tests: " + (ExchangeBuffer.droppedOutsideTests() - droppedBefore));
    }

    @Test
    @DisplayName("a call that fails is captured with its exception's class name, which the test still gets")
    void aFailedCallIsCapturedAndRethrown() throws Exception {
        try (java.net.ServerSocket socket = new java.net.ServerSocket(0, 1,
                java.net.InetAddress.getByName("127.0.0.1"))) {
            closedPort = socket.getLocalPort();
        }
        thrown = null;
        TestExecutionSummary summary = run(RefusedSample.class);

        assertEquals(1, summary.getTestsSucceededCount(), "the sample saw a ConnectException");
        assertNotNull(thrown);
        assertEquals(1, server.httpExchangePosts.size(), server.httpExchangePosts.toString());
        JsonNode exchange = server.httpExchangePosts.get(0).path("exchanges").get(0);
        assertEquals(thrown.getClass().getSimpleName(), exchange.path("error").asText(), exchange.toString());
        assertTrue(exchange.path("status").isMissingNode(), exchange.toString());
        assertTrue(exchange.path("url").asText().endsWith("/down"), exchange.toString());
    }

    @Test
    @DisplayName("a refused POST never fails the test and is logged with its status code only")
    void aRefusalIsLoggedWithTheStatusOnly() throws Exception {
        server.httpExchangesStatus = 403;
        List<LogRecord> logged = new ArrayList<>();
        Handler handler = new Handler() {
            @Override public void publish(LogRecord record) { synchronized (logged) { logged.add(record); } }
            @Override public void flush() { }
            @Override public void close() { }
        };
        Logger reporterLog = Logger.getLogger("io.testlookup.TestLookupReporter");
        reporterLog.addHandler(handler);
        TestExecutionSummary summary;
        try {
            summary = run(PlainSample.class);
        } finally {
            reporterLog.removeHandler(handler);
        }

        assertEquals(1, summary.getTestsSucceededCount());
        assertEquals(0, summary.getTestsFailedCount());
        assertEquals(List.of("createsAnOrderPlainly():PASSED"), server.results());
        assertEquals(1, server.httpExchangePosts.size());
        List<String> refusals = new ArrayList<>();
        synchronized (logged) {
            for (LogRecord record : logged) {
                if (record.getMessage().contains("HTTP exchanges")) refusals.add(record.getMessage());
            }
        }
        assertEquals(1, refusals.size(), refusals.toString());
        String line = refusals.get(0);
        assertTrue(line.contains("HTTP 403"), line);
        assertFalse(line.contains(server.baseUrl()) || line.contains("/api/") || line.contains(SECRET)
            || line.contains("jwt-test") || line.contains("{"), line);
    }
}
