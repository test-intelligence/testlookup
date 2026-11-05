package io.testlookup.http;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.File;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * RestAssured is a provided, optional dependency: a user without it must still be able to run the SDK. The
 * reporter, the JUnit 5 extension and the TestNG listener load, initialise and report from a class loader
 * whose classpath is this test run's minus every RestAssured and Groovy jar (parent: the platform loader, so
 * nothing leaks in from the application class loader).
 */
class SdkLoadsWithoutRestAssuredTest {

    private static final List<String> SDK_CLASSES = List.of(
        "io.testlookup.TestLookupReporter",
        "io.testlookup.junit5.TestLookupExtension",
        "io.testlookup.testng.TestLookupListener",
        "io.testlookup.http.ExchangeBuffer");

    /** The test classpath: Surefire's own record of it, else the JVM's. */
    private static List<String> testClassPath() {
        String path = System.getProperty("surefire.test.class.path");
        if (path == null || path.isEmpty()) path = System.getProperty("java.class.path");
        List<String> entries = new ArrayList<>();
        for (String entry : path.split(File.pathSeparator)) {
            if (!entry.isEmpty()) entries.add(entry);
        }
        return entries;
    }

    private static boolean isRestAssuredOrGroovy(String entry) {
        String normalised = entry.replace('\\', '/').toLowerCase(Locale.ROOT);
        String file = normalised.substring(normalised.lastIndexOf('/') + 1);
        return normalised.contains("/io/rest-assured/") || normalised.contains("/org/apache/groovy/")
            || normalised.contains("/org/codehaus/groovy/")
            || file.startsWith("rest-assured") || file.startsWith("groovy")
            || file.startsWith("json-path") || file.startsWith("xml-path");
    }

    @Test
    @DisplayName("the reporter, extension and listener load, initialise and report without RestAssured")
    void theSdkRunsWithoutRestAssured(@TempDir Path dir) throws Exception {
        List<URL> kept = new ArrayList<>();
        List<String> removed = new ArrayList<>();
        for (String entry : testClassPath()) {
            if (isRestAssuredOrGroovy(entry)) {
                removed.add(Paths.get(entry).getFileName().toString());
            } else {
                kept.add(Paths.get(entry).toUri().toURL());
            }
        }
        // Proof the filtering means something: this run's classpath had them.
        assertTrue(removed.stream().anyMatch(name -> name.startsWith("rest-assured-")), removed.toString());
        assertTrue(removed.stream().anyMatch(name -> name.startsWith("groovy-")), removed.toString());

        ClassLoader previous = Thread.currentThread().getContextClassLoader();
        try (URLClassLoader child = new URLClassLoader(kept.toArray(new URL[0]),
                ClassLoader.getPlatformClassLoader())) {
            assertThrows(ClassNotFoundException.class,
                () -> Class.forName("io.restassured.filter.Filter", false, child), "RestAssured is absent");

            for (String name : SDK_CLASSES) {
                Class<?> loaded = Class.forName(name, true, child);
                assertSame(child, loaded.getClassLoader(), name);
                loaded.getDeclaredMethods(); // resolves every type in every signature
                loaded.getDeclaredFields();
            }
            Class.forName("io.testlookup.junit5.TestLookupExtension", true, child)
                .getDeclaredConstructor().newInstance();
            Class.forName("io.testlookup.testng.TestLookupListener", true, child)
                .getDeclaredConstructor().newInstance();

            // ... and only the filter needs it.
            assertThrows(NoClassDefFoundError.class,
                () -> Class.forName("io.testlookup.restassured.TestLookupRestAssuredFilter", true, child));

            // A whole reporting run, both frameworks, inside the RestAssured-free loader.
            Thread.currentThread().setContextClassLoader(child);
            Method run = Class.forName(NoRestAssuredRun.class.getName(), true, child)
                .getMethod("run", String.class);
            @SuppressWarnings("unchecked")
            List<String> results = (List<String>) run.invoke(null, dir.resolve("testng").toString());
            List<String> sorted = new ArrayList<>(results);
            java.util.Collections.sort(sorted);
            assertEquals(List.of("http-exchange-posts:0", "reportsWithoutRestAssured():PASSED",
                "reportsWithoutRestAssuredToo:PASSED"), sorted);
        } finally {
            Thread.currentThread().setContextClassLoader(previous);
        }
    }
}
