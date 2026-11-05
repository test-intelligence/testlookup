package io.testlookup.http;

import io.testlookup.FakeTestLookupServer;
import io.testlookup.TestConfigScope;
import io.testlookup.junit5.TestLookupExtension;
import io.testlookup.testng.TestLookupListener;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;
import org.testng.TestNG;
import org.testng.xml.XmlClass;
import org.testng.xml.XmlSuite;
import org.testng.xml.XmlTest;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.platform.engine.discovery.DiscoverySelectors.selectClass;
import static org.junit.platform.launcher.EngineFilter.includeEngines;

/**
 * A reporting run with the JUnit 5 extension and the TestNG listener, invoked reflectively by
 * {@link SdkLoadsWithoutRestAssuredTest} inside a class loader that has no RestAssured. Not a test class
 * itself (Surefire runs neither it nor its nested samples).
 */
public final class NoRestAssuredRun {

    private NoRestAssuredRun() {
    }

    @ExtendWith(TestLookupExtension.class)
    public static class JUnitSample {
        @Test void reportsWithoutRestAssured() { }
    }

    public static class TestNgSample {
        @org.testng.annotations.Test public void reportsWithoutRestAssuredToo() { }
    }

    /** Run both samples against a fresh fake; the recorded results ("name:STATUS"). */
    public static List<String> run(String testNgOutputDir) throws Exception {
        try (FakeTestLookupServer server = FakeTestLookupServer.http();
             TestConfigScope ignored = new TestConfigScope().sysProps(Map.of(
                 "testlookup.url", server.baseUrl(),
                 "testlookup.token", "jwt-test",
                 "testlookup.projectId", "proj-1"))) {
            LauncherFactory.create().execute(LauncherDiscoveryRequestBuilder.request()
                .selectors(selectClass(JUnitSample.class))
                .filters(includeEngines("junit-jupiter"))
                .build());

            XmlSuite suite = new XmlSuite();
            suite.setName("No RestAssured");
            XmlTest test = new XmlTest(suite);
            test.setName("plain");
            test.setXmlClasses(List.of(new XmlClass(TestNgSample.class)));
            TestNG testng = new TestNG(false);
            testng.setOutputDirectory(testNgOutputDir);
            testng.setXmlSuites(List.of(suite));
            testng.addListener(new TestLookupListener());
            testng.run();

            List<String> results = new ArrayList<>(server.results());
            results.add("http-exchange-posts:" + server.httpExchangePosts.size());
            return results;
        }
    }
}
